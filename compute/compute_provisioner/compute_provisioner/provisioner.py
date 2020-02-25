import base64
import datetime
import hashlib
import json
import logging
import os
import threading
import time
from collections import OrderedDict
from uuid import uuid4

import redis
import yaml
import zmq
from kubernetes import client
from kubernetes import config

from compute_provisioner import constants

logger = logging.getLogger('provisioner')

class ResourceAllocationError(Exception):
    pass

class RemoveWorkerError(Exception):
    pass

def json_encoder(x):
    def default(y):
        return {'data': y.decode(constants.ENCODING)}

    return json.dumps(x, default=default)


def json_decoder(x):
    def object_hook(y):
        if 'data' in y:
            return y['data'].encode(constants.ENCODING)

        return y

    return json.loads(x, object_hook=object_hook)

class RedisQueue(object):
    def queue_job(self, version, frames):
        self.redis.rpush(version, json_encoder(frames))

        logger.info('Queued job')

    def requeue_job(self, version, frames):
        self.redis.lpush(version, json_encoder(frames))

        logger.info('Requeued job')

class State(object):
    def on_event(self, **event):
        pass

    def __repr__(self):
        return self.__str__()

    def __str__(self):
        return self.__class__.__name__

class KubernetesAllocator(object):
    def __init__(self):
        config.load_incluster_config()

        self.core = client.CoreV1Api()

        self.apps = client.AppsV1Api()

        self.extensions = client.ExtensionsV1beta1Api()

        self.rbac = client.RbacAuthorizationV1Api()

    def create_namespace(self, body, **kwargs):
        return self.core.create_namespace(body, **kwargs)

    def list_namespace(self, **kwargs):
        return self.core.list_namespace(**kwargs)

    def delete_namespace(self, name, **kwargs):
        return self.core.delete_namespace(name, **kwargs)

    def create_service_account(self, namespace, body, **kwargs):
        return self.core.create_namespaced_service_account(namespace, body, **kwargs)

    def create_role(self, namespace, body, **kwargs):
        return self.rbac.create_namespaced_role(namespace, body, **kwargs)

    def create_role_binding(self, namespace, body, **kwargs):
        return self.rbac.create_namespaced_role_binding(namespace, body, **kwargs)

    def create_resource_quota(self, namespace, body, **kwargs):
        return self.core.create_namespaced_resource_quota(namespace, body, **kwargs)

    def create_pod(self, namespace, body, **kwargs):
        return self.core.create_namespaced_pod(namespace, body, **kwargs)

    def create_deployment(self, namespace, body, **kwargs):
        return self.apps.create_namespaced_deployment(namespace, body, **kwargs)

    def create_service(self, namespace, body, **kwargs):
        return self.core.create_namespaced_service(namespace, body, **kwargs)

    def create_ingress(self, namespace, body, **kwargs):
        return self.extensions.create_namespaced_ingress(namespace, body, **kwargs)

    def create_config_map(self, namespace, body, **kwargs):
        return self.core.create_namespaced_config_map(namespace, body, **kwargs)

    def create_secret(self, namespace, body, **kwargs):
        return self.core.create_namespaced_secret(namespace, body, **kwargs)

class WaitingResourceRequestState(State, RedisQueue):
    def __init__(self, **kwargs):
        self.kwargs = kwargs

        self.redis = kwargs['redis']

        self.lifetime = kwargs['lifetime']

        self.backend = kwargs['backend']

        self.k8s = KubernetesAllocator()

    def set_resource_expiry(self, resource_uuid):
        expired = (datetime.datetime.now() + datetime.timedelta(seconds=self.lifetime)).timestamp()

        self.redis.hset('resource', resource_uuid, expired)

        logger.info(f'Set resource {resource_uuid!r} to expire in {self.lifetime!r} seconds')

    def try_extend_resource_expiry(self, resource_uuid):
        if self.redis.hexists('resource', resource_uuid):
            logger.info(f'Extending resource {resource_uuid!r} expiration')

            self.set_resource_expiry(resource_uuid)

            return True

        return False

    def create_user_environment(self, namespace, labels):
        name = 'compute-user'

        ns = client.V1Namespace()
        ns.metadata = client.V1ObjectMeta(name=namespace, labels=labels)

        self.k8s.create_namespace(ns)

        logger.info(f'Created namespace {namespace!s}')

        image_pull_secrets = []

        root_path = '/etc/imagepullsecrets'

        for x in os.listdir(root_path):
            path = os.path.join(root_path, x, '.dockerconfigjson')

            if not os.path.exists(path):
                continue

            image_pull_secrets.append(client.V1LocalObjectReference(name=x))

            with open(path, 'rb') as f:
                data = base64.b64encode(f.read())

            data = {
                '.dockerconfigjson': data.decode(),
            }

            secret = client.V1Secret(data=data, type='kubernetes.io/dockerconfigjson')
            secret.metadata = client.V1ObjectMeta(name=x, labels=labels)

            self.k8s.create_secret(namespace, secret)

        sa = client.V1ServiceAccount(image_pull_secrets=image_pull_secrets)
        sa.metadata = client.V1ObjectMeta(name=name, labels=labels)

        self.k8s.create_service_account(namespace, sa)

        api_groups = ['', 'apps', 'extensions']
        resources = ['pods', 'deployments', 'services', 'ingresses']
        verbs = ['get', 'list', 'create', 'delete']

        rules = [
            client.V1PolicyRule(api_groups=api_groups, resources=resources, verbs=verbs),
        ]

        role = client.V1Role(rules=rules)
        role.metadata = client.V1ObjectMeta(name=name, labels=labels)

        self.k8s.create_role(namespace, role)

        role_ref = client.V1RoleRef(api_group='rbac.authorization.k8s.io', kind='Role', name=name)
        subjects = [
            client.V1Subject(api_group='', kind='ServiceAccount', name=name, namespace=namespace)
        ]

        role_binding = client.V1RoleBinding(role_ref=role_ref, subjects=subjects)
        role_binding.metadata = client.V1ObjectMeta(name=name, labels=labels)
        role_binding.subjects = [
            client.V1Subject(api_group='', kind='ServiceAccount', name=name, namespace=namespace)
        ]

        self.k8s.create_role_binding(namespace, role_binding)

        spec = client.V1ResourceQuotaSpec()
        spec.hard = {
            'limits.cpu': os.environ.get('USER_LIMIT_CPU', '1'),
            'limits.memory': os.environ.get('USER_LIMIT_MEMORY', '1Gi'),
            'requests.cpu': os.environ.get('USER_REQUEST_CPU', '1'),
            'requests.memory': os.environ.get('USER_REQUEST_MEMORY', '1Gi'),
        }

        rq = client.V1ResourceQuota(spec=spec)
        rq.metadata = client.V1ObjectMeta(name=name, labels=labels)

        self.k8s.create_resource_quota(namespace, rq)

        logger.info(f'Created resource quota')

    def allocate_resources(self, request_raw):
        """ Allocate resources.

        Only support the following resources types: Pod, Deployment, Service, Ingress.

        Args:
            request: A str containing a list of YAML definition of k8s resources.
        """
        resource_uuid = hashlib.sha256(request_raw).hexdigest()[:7]

        labels = {'compute.io/resource-group': resource_uuid}

        namespace = f'compute-{resource_uuid!s}'

        request = json.loads(request_raw)

        logger.info(f'Allocating {len(request)!r} resources with uuid {resource_uuid!r}')

        existing = self.try_extend_resource_expiry(resource_uuid)

        if not existing:
            try:
                self.create_user_environment(namespace, labels)

                for item in request:
                    yaml_data = yaml.safe_load(item)

                    try:
                        yaml_data['metadata']['labels'].update(labels)
                    except KeyError:
                        yaml_data['metadata'].update({
                            'labels': labels
                        })

                    kind = yaml_data['kind']

                    logger.info(f'Allocating {kind!r} with labels {yaml_data["metadata"]["labels"]!r}')

                    if kind == 'Pod':
                        yaml_data['spec']['serviceAccountName'] = 'compute-user'

                        self.k8s.create_pod(namespace, yaml_data)
                    elif kind == 'Deployment':
                        yaml_data['spec']['template']['spec']['serviceAccountName'] = 'compute-user'

                        self.k8s.create_deployment(namespace, yaml_data)
                    elif kind == 'Service':
                        self.k8s.create_service(namespace, yaml_data)
                    elif kind == 'Ingress':
                        self.k8s.create_ingress(namespace, yaml_data)
                    elif kind == 'ConfigMap':
                        self.k8s.create_config_map(namespace, yaml_data)
                    else:
                        raise Exception('Requested an unsupported resource')
            except client.rest.ApiException as e:
                logger.exception(f'Kubernetes API call failed status code {e.status!r}')

                # 403 is Forbidden tends to be raised when namespace is out of resources.
                # 409 is Conflict, generally because the resource already exists.
                if e.status not in (403, 409):
                    raise ResourceAllocationError()

                raise Exception()

            self.set_resource_expiry(resource_uuid)

        extra = {
            'namespace': namespace,
        }

        return extra

    def on_event(self, address, transition, *args):
        logger.info(f'{self!s} transition state {transition!r}')

        if transition == constants.RESOURCE:
            extra = self.allocate_resources(args[0])

            new_frames = [address, constants.ACK, json.dumps(extra).encode()]

            logger.info('Notifying backend %r of successful allocation', address)

            try:
                self.backend.send_multipart(new_frames)
            except zmq.Again:
                logger.error(f'Error notifying backend of successful allocation')

            return None

        return self


class Worker(object):
    def __init__(self, address, version, **kwargs):
        self.address = address
        self.version = version
        self.frames = None
        self.expiry = time.time() + constants.HEARTBEAT_INTERVAL * constants.HEARTBEAT_LIVENESS
        self.state = WaitingResourceRequestState(**kwargs)

    def on_event(self, address, *frames):
        old_state = str(self.state)

        self.state = self.state.on_event(address, *frames)

        logger.info(f'Worker {address!r} transition from {old_state!s} to {self.state!s}')

        if self.state is None:
            raise RemoveWorkerError()

class WorkerQueue(object):
    def __init__(self):
        self.queue = OrderedDict()

        self.heartbeat_at = time.time() + constants.HEARTBEAT_INTERVAL

    def ready(self, worker):
        self.queue.pop(worker.address, None)

        self.queue[worker.address] = worker

        logger.info('Setting worker %r to expire at %r', worker.address, worker.expiry)

    def remove(self, address):
        self.queue.pop(address)

        logger.info(f'Removed worker {address!r}')

    def purge(self):
        t = time.time()

        expired = []

        for address, worker in self.queue.items():
            if t > worker.expiry:
                expired.append(address)

        for address in expired:
            self.queue.pop(address, None)

            logger.info('Idle worker %r expired', address)

    @property
    def heartbeat_time(self):
        return time.time() >= self.heartbeat_at

    def heartbeat(self, socket):
        for address in self.queue:
            try:
                socket.send_multipart([address, constants.HEARTBEAT])

                logger.debug('Send heartbeat to %r', address)
            except zmq.Again:
                logger.error('Error sending heartbaet to %r', address)

        self.heartbeat_at = time.time() + constants.HEARTBEAT_INTERVAL

        logger.debug('Setting next heartbeat at %r', self.heartbeat_at)

    def next(self, version):
        candidates = [x.address for x in self.queue.values() if x.version == version]

        logger.info('Candidates for next worker handling version %r: %r', version, candidates)

        address = candidates.pop(0)

        self.queue.pop(address, None)

        return address


class Provisioner(threading.Thread, RedisQueue):
    def __init__(self, frontend_port, backend_port, redis_host, lifetime, **kwargs):
        super(Provisioner, self).__init__(target=self.monitor, args=(frontend_port, backend_port, redis_host))
        self.lifetime = lifetime

        self.context = None

        self.frontend = None

        self.backend = None

        self.poll_both = None

        self.workers = WorkerQueue()

        self.running = {}

        self.redis = None

    def initialize(self, frontend_port, backend_port, redis_host):
        """ Initializes the load balancer.

        We initialize the zmq context, create the frontend/backend sockets and
        creates their respective pollers.

        Args:
            frontend_port: An int port number used to listen for frontend
                connections.
            backend_port: An int port number used to listen for backend
                connections.
        """
        self.context = zmq.Context(1)

        self.frontend = self.context.socket(zmq.ROUTER)

        FSNDTIMEO = os.environ.get('FRONTEND_SEND_TIMEOUT', 15)
        FRCVTIMEO = os.environ.get('FRONTEND_RECV_TIMEOUT', 15)

        self.frontend.setsockopt(zmq.SNDTIMEO, FSNDTIMEO * 1000)
        self.frontend.setsockopt(zmq.RCVTIMEO, FRCVTIMEO * 1000)

        frontend_addr = 'tcp://*:{!s}'.format(frontend_port)

        self.frontend.bind(frontend_addr)

        logger.info('Binding frontend to %r', frontend_addr)

        self.backend = self.context.socket(zmq.ROUTER)

        BSNDTIMEO = os.environ.get('BACKEND_SEND_TIMEOUT', 15)
        BRCVTIMEO = os.environ.get('BACKEND_RECV_TIMEOUT', 15)

        self.backend.setsockopt(zmq.SNDTIMEO, BSNDTIMEO * 1000)
        self.backend.setsockopt(zmq.RCVTIMEO, BRCVTIMEO * 1000)

        backend_addr = 'tcp://*:{!s}'.format(backend_port)

        self.backend.bind(backend_addr)

        logger.info('Binding backend to %r', backend_addr)

        self.poll_both = zmq.Poller()

        self.poll_both.register(self.frontend, zmq.POLLIN)

        self.poll_both.register(self.backend, zmq.POLLIN)

        self.redis = redis.Redis(host=redis_host, db=1)

        logger.info('Connected to redis db %r', self.redis)

    def handle_frontend_frames(self, frames):
        logger.info('Handling frontend frames %r', frames)

        # Address and blank space
        address = frames[0:2]

        version = frames[2]

        # Frames to be forwarded to the backend
        frames = frames[2:]

        try:
            self.queue_job(version, frames)
        except redis.lock.LockError:
            response = address + [constants.ERR]

            logger.info('Error aquiring redis lock')
        else:
            response = address + [constants.ACK]

            logger.info('Successfully queued job')

        try:
            self.frontend.send_multipart(response)
        except zmq.Again:
            logger.info('Error notifying client with response %r', response)
        else:
            logger.info('Notified client with response %r', response)

    def handle_backend_frames(self, frames):
        address = frames[0]

        logger.info('Handling frames from backend %r', frames[:3])

        if frames[1] in (constants.READY, constants.HEARTBEAT):
            version = frames[2]

            kwargs = {
                'redis': self.redis,
                'namespace': 'default',
                'lifetime': self.lifetime,
                'backend': self.backend,
            }

            self.workers.ready(Worker(address, version, **kwargs))
        else:
            try:
                self.running[address].on_event(*frames)
            except KeyError:
                logger.error(f'Worker {address!r} is not in the running list')
            except RemoveWorkerError:
                self.running.pop(address)
            except ResourceAllocationError:
                self.running.pop(address)

                self.requeue_job(frames[2], frames[3:])

                logger.info(f'Worker {address!r} failed, requeueing job')

    def dispatch_workers(self):
        try:
            with self.redis.lock('job_queue', blocking_timeout=4):
                for address, worker in list(self.workers.queue.items()):
                    logger.info('Processing waiting worker %r', address)

                    try:
                        raw_frames = self.redis.lpop(worker.version)
                    except Exception:
                        # TODO handle edge case of malformed data
                        logger.info('No work found for version %r', worker.version)

                        continue
                    else:
                        if raw_frames is None:
                            continue

                        frames = json_decoder(raw_frames)

                    frames.insert(0, address)

                    frames.insert(1, constants.REQUEST)

                    try:
                        self.backend.send_multipart(frames)
                    except zmq.Again:
                        logger.info('Error sending frames to worker %r', address)

                        self.requeue_job(worker.version, json_enceder(frames[2:]))
                    else:
                        worker.frames = raw_frames

                        self.running[address] = worker

                        self.workers.remove(address)

                        logger.info(f'Moved worker {address!r} to running')
        except redis.lock.LockError:
            # No need to handle anything if we fail to aquire the lock
            # TODO we should track how often we cannot aquire the lock, as there may be a bigger issue
            pass

    def monitor(self, frontend_port, backend_port, redis_host):
        """ Main loop for the load balancer.
        """
        self.initialize(frontend_port, backend_port, redis_host)

        while True:
            socks = dict(self.poll_both.poll(constants.HEARTBEAT_INTERVAL * 1000))

            if socks.get(self.backend) == zmq.POLLIN:
                try:
                    frames = self.backend.recv_multipart()
                except zmq.Again:
                    logger.info('Error receiving frames from backend')
                else:
                    if not frames:
                        break

                    self.handle_backend_frames(frames)

            if socks.get(self.frontend) == zmq.POLLIN:
                try:
                    frames = self.frontend.recv_multipart()
                except zmq.Again:
                    logger.info('Error receiving frames from frontend')
                else:
                    if not frames:
                        break

                    self.handle_frontend_frames(frames)

            if self.workers.heartbeat_time:
                self.workers.heartbeat(self.backend)

            self.workers.purge()

            self.dispatch_workers()


def main():
    import argparse

    parser = argparse.ArgumentParser()

    parser.add_argument('--log-level', help='Logging level', choices=logging._nameToLevel.keys(), default='INFO')

    parser.add_argument('--redis-host', help='Redis host', required=True)

    parser.add_argument('--frontend-port', help='Frontend port', type=int, default=7777)

    parser.add_argument('--backend-port', help='Backend port', type=int, default=7778)

    parser.add_argument('--lifetime', help='Life time of resources in seconds', type=int, default=120)

    args = parser.parse_args()

    logging.basicConfig(level=args.log_level)

    provisioner = Provisioner(**vars(args))

    provisioner.start()

    provisioner.join()


if __name__ == '__main__':
    main()
