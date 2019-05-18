from __future__ import unicode_literals

import os

from django.apps import AppConfig

from cwt_settings import settings


class WpsConfig(AppConfig):
    name = 'wps'

    def ready(self):
        from django.conf import settings as wps_settings
        from wps import metrics # noqa
        from wps import WPSError # noqa
        from wps import signals # noqa

        os.environ['UVCDAT_ANONYMOUS_LOG'] = 'no'

        settings.patch_settings(wps_settings)
