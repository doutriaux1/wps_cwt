import builtins
import json
import re
import os

import cdms2
import cwt
import dask
import pytest
import pandas as pd
import numpy as np
import dask.array as da
import xarray as xr
from OpenSSL import SSL
from distributed.utils_test import (  # noqa: F401
    client,
    loop,
    cluster_fixture,
)
from jinja2 import Environment, BaseLoader
from myproxy.client import MyProxyClient

from compute_tasks import base
from compute_tasks import cdat
from compute_tasks import WPSError
from compute_tasks.context import operation


MyProxyClient.SSL_METHOD = SSL.TLSv1_2_METHOD


class TestDataGenerator(object):
    def standard(self, value, lat=90, lon=180, time=10, name=None):
        if name is None:
            name = 'pr'

        data_vars = {
            name: (['time', 'lat', 'lon'], np.full((time, lat, lon), value)),
        }

        coords = {
            'time': pd.date_range('1990-01-01', periods=time),
            'lat': (['lat'], np.arange(-90, 90, 180/lat)),
            'lon': (['lon'], np.arange(0, 360, 360/lon)),
        }

        return xr.Dataset(data_vars, coords)

    def increasing_lat(self, lat=90, lon=180, time=10, name=None):
        if name is None:
            name = 'pr'

        data = np.array([np.array([np.full((lon), x) for x in range(lat)]) for _ in range(time)])

        data_vars = {
            name: (['time', 'lat', 'lon'], data),
        }

        coords = {
            'time': pd.date_range('1990-01-01', periods=time),
            'lat': (['lat'], np.arange(-90, 90, 180/lat)),
            'lon': (['lon'], np.arange(0, 360, 360/lon)),
        }

        return xr.Dataset(data_vars, coords)

@pytest.fixture
def test_data():
    return TestDataGenerator()


def test_groupby_bins_invalid_bin(test_data, mocker):
    identifier = 'CDAT.groupby_bins'

    v1 = test_data.increasing_lat(name='pr')

    inputs = [v1]

    p = cwt.Process(identifier)
    p.add_inputs(cwt.Variable('test.nc', 'pr'))
    p.add_parameters(variable='tas', bins=[str(x) for x in range(0, 90, 10)]+['abcd'])

    data_inputs = {
        'variable': json.dumps([x.to_dict() for x in p.inputs]),
        'domain': json.dumps([]),
        'operation': json.dumps([p.to_dict()]),
    }

    context = operation.OperationContext.from_data_inputs(identifier, data_inputs)

    mocker.patch.object(context, 'message')

    with pytest.raises(WPSError):
        cdat.PROCESS_FUNC_MAP[identifier](context, p, *inputs)


def test_groupby_bins_missing_variable(test_data, mocker):
    identifier = 'CDAT.groupby_bins'

    v1 = test_data.increasing_lat(name='pr')

    inputs = [v1]

    p = cwt.Process(identifier)
    p.add_inputs(cwt.Variable('test.nc', 'pr'))
    p.add_parameters(variable='tas', bins=[str(x) for x in range(0, 90, 10)])

    data_inputs = {
        'variable': json.dumps([x.to_dict() for x in p.inputs]),
        'domain': json.dumps([]),
        'operation': json.dumps([p.to_dict()]),
    }

    context = operation.OperationContext.from_data_inputs(identifier, data_inputs)

    mocker.patch.object(context, 'message')

    with pytest.raises(WPSError):
        cdat.PROCESS_FUNC_MAP[identifier](context, p, *inputs)


def test_groupby_bins(test_data, mocker):
    identifier = 'CDAT.groupby_bins'

    v1 = test_data.increasing_lat(name='pr')

    inputs = [v1]

    p = cwt.Process(identifier)
    p.add_inputs(cwt.Variable('test.nc', 'pr'))
    p.add_parameters(variable='pr', bins=[str(x) for x in range(0, 90, 10)])

    data_inputs = {
        'variable': json.dumps([x.to_dict() for x in p.inputs]),
        'domain': json.dumps([]),
        'operation': json.dumps([p.to_dict()]),
    }

    context = operation.OperationContext.from_data_inputs(identifier, data_inputs)

    mocker.patch.object(context, 'message')

    output = cdat.PROCESS_FUNC_MAP[identifier](context, p, *inputs)

    assert len(output) == 8


def test_where_fillna_cannot_convert(test_data, mocker):
    cond = 'pr>1'
    identifier = 'CDAT.where'

    v1 = test_data.standard(1, name='pr')

    inputs = [v1]

    p = cwt.Process(identifier)
    p.add_inputs(cwt.Variable('test.nc', 'pr'))
    p.add_parameters(cond=cond, fillna='asd')

    data_inputs = {
        'variable': json.dumps([x.to_dict() for x in p.inputs]),
        'domain': json.dumps([]),
        'operation': json.dumps([p.to_dict()]),
    }

    context = operation.OperationContext.from_data_inputs(identifier, data_inputs)

    mocker.patch.object(context, 'message')

    with pytest.raises(WPSError):
        cdat.PROCESS_FUNC_MAP[identifier](context, p, *inputs)


def test_where_fillna(test_data, mocker):
    cond = 'pr>1'
    identifier = 'CDAT.where'

    v1 = test_data.standard(1, name='pr')

    inputs = [v1]

    p = cwt.Process(identifier)
    p.add_inputs(cwt.Variable('test.nc', 'pr'))
    p.add_parameters(cond=cond, fillna='1e22')

    data_inputs = {
        'variable': json.dumps([x.to_dict() for x in p.inputs]),
        'domain': json.dumps([]),
        'operation': json.dumps([p.to_dict()]),
    }

    context = operation.OperationContext.from_data_inputs(identifier, data_inputs)

    mocker.patch.object(context, 'message')

    output = cdat.PROCESS_FUNC_MAP[identifier](context, p, *inputs)

    expected = test_data.standard(1e22, name='pr')

    assert np.array_equal(output.pr.values, expected.pr.values)


def test_where_unsupported_comp(test_data, mocker):
    cond = 'tas>>45'
    identifier = 'CDAT.where'

    v1 = test_data.standard(1, name='pr')

    inputs = [v1]

    p = cwt.Process(identifier)
    p.add_inputs(cwt.Variable('test.nc', 'pr'))
    p.add_parameters(cond=cond)

    data_inputs = {
        'variable': json.dumps([x.to_dict() for x in p.inputs]),
        'domain': json.dumps([]),
        'operation': json.dumps([p.to_dict()]),
    }

    context = operation.OperationContext.from_data_inputs(identifier, data_inputs)

    mocker.patch.object(context, 'message')

    with pytest.raises(WPSError):
        cdat.PROCESS_FUNC_MAP[identifier](context, p, *inputs)


def test_where_missing_var(test_data, mocker):
    cond = 'tas>-45'
    identifier = 'CDAT.where'

    v1 = test_data.standard(1, name='pr')

    inputs = [v1]

    p = cwt.Process(identifier)
    p.add_inputs(cwt.Variable('test.nc', 'pr'))
    p.add_parameters(cond=cond)

    data_inputs = {
        'variable': json.dumps([x.to_dict() for x in p.inputs]),
        'domain': json.dumps([]),
        'operation': json.dumps([p.to_dict()]),
    }

    context = operation.OperationContext.from_data_inputs(identifier, data_inputs)

    mocker.patch.object(context, 'message')

    with pytest.raises(WPSError):
        cdat.PROCESS_FUNC_MAP[identifier](context, p, *inputs)


def test_where_bad_cond(test_data, mocker):
    cond = 'pr>--45'
    identifier = 'CDAT.where'

    v1 = test_data.standard(1, name='pr')

    inputs = [v1]

    p = cwt.Process(identifier)
    p.add_inputs(cwt.Variable('test.nc', 'pr'))
    p.add_parameters(cond=cond)

    data_inputs = {
        'variable': json.dumps([x.to_dict() for x in p.inputs]),
        'domain': json.dumps([]),
        'operation': json.dumps([p.to_dict()]),
    }

    context = operation.OperationContext.from_data_inputs(identifier, data_inputs)

    mocker.patch.object(context, 'message')

    with pytest.raises(WPSError):
        cdat.PROCESS_FUNC_MAP[identifier](context, p, *inputs)


def test_where_missing_cond(test_data, mocker):
    identifier = 'CDAT.where'

    v1 = test_data.standard(1, name='pr')

    inputs = [v1]

    p = cwt.Process(identifier)
    p.add_inputs(cwt.Variable('test.nc', 'pr'))

    data_inputs = {
        'variable': json.dumps([x.to_dict() for x in p.inputs]),
        'domain': json.dumps([]),
        'operation': json.dumps([p.to_dict()]),
    }

    context = operation.OperationContext.from_data_inputs(identifier, data_inputs)

    mocker.patch.object(context, 'message')

    with pytest.raises(WPSError):
        cdat.PROCESS_FUNC_MAP[identifier](context, p, *inputs)


@pytest.mark.parametrize('cond', [
    'lat>-45',
    'lat>45',
    'lon>360',
    'lon>=360',
    'lon<180',
    'lon<=180',
    'lon==180',
    'lon!=180',
])
def test_where(test_data, cond, mocker):
    identifier = 'CDAT.where'

    v1 = test_data.standard(1, name='pr')

    inputs = [v1]

    p = cwt.Process(identifier)
    p.add_inputs(cwt.Variable('test.nc', 'pr'))
    p.add_parameters(cond=cond)

    data_inputs = {
        'variable': json.dumps([x.to_dict() for x in p.inputs]),
        'domain': json.dumps([]),
        'operation': json.dumps([p.to_dict()]),
    }

    context = operation.OperationContext.from_data_inputs(identifier, data_inputs)

    mocker.patch.object(context, 'message')

    result = cdat.PROCESS_FUNC_MAP[identifier](context, p, *inputs)

    assert 'pr' in result


def test_abstracts():
    for x in cdat.PROCESS_FUNC_MAP.keys():
        assert x in cdat.ABSTRACT_MAP


def test_merge(test_data, mocker):
    identifier = 'CDAT.merge'

    v1 = test_data.standard(1, name='pr')
    v2 = test_data.standard(1, name='prw')

    inputs = [v1, v2]

    p = cwt.Process(identifier)
    p.add_inputs(cwt.Variable('test.nc', 'pr'), cwt.Variable('test.nc', 'prw'))

    data_inputs = {
        'variable': json.dumps([x.to_dict() for x in p.inputs]),
        'domain': json.dumps([]),
        'operation': json.dumps([p.to_dict()]),
    }

    context = operation.OperationContext.from_data_inputs(identifier, data_inputs)

    mocker.patch.object(context, 'message')

    result = cdat.PROCESS_FUNC_MAP[identifier](context, p, *inputs)

    assert 'pr' in result
    assert 'prw' in result


@pytest.mark.parametrize('identifier,v1,v2,output,extra', [
    ('CDAT.abs', -2, None, 2, {}),
    ('CDAT.add', 1, 2, 3, {}),
    ('CDAT.add', 1, None, 3, {'const': '2'}),
    ('CDAT.divide', 1, 2, 0.5, {}),
    ('CDAT.divide', 1, None, 0.5, {'const': '2'}),
    ('CDAT.exp', 1, None, 2.718281828459045, {}),
    ('CDAT.log', 5, None, 1.6094379124341003, {}),
    ('CDAT.multiply', 2, 3, 6, {}),
    ('CDAT.multiply', 2, None, 6, {'const': '3'}),
    ('CDAT.power', 2, 2, 4, {}),
    ('CDAT.power', 2, None, 4, {'const': '2'}),
    ('CDAT.subtract', 2, 1, 1, {}),
    ('CDAT.subtract', 2, None, 3, {'const': '-1'}),
    ('CDAT.count', 2, None, np.array(162000), {}),
    ('CDAT.mean', 2, None, np.full((90, 180), 2), {'axes': ['time']}),
    ('CDAT.std', 5, None, np.full((90, 180), 0), {'axes': ['time']}),
    ('CDAT.var', 5, None, np.full((90, 180), 0), {'axes': ['time']}),
])
def test_processing(test_data, identifier, v1, v2, output, extra):
    inputs = [test_data.standard(v1)]

    if v2 is not None:
        inputs.append(test_data.standard(v2))

    p = cwt.Process(identifier)
    p.add_parameters(**extra)

    vars = [cwt.Variable('test{!s}.nc'.format(str(x)), 'pr') for x, _ in enumerate(inputs)]

    data_inputs = {
        'variable': json.dumps([x.to_dict() for x in vars]),
        'domain': json.dumps([]),
        'operation': json.dumps([p.to_dict()]),
    }

    context = operation.OperationContext.from_data_inputs(identifier, data_inputs)

    result = cdat.PROCESS_FUNC_MAP[identifier](context, p, *inputs)

    if isinstance(output, np.ndarray):
        assert np.array_equal(result.pr.values, output)
    else:
        expected = test_data.standard(output)

        assert np.array_equal(result.pr.values, expected.pr.values)


@pytest.mark.data
def test_subset(mocker, esgf_data, client):  # noqa: F811
    mocker.patch.dict(os.environ, {
        'DATA_PATH': '/',
    })

    self = mocker.MagicMock()

    v1 = cwt.Variable(esgf_data.data['tas-opendap']['files'][0], 'tas')

    subset = cwt.Process('CDAT.subset')
    subset.set_domain(cwt.Domain(time=(674885.0, 674911.0)))
    subset.add_inputs(v1)

    data_inputs = {
        'variable': json.dumps([v1.to_dict()]),
        'domain': json.dumps([subset.domain.to_dict()]),
        'operation': json.dumps([subset.to_dict()]),
    }

    ctx = operation.OperationContext.from_data_inputs('CDAT.subset', data_inputs)
    ctx.extra['DASK_SCHEDULER'] = client.scheduler.address
    ctx.user = 0
    ctx.job = 0

    print(ctx.gparameters)

    mocker.patch.object(ctx, 'message')
    mocker.patch.object(ctx, 'action')

    new_ctx = cdat.process_wrapper(self, ctx)

    assert new_ctx

    with cdms2.open(new_ctx.output[0].uri) as infile:
        var_name = new_ctx.output[0].var_name

        assert infile[var_name].shape == (27, 192, 288)


def test_subset_input_value_spatial(mocker, esgf_data):
    context = operation.OperationContext()
    context._variable = {'tas': cwt.Variable('', 'tas')}

    process = cwt.Process('CDAT.subset')
    process.set_domain(cwt.Domain(lat=(-45, 45)))

    input = esgf_data.to_xarray('tas-opendap')

    new_input = cdat.subset_input(context, process, input)

    assert list(new_input.dims.values()) == [96, 288, 2, 3650]


def test_subset_input_value_timestamps(mocker, esgf_data):
    context = operation.OperationContext()
    context._variable = {'tas': cwt.Variable('', 'tas')}

    process = cwt.Process('CDAT.subset')
    process.set_domain(cwt.Domain(time=('1850', '1851')))

    input = esgf_data.to_xarray('tas-opendap')

    new_input = cdat.subset_input(context, process, input)

    assert list(new_input.dims.values()) == [192, 288, 2, 730]


def test_subset_input_value_time(mocker, esgf_data):
    context = operation.OperationContext()
    context._variable = {'tas': cwt.Variable('', 'tas')}

    process = cwt.Process('CDAT.subset')
    process.set_domain(cwt.Domain(time=(674885., 674985.)))

    input = esgf_data.to_xarray('tas-opendap')

    new_input = cdat.subset_input(context, process, input)

    assert list(new_input.dims.values()) == [192, 288, 2, 101]


def test_subset_input_indices(mocker, esgf_data):
    context = operation.OperationContext()

    process = cwt.Process('CDAT.subset')
    process.set_domain(cwt.Domain(time=slice(0, 10)))

    input = esgf_data.to_xarray('tas-opendap')

    new_input = cdat.subset_input(context, process, input)

    assert list(new_input.dims.values()) == [192, 288, 2, 10]


@pytest.mark.myproxyclient
def test_open_dataset(mocker, client):
    url = 'http://crd-esgf-drc.ec.gc.ca/thredds/dodsC/esg_dataroot/AR5/CMIP5/output/CCCma/CanAM4/amip/3hr/atmos/clt/r1i1p1/clt_cf3hr_CanAM4_amip_r1i1p1_197901010300-201001010000.nc'

    context = operation.OperationContext()

    m = MyProxyClient(hostname=os.environ['MPC_HOST'])

    cert = m.logon(os.environ['MPC_USERNAME'], os.environ['MPC_PASSWORD'], bootstrap=True)

    mocker.patch.object(context, 'user_cert', return_value=''.join([x.decode() for x in cert]))

    ds = cdat.open_dataset(context, url, 'clt', chunks={'time': 100})

    assert ds
    assert ds.clt.shape == (90520, 64, 128)
    assert 'lat_bnds' in ds
    assert 'lon_bnds' in ds
    assert 'time_bnds' in ds

# 'http://crd-esgf-drc.ec.gc.ca/thredds/dodsC/esg_dataroot/AR5/CMIP5/output/CCCma/CanAM4/amip/3hr/atmos/clt/r1i1p1/clt_cf3hr_CanAM4_amip_r1i1p1_197901010300-201001010000.nc',
# 'http://crd-esgf-drc.ec.gc.ca/thredds/dodsC/esgA_dataroot/AR6/CMIP6/CMIP/CCCma/CanESM5/amip/r2i1p1f1/day/clt/gn/v20190429/clt_day_CanESM5_amip_r2i1p1f1_gn_19500101-20141231.nc',


def test_execute_delayed_with_client(mocker):
    mocker.patch.object(dask, 'compute')
    mocker.patch.object(cdat, 'DaskJobTracker')

    context = mocker.MagicMock()

    client = mocker.MagicMock()  # noqa: F811

    futures = []

    cdat.execute_delayed(context, futures, client)

    dask.compute.assert_not_called()

    client.compute.assert_called_with(futures)

    cdat.DaskJobTracker.assert_called_with(context, client.compute.return_value)


def test_execute_delayed(mocker):
    mocker.patch.object(dask, 'compute')

    context = mocker.MagicMock()

    futures = []

    cdat.execute_delayed(context, futures)

    dask.compute.assert_called_with(futures)


def test_get_user_cert(mocker):
    context = mocker.MagicMock()
    context.user_cert.return_value = 'client cert data'

    tf = cdat.get_user_cert(context)

    assert tf


def test_check_access_exception(esgf_data):
    with pytest.raises(WPSError):
        cdat.check_access('https://esgf-node.llnl.gov/sjdlasjdla')


def test_check_access_request_exception(esgf_data):
    with pytest.raises(WPSError):
        cdat.check_access('https://ajsdklajskdja')


@pytest.mark.parametrize('status_code', [
    (401),
    (403),
])
def test_check_access_protected(mocker, esgf_data, status_code):
    requests = mocker.patch.object(cdat, 'requests')

    requests.get.return_value.status_code = status_code

    assert not cdat.check_access(esgf_data.data['tas-opendap-cmip5']['files'][0])


def test_check_access(esgf_data):
    assert cdat.check_access(esgf_data.data['tas-opendap']['files'][0])


def test_clean_variable_encoding(mocker, esgf_data):
    ds = esgf_data.to_xarray('tas-opendap')

    cdat.clean_variable_encoding(ds)

    assert 'missing_value' not in ds.tas.encoding


def test_dask_job_tracker(mocker, client):  # noqa: F811
    context = mocker.MagicMock()

    data = da.random.random((100, 100), chunks=(10, 10))

    fut = client.compute(data)

    cdat.DaskJobTracker(context, fut)

    assert context.message.call_count > 0


def test_gather_workflow_outputs_bad_coord(mocker):
    context = mocker.MagicMock()

    subset = cwt.Process(identifier='CDAT.subset')
    subset_delayed = mocker.MagicMock()
    subset_delayed.to_netcdf.side_effect = ValueError

    interm = {
        subset.name: subset_delayed,
    }

    with pytest.raises(WPSError):
        cdat.gather_workflow_outputs(context, interm, [subset])


def test_gather_workflow_outputs_missing_interm(mocker):
    context = mocker.MagicMock()

    subset = cwt.Process(identifier='CDAT.subset')
    subset_delayed = mocker.MagicMock()

    max = cwt.Process(identifier='CDAT.max')

    interm = {
        subset.name: subset_delayed,
    }

    with pytest.raises(WPSError):
        cdat.gather_workflow_outputs(context, interm, [subset, max])


def test_gather_workflow_outputs(mocker):
    context = mocker.MagicMock()

    subset = cwt.Process(identifier='CDAT.subset')
    subset_delayed = mocker.MagicMock()

    max = cwt.Process(identifier='CDAT.max')
    max_delayed = mocker.MagicMock()

    interm = {
        subset.name: subset_delayed,
        max.name: max_delayed,
    }

    delayed = cdat.gather_workflow_outputs(context, interm, [subset, max])

    assert len(delayed) == 2
    assert len(interm) == 0


def test_gather_inputs_aggregate(mocker, esgf_data):
    process = cwt.Process('CDAT.aggregate')
    process.add_inputs(*[cwt.Variable(x, 'tas') for x in esgf_data.data['tas-opendap']['files']])

    context = mocker.MagicMock()

    data = cdat.gather_inputs(context, process)

    assert len(data) == 1


def test_gather_inputs_exception(mocker, esgf_data):
    tempfile_mock = mocker.MagicMock()

    files = esgf_data.data['tas-opendap-cmip5']['files']

    process = cwt.Process('CDAT.subset')
    process.add_inputs(*[cwt.Variable(x, 'tas') for x in files])

    context = mocker.MagicMock()
    context.user_cert.return_value = 'cert data'

    data = cdat.gather_inputs(context, process)

    assert len(data) == 2


def test_gather_inputs(mocker, esgf_data):
    process = cwt.Process('CDAT.subset')
    process.add_inputs(*[cwt.Variable(x, 'tas') for x in esgf_data.data['tas-opendap']['files']])

    context = mocker.MagicMock()

    data = cdat.gather_inputs(context, process)

    assert len(data) == 2


def test_build_workflow_missing_interm(mocker):
    process_func = mocker.MagicMock()
    mocker.patch.object(cdat, 'gather_inputs')
    mocker.patch.dict(cdat.PROCESS_FUNC_MAP, {
        'CDAT.subset': process_func,
    }, True)

    subset = cwt.Process(identifier='CDAT.subset')

    mocker.patch.object(subset, 'copy')

    subset2 = cwt.Process(identifier='CDAT.subset')

    max = cwt.Process(identifier='CDAT.max')
    max.add_inputs(subset, subset2)

    context = mocker.MagicMock()
    context.topo_sort.return_value = [subset, max]

    with pytest.raises(WPSError):
        cdat.build_workflow(context)


def test_build_workflow_use_interm(mocker):
    mocker.patch.object(cdat, 'gather_inputs')
    mocker.patch.dict(cdat.PROCESS_FUNC_MAP, {
        'CDAT.subset': mocker.MagicMock(),
        'CDAT.max': mocker.MagicMock(),
    }, True)

    subset = cwt.Process(identifier='CDAT.subset')

    mocker.patch.object(subset, 'copy')

    max = cwt.Process(identifier='CDAT.max')
    max.add_inputs(subset)

    context = mocker.MagicMock()
    context.topo_sort.return_value = [subset, max]

    interm = cdat.build_workflow(context)

    cdat.gather_inputs.assert_called_with(context, subset)

    assert len(interm) == 2
    assert subset.name in interm
    assert max.name in interm


def test_build_workflow_process_func(mocker):
    process_func = mocker.MagicMock()
    mocker.patch.object(cdat, 'gather_inputs')
    mocker.patch.dict(cdat.PROCESS_FUNC_MAP, {
        'CDAT.subset': process_func,
    }, True)

    subset = cwt.Process(identifier='CDAT.subset')

    context = mocker.MagicMock()
    context.topo_sort.return_value = [subset]

    interm = cdat.build_workflow(context)

    cdat.gather_inputs.assert_called_with(context, subset)

    assert len(interm) == 1
    assert subset.name in interm


def test_build_workflow(mocker):
    mocker.patch.object(cdat, 'gather_inputs')
    mocker.patch.dict(cdat.PROCESS_FUNC_MAP, {
        'CDAT.subset': mocker.MagicMock(),
    }, True)

    subset = cwt.Process(identifier='CDAT.subset')

    context = mocker.MagicMock()
    context.topo_sort.return_value = [subset]

    interm = cdat.build_workflow(context)

    cdat.gather_inputs.assert_called_with(context, subset)

    assert len(interm) == 1
    assert subset.name in interm


@pytest.mark.skip(reason='Unconfigurable exponential timeout')
def test_workflow_func_dask_cluster_error(mocker):
    mocker.patch.object(cdat, 'gather_workflow_outputs')
    mocker.patch.object(cdat, 'Client')
    mocker.patch.object(cdat, 'build_workflow')
    mocker.patch.object(cdat, 'DaskJobTracker')

    context = mocker.MagicMock()
    context.extra = {
        'DASK_SCHEDULER': 'dask-scheduler',
    }

    cdat.Client.side_effect = OSError()

    cdat.workflow_func(context)

    cdat.build_workflow.assert_called()

    cdat.Client.assert_called_with('dask-scheduler.default.svc:8786')


def test_workflow_func_error(mocker):
    mocker.patch.object(cdat, 'gather_workflow_outputs')
    mocker.patch.object(cdat, 'Client')
    mocker.patch.object(cdat, 'build_workflow')
    mocker.patch.object(cdat, 'DaskJobTracker')
    mocker.patch.object(cdat, 'execute_delayed')

    context = mocker.MagicMock()
    context.extra = {
        'DASK_SCHEDULER': 'dask-scheduler',
    }

    cdat.execute_delayed.side_effect = WPSError

    with pytest.raises(WPSError):
        cdat.workflow_func(context)

    cdat.build_workflow.assert_called()

    cdat.Client.assert_called_with('dask-scheduler')

    cdat.gather_workflow_outputs.assert_any_call(context, cdat.build_workflow.return_value,
                                                 context.output_ops.return_value)

    cdat.Client.return_value.compute.assert_not_called()


def test_workflow_func_execute_error(mocker):
    mocker.patch.object(cdat, 'gather_workflow_outputs')
    mocker.patch.object(cdat, 'Client')
    mocker.patch.object(cdat, 'build_workflow')
    mocker.patch.object(cdat, 'DaskJobTracker')

    context = mocker.MagicMock()
    context.extra = {
        'DASK_SCHEDULER': 'dask-scheduler',
    }

    cdat.Client.return_value.compute.side_effect = Exception()

    with pytest.raises(WPSError):
        cdat.workflow_func(context)

    cdat.build_workflow.assert_called()

    cdat.Client.assert_called_with('dask-scheduler')

    cdat.gather_workflow_outputs.assert_any_call(context, cdat.build_workflow.return_value,
                                                 context.output_ops.return_value)

    cdat.Client.return_value.compute.assert_called()


def test_workflow_func_no_client(mocker):
    mocker.patch.object(cdat, 'gather_workflow_outputs')
    mocker.patch.object(cdat, 'Client')
    mocker.patch.object(cdat, 'build_workflow')
    mocker.patch.object(cdat, 'DaskJobTracker')

    context = mocker.MagicMock()
    context.extra = {}

    cdat.workflow_func(context)

    cdat.build_workflow.assert_called()

    cdat.Client.assert_not_called()

    cdat.gather_workflow_outputs.assert_any_call(context, cdat.build_workflow.return_value,
                                                 context.output_ops.return_value)

    cdat.Client.return_value.compute.assert_not_called()


def test_workflow_func(mocker):
    mocker.patch.object(cdat, 'gather_workflow_outputs')
    mocker.patch.object(cdat, 'Client')
    mocker.patch.object(cdat, 'build_workflow')
    mocker.patch.object(cdat, 'DaskJobTracker')

    context = mocker.MagicMock()
    context.extra = {
        'DASK_SCHEDULER': 'dask-scheduler',
    }

    cdat.workflow_func(context)

    cdat.build_workflow.assert_called()

    cdat.Client.assert_called_with('dask-scheduler')

    cdat.gather_workflow_outputs.assert_any_call(context, cdat.build_workflow.return_value,
                                                 context.output_ops.return_value)

    cdat.Client.return_value.compute.assert_called()


def test_process_wrapper(mocker):
    mocker.patch.object(cdat, 'workflow_func')

    context = mocker.MagicMock()

    self = mocker.MagicMock()

    cdat.process_wrapper(self, context)

    cdat.workflow_func.assert_called_with(context)


SINGLE_ABS = """Test description.

Accepts exactly 1 input.
"""

MIN_MAX_ABS = """Test description.

Accepts 2 to 4 inputs.
"""

INF_ABS = """Test description.

Accepts a minimum of 1 inputs.
"""

MULTI_PARAM_ABS = """Test description.

Accepts exactly 1 input.

Parameters:
    axes: A list of axes to reduce dimensionality over. Separate multiple values with "|" e.g. time|lat.
    bins: A list of bins. Separate values with "|" e.g. 0|10|20, this would create 2 bins (0-10), (10, 20).
"""

@pytest.mark.parametrize('d,min_inputs,max_inputs,params,expected', [
    ('Test description.', None, None, {}, SINGLE_ABS),
    ('Test description.', 2, 4, {}, MIN_MAX_ABS),
    ('Test description.', None, float('inf'), {}, INF_ABS),
    ('Test description.', None, None, {'axes': cdat.AXES, 'bins': cdat.BINS}, MULTI_PARAM_ABS),
])
def test_render_abstract(d, min_inputs, max_inputs, params, expected):
    kwargs = params.copy()

    kwargs['min_inputs'] = min_inputs
    kwargs['max_inputs'] = max_inputs

    text = cdat.render_abstract(d, **kwargs)

    assert text == expected


def test_discover_processes(mocker):
    mocker.spy(builtins, 'setattr')
    mocker.patch.object(base, 'cwt_shared_task')
    mocker.patch.object(base, 'register_process')

    cdat.discover_processes()

    base.cwt_shared_task.assert_called()
    base.cwt_shared_task.return_value.assert_called()

    base.register_process.assert_any_call('CDAT', 'subset', abstract=cdat.ABSTRACT_MAP['CDAT.subset'])

    builtins.setattr.assert_any_call(cdat, 'subset_func', base.register_process.return_value.return_value)
