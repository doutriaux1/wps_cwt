#! /usr/bin/env python

import cdms2
import cwt
import datetime
import hashlib
import json
import os
import requests
import uuid
from django.conf import settings
from celery.utils import log

from wps import helpers
from wps import models
from wps.tasks import base
from wps.tasks import process
from wps.tasks import file_manager

__ALL__ = [
    'preprocess',
    'ingress',
]

logger = log.get_task_logger('wps.tasks.ingress')

def is_workflow(operations):
    operations = dict((x, cwt.Process.from_dict(y)) for x, y in operations.iteritems())

    # flatten list of operation inputs
    op_inputs = [y for x in operations.values() for y in x.inputs]

    logger.info('Operation inputs %r', op_inputs)

    root_ops = [x for x in operations.keys() if x not in op_inputs]

    if len(root_ops) > 1:
        raise base.WPSError('Invalid workflow, there appears to be %d root operations', len(root_ops))

    try:
        root_op = root_ops[0]
    except IndexError:
        raise base.WPSError('Could not find the root operation')

    logger.info('Root op %r', root_op)

    return root_op, len(operations) > 1, operations

@base.cwt_shared_task()
def preprocess(self, identifier, variables, domains, operations, user_id, job_id):
    self.PUBLISH = base.RETRY | base.FAILURE

    logger.info('Preprocessing job %s user %s', job_id, user_id)
    logger.info('Identifier %r', identifier)
    logger.info('Variables %r', variables)
    logger.info('Domains %r', domains)
    logger.info('Operations %r', operations)

    root_node, workflow, loaded_ops = is_workflow(operations)

    if workflow:
        logger.info('Setting up a workflow pipeline')

        data = {
            'type': 'workflow',
            'identifier': identifier,
            'variables': json.dumps(variables),
            'domains': json.dumps(domains),
            'root_op': root_node,
            'operations': json.dumps(operations),
            'user_id': user_id,
            'job_id': job_id,
        }

        proc = process.Process(self.request.id)

        proc.initialize(user_id, job_id)

        preprocess_data = {}

        for op in loaded_ops.values():
            op_inputs = [cwt.Variable.from_dict(variables[x])
                         for x in op.inputs if x in variables.keys()]

            if len(op_inputs) == 0:
                continue

            try:
                op.domain = cwt.Domain.from_dict(domains[op.domain])
            except KeyError:
                raise WPSError('Missing domain "{name}" definition', name=op.domain)

            with file_manager.DataSetCollection.from_variables(op_inputs) as collection:
                logger.info('INGRESS %r', settings.INGRESS_ENABLED)

                if not proc.check_cache(collection, op.domain) and settings.INGRESS_ENABLED:
                    logger.info('Configuring workflow with ingress')

                    chunk_map = proc.generate_chunk_map(collection, op.domain)

                    preprocess = {
                        'type': 'ingress',
                        'data': json.dumps(chunk_map, default=helpers.json_dumps_default),
                        'estimate_size': collection.estimate_size(op.domain),
                    }
                else:
                    logger.info('Configuring workflow without ingress')

                    domain_map = proc.generate_domain_map(collection)

                    preprocess = {
                        'type': 'domain',
                        'data': json.dumps(domain_map, default=helpers.json_dumps_default),
                        'estimate_size': collection.estimate_size(op.domain),
                    }

            preprocess_data[op.name] = preprocess

        data['preprocess'] = json.dumps(preprocess_data, default=helpers.json_dumps_default)
    else:
        logger.info('Setting up a single process pipeline')

        _, _, o = self.load({}, variables, domains, operations.values()[0])

        data = {
            'identifier': identifier,
            'variables': json.dumps(variables),
            'domains': json.dumps(domains),
            'operation': json.dumps(o.parameterize()),
            'user_id': user_id,
            'job_id': job_id,
        }

        proc = process.Process(self.request.id)

        proc.initialize(user_id, job_id)

        with file_manager.DataSetCollection.from_variables(o.inputs) as collection:
            logger.info('INGRESS %r', settings.INGRESS_ENABLED)

            if not proc.check_cache(collection, o.domain) and settings.INGRESS_ENABLED:
                logger.info('Configuring an ingress pipeline')

                chunk_map = proc.generate_chunk_map(collection, o.domain)

                data['type'] = 'ingress'

                data['chunk_map'] = json.dumps(chunk_map, default=helpers.json_dumps_default)
            else:
                logger.info('Configuring an execute pipeline')

                domain_map = proc.generate_domain_map(collection)

                data['type'] = 'execute'

                data['domain_map'] = json.dumps(domain_map, default=helpers.json_dumps_default)

            data['estimate_size'] = collection.estimate_size(o.domain)

    headers = { }

    logger.info('Executing with %r', data)

    response = requests.post(settings.WPS_EXECUTE_URL, data, headers=headers, verify=False)

    if not response.ok:
        raise base.WPSError('Failed to ingress data status code {code}', code=response.status_code)

    logger.info('Successfuly submitted the execute request')

@base.cwt_shared_task()
def preingress(self, user_id, job_id):
    self.PUBLISH = base.RETRY | base.FAILURE

    proc = process.Process(self.request.id)

    proc.initialize(user_id, job_id)

    proc.job.started()

@base.cwt_shared_task()
def ingress(self, input_url, var_name, domain, base_units, output_uri):
    self.PUBLISH = base.RETRY | base.FAILURE

    logger.info('Ingress "%s" from %s', var_name, input_url)

    domain = json.loads(domain, object_hook=helpers.json_loads_object_hook)

    temporal = domain['temporal']

    spatial = domain['spatial']

    start = datetime.datetime.now()

    try:
        with cdms2.open(input_url) as infile, cdms2.open(output_uri, 'w') as outfile:
            data = infile(var_name, time=temporal, **spatial)

            data.getTime().toRelativeTime(base_units)

            outfile.write(data, id=var_name)
    except cdms2.CDMSError as e:
        raise base.AccessError('', e.message)

    delta = datetime.datetime.now() - start

    stat = os.stat(output_uri)

    variable = cwt.Variable(output_uri, var_name)

    return { 
        'delta': delta.seconds,
        'size': stat.st_size / 1048576.0,
        'variable': variable.parameterize() 
    }

@base.cwt_shared_task()
def ingress_cache(self, ingress_chunks, ingress_map, job_id, output_id, process_id=None):
    self.PUBLISH = base.ALL

    logger.info('Generating cache files from ingressed data')

    collection = file_manager.DataSetCollection()

    elapsed = 0.0
    size = 0.0
    variable_name = None
    variables = []

    if not isinstance(ingress_chunks, list):
        ingress_chunks = [ingress_chunks,]

    for chunk in ingress_chunks:
        elapsed += chunk['delta']

        size += chunk['size']

        variables.append(cwt.Variable.from_dict(chunk['variable']))

    ingress_map = json.loads(ingress_map, object_hook=helpers.json_loads_object_hook)

    output_name = '{}.nc'.format(str(uuid.uuid4()))

    output_path = os.path.join(settings.WPS_LOCAL_OUTPUT_PATH, output_name)

    output_url = settings.WPS_DAP_URL.format(filename=output_name)

    logger.info('Writing output to %s', output_path)

    try:
        with cdms2.open(output_path, 'w') as outfile:
            start = datetime.datetime.now()
    
            for url in sorted(ingress_map.keys()):
                meta = ingress_map[url]

                logger.info('Processing source input %s', url)

                cache = None
                cache_obj = None

                if variable_name is None:
                    variable_name = meta['variable_name']

                dataset = file_manager.DataSet(cwt.Variable(url, variable_name))

                dataset.temporal = meta['temporal']

                dataset.spatial = meta['spatial']

                logger.info('Processing "%s" ingressed chunks of data', len(meta['ingress_chunks']))

                # Try/except to handle closing of cache_obj
                try:
                    for chunk in meta['ingress_chunks']:
                        # Try/except to handle opening of chunk
                        try:
                            with cdms2.open(chunk) as infile:
                                if cache is None:
                                    dataset.file_obj = infile

                                    domain = collection.generate_dataset_domain(dataset)

                                    dimensions = json.dumps(domain, default=models.slice_default)

                                    uid = '{}:{}'.format(dataset.url, dataset.variable_name)

                                    uid_hash = hashlib.sha256(uid).hexdigest()

                                    cache = models.Cache.objects.create(uid=uid_hash, url=dataset.url, dimensions=dimensions)

                                    try:
                                        cache_obj = cdms2.open(cache.local_path, 'w')
                                    except cdms2.CDMSError as e:
                                        raise base.AccessError(cache.local_path, e)

                                try:
                                    data = infile(variable_name)
                                except Exception as e:
                                    raise base.WPSError('Error reading data from {url} error {error}', url=infile.id, error=e)

                                try:
                                    cache_obj.write(data, id=variable_name)
                                except Exception as e:
                                    logger.exception('%r', data)

                                    raise base.WPSError('Error writing data to cache file {url} error {error}', url=cache_obj.id, error=e)

                                try:
                                    outfile.write(data, id=variable_name)
                                except Exception as e:
                                    logger.exception('%r', data)

                                    raise base.WPSError('Error writing data to output file {url} error {error}', url=outfile.id, error=e)
                        except cdms2.CDMSError as e:
                            raise base.AccessError(chunk, e)
                except:
                    raise
                finally:
                    if cache_obj is not None:
                        cache_obj.close()

            delta = datetime.datetime.now() - start

            elapsed += delta.seconds

            stat = os.stat(outfile.id)

            size += (stat.st_size / 1048576.0) 
    except:
        raise
    finally:
        # Clean up the ingressed files
        for url, meta in ingress_map.iteritems():
            for chunk in meta['ingress_chunks']:
                os.remove(chunk)

    if process_id is not None:
        try:
            process = models.Process.objects.get(pk=process_id)
        except models.Process.DoesNotExist:
            raise base.WPSError('Error no process with id "{id}" exists', id=process_id)

        process.update_rate(size, elapsed)

    variable = cwt.Variable(output_url, variable_name)

    return { output_id: variable.parameterize() }
