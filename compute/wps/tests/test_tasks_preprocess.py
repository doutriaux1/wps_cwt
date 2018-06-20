#! /usr/bin/env python

import copy
import json
import mock

import cwt
import requests
from django import test
from django.conf import settings

import wps
from wps import helpers
from wps import models
from wps.tasks import preprocess

class MockFilter:
    def __init__(self, items):
        self.items = items

    def count(self):
        return len(self.items)

    def __getitem__(self, x):
        return self.items[x]

    def __len__(self):
        return self.count()

class PreprocessTestCase(test.TestCase):

    fixtures = ['users.json']

    def setUp(self):
        self.user = models.User.objects.first()

        self.uris = [
            'file:///test1.nc',
            'file:///test2.nc',
            'file:///test3.nc',
        ]

        self.mock_time = mock.MagicMock()
        type(self.mock_time).id = mock.PropertyMock(return_value='time')
        type(self.mock_time.getTime.return_value).units = mock.PropertyMock(return_value='days since 1990-1-1 0')
        type(self.mock_time.clone.return_value).id = mock.PropertyMock(return_value='time')
        self.mock_time.isTime.return_value = True
        self.mock_time.clone.return_value.mapInterval.return_value = (0, 122)
        type(self.mock_time).shape = mock.PropertyMock(return_value=(122, 0))

        self.mock_time2 = mock.MagicMock()
        type(self.mock_time2).id = mock.PropertyMock(return_value='time')
        type(self.mock_time2.getTime.return_value).units = mock.PropertyMock(return_value='days since 2000-1-1 0')
        self.mock_time2.clone.return_value.mapInterval.return_value = (0, 120)
        type(self.mock_time2).shape = mock.PropertyMock(return_value=(120, 0))

        self.mock_time3 = mock.MagicMock()
        type(self.mock_time3).id = mock.PropertyMock(return_value='time')
        type(self.mock_time3.getTime.return_value).units = mock.PropertyMock(return_value='days since 2010-1-1 0')
        self.mock_time3.clone.return_value.mapInterval.return_value = (0, 120)
        type(self.mock_time3).shape = mock.PropertyMock(return_value=(120, 0))

        self.mock_lat = mock.MagicMock()
        type(self.mock_lat).id = mock.PropertyMock(return_value='lat')
        self.mock_lat.isTime.return_value = False
        self.mock_lat.mapInterval.return_value = (0, 100)
        type(self.mock_lat).shape = mock.PropertyMock(return_value=(100,))

        self.mock_lon = mock.MagicMock()
        type(self.mock_lon).id = mock.PropertyMock(return_value='lon')
        self.mock_lon.isTime.return_value = False
        self.mock_lon.mapInterval.return_value = (0, 200)

        self.domain1 = cwt.Domain(time=(0, 200), lat=(-90, 0), lon=(180, 360))
        self.domain2 = cwt.Domain(time=(0, 400), lon=(180, 360))
        self.domain3 = cwt.Domain(time=(0, 400), lat=slice(0, 100), lon=slice(0, 200))
        self.domain4 = cwt.Domain([cwt.Dimension('lat', 0, 200, cwt.CRS('test'))])
        self.domain5 = cwt.Domain(time=slice(0, 200), lat=slice(0, 100), lon=(180, 360))

        self.units_single = {
            'base_units': 'days since 1990-1-1 0', 
            self.uris[0]: {},
        }

        self.units_multiple = {
            'base_units': 'days since 1990-1-1 0', 
            self.uris[0]: {},
            self.uris[1]: {},
            self.uris[2]: {},
        }

        self.map_single = copy.deepcopy(self.units_single)
        self.map_single['var_name'] = 'tas'
        self.map_single[self.uris[0]]['mapped'] = {
            'time': slice(0, 122),
            'lat': slice(0, 100),
            'lon': slice(0, 200),
        }

        self.map_single_error = copy.deepcopy(self.map_single)
        self.map_single_error[self.uris[0]]['mapped'] = None

        self.map_single_indices = copy.deepcopy(self.map_single)
        self.map_single_indices[self.uris[0]]['mapped'] = {
            'time': slice(0, 122),
            'lat': (0, 100),
            'lon': (0, 200),
        }

        self.map_multiple = copy.deepcopy(self.units_multiple)
        self.map_multiple['var_name'] = 'tas'
        self.map_multiple[self.uris[0]]['mapped'] = {
            'time': slice(0, 122),
            'lat': slice(0, 100),
            'lon': (180, 360),
        }
        self.map_multiple[self.uris[1]]['mapped'] = {
            'time': slice(0, 78),
            'lat': slice(0, 100),
            'lon': (180, 360),
        }
        self.map_multiple[self.uris[2]]['mapped'] = None

        self.cache_multiple = copy.deepcopy(self.map_multiple)
        self.cache_multiple[self.uris[0]]['cached'] = 'file:///test1_cached.nc'

        self.mock_f = mock.MagicMock()
        type(self.mock_f).url = mock.PropertyMock(return_value='file:///test1_cached.nc')
        type(self.mock_f).valid = mock.PropertyMock(return_value=True)
        self.mock_f.is_superset.return_value = True

        self.mock_f2 = mock.MagicMock()
        type(self.mock_f2).url = mock.PropertyMock(return_value='file:///test2_cached.nc')
        type(self.mock_f2).valid = mock.PropertyMock(return_value=True)
        self.mock_f2.is_superset.return_value = True

        self.mock_f3 = mock.MagicMock()
        type(self.mock_f3).url = mock.PropertyMock(return_value='file:///test3_cached.nc')
        type(self.mock_f3).valid = mock.PropertyMock(return_value=True)
        self.mock_f3.is_superset.return_value = True

    @mock.patch('wps.models.Cache.objects.filter')
    def test_check_cache(self, mock_filter):
        self.mock_f3.is_superset.return_value = False

        mock_filter.return_value = MockFilter([self.mock_f, self.mock_f2, self.mock_f3])

        data = preprocess.check_cache(self.map_multiple, self.uris[0])

        self.assertEqual(data, self.cache_multiple)

    @mock.patch('wps.tasks.credentials.load_certificate')
    @mock.patch('cdms2.open')
    @mock.patch('wps.tasks.preprocess.get_axis_list')
    @mock.patch('wps.tasks.preprocess.get_uri')
    def test_map_domain_aggregate(self, mock_uri, mock_axis, mock_open, mock_load):
        mock_axis.side_effect = [
            [self.mock_time, self.mock_lat, self.mock_lon],
            [self.mock_time2, self.mock_lat, self.mock_lon],
            [self.mock_time3, self.mock_lat, self.mock_lon],
        ]

        mock_uri.side_effect = self.uris 

        data = preprocess.map_domain_aggregate(self.units_multiple, self.uris, 'tas', self.domain5, self.user.id)

        self.assertEqual(data, self.map_multiple)

    @mock.patch('wps.tasks.credentials.load_certificate')
    @mock.patch('cdms2.open')
    @mock.patch('wps.tasks.preprocess.get_axis_list')
    def test_map_domain_map_interval_error(self, mock_axis, mock_open, mock_load):
        self.mock_lat.mapInterval.side_effect = TypeError()

        mock_axis.return_value = [self.mock_time,self.mock_lat,self.mock_lon]

        data = preprocess.map_domain(self.units_single, self.uris[0], 'tas', self.domain1, self.user.id)

        self.assertEqual(data, self.map_single_error)

    @mock.patch('wps.tasks.credentials.load_certificate')
    @mock.patch('cdms2.open')
    @mock.patch('wps.tasks.preprocess.get_axis_list')
    def test_map_domain_not_in_user_domain(self, mock_axis, mock_open, mock_load):
        mock_axis.return_value = [self.mock_time,self.mock_lat,self.mock_lon]
        
        data = preprocess.map_domain(self.units_single, self.uris[0], 'tas', self.domain2, self.user.id)

        self.assertEqual(data, self.map_single)

    @mock.patch('wps.tasks.credentials.load_certificate')
    @mock.patch('cdms2.open')
    @mock.patch('wps.tasks.preprocess.get_axis_list')
    def test_map_domain_indices(self, mock_axis, mock_open, mock_load):
        self.maxDiff=None
        mock_axis.return_value = [self.mock_time,self.mock_lat,self.mock_lon]
        
        data = preprocess.map_domain(self.units_single, self.uris[0], 'tas', self.domain3, self.user.id)

        self.assertEqual(data, self.map_single_indices)

    @mock.patch('wps.tasks.credentials.load_certificate')
    @mock.patch('cdms2.open')
    @mock.patch('wps.tasks.preprocess.get_axis_list')
    def test_map_domain_crs_unknown(self, mock_axis, mock_open, mock_load):
        mock_axis.return_value = [self.mock_lat]
        
        with self.assertRaises(wps.WPSError):
            data = preprocess.map_domain(self.units_single, self.uris[0], 'tas', self.domain4, self.user.id)

    @mock.patch('wps.tasks.credentials.load_certificate')
    @mock.patch('cdms2.open')
    @mock.patch('wps.tasks.preprocess.get_axis_list')
    def test_map_domain(self, mock_axis, mock_open, mock_load):
        self.maxDiff = None
        mock_axis.return_value = [self.mock_time,self.mock_lat,self.mock_lon]
        
        data = preprocess.map_domain(self.units_single, self.uris[0], 'tas', self.domain1, self.user.id)

        self.assertEqual(data, self.map_single)

    @mock.patch('wps.tasks.credentials.load_certificate')
    @mock.patch('cdms2.open')
    @mock.patch('wps.tasks.preprocess.get_variable')
    def test_determine_base_units_no_files(self, mock_variable, mock_open, mock_load):
        with self.assertRaises(wps.WPSError):
            data = preprocess.determine_base_units([], 'tas', self.user.id)

    @mock.patch('wps.tasks.credentials.load_certificate')
    @mock.patch('cdms2.open')
    @mock.patch('wps.tasks.preprocess.get_variable')
    def test_determine_base_units(self, mock_variable, mock_open, mock_load):
        mock_variable.side_effect = [
            self.mock_time3,
            self.mock_time,
            self.mock_time2,
        ]

        data = preprocess.determine_base_units(self.uris, 'tas', self.user.id)

        self.assertEqual(data, self.units_multiple)

    def test_get_axis_list(self):
        mock_variable = mock.MagicMock()
        mock_variable.getAxisList.return_value = [self.mock_time, self.mock_lat]

        data = preprocess.get_axis_list(mock_variable)

        self.assertEqual(data, [self.mock_time, self.mock_lat])

    @mock.patch('wps.tasks.credentials.load_certificate')
    @mock.patch('wps.models.User.objects.get')
    def test_load_credentials_missing_user(self, mock_get, mock_load):
        mock_get.side_effect = models.User.DoesNotExist()

        with self.assertRaises(wps.WPSError):
            preprocess.load_credentials(self.user.id)

    @mock.patch('wps.tasks.credentials.load_certificate')
    def test_load_credentials(self, mock_load):
        preprocess.load_credentials(self.user.id)

        mock_load.assert_called_with(self.user)
