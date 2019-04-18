from __future__ import unicode_literals
from __future__ import division

from future import standard_library
from functools import reduce
standard_library.install_aliases()
from builtins import range
from past.utils import old_div
from builtins import object
import base64
import contextlib
import datetime
import hashlib
import json
import logging
import os
import random
import re
import string
import sys
import time
from urllib.parse import urlparse

import cdms2
import cwt
from django.conf import settings
from django.contrib.auth.models import User
from django.db import models
from django.db import transaction
from django.db.models import F
from django.db.models import signals
from django.db.models.query_utils import Q
from django.utils import timezone
from openid import association
from openid.store import interface
from openid.store import nonce
from rest_framework.settings import api_settings

from wps import helpers
from wps import metrics
from wps.util import wps_response

logger = logging.getLogger('wps.models')

ProcessAccepted = 'ProcessAccepted'
ProcessStarted = 'ProcessStarted'
ProcessPaused = 'ProcessPaused'
ProcessSucceeded = 'ProcessSucceeded'
ProcessFailed = 'ProcessFailed'

class DjangoOpenIDStore(interface.OpenIDStore):
    # Heavily borrowed from http://bazaar.launchpad.net/~ubuntuone-pqm-team/django-openid-auth/trunk/view/head:/django_openid_auth/store.py

    def storeAssociation(self, server_url, association):
        logger.info('storeAssociation %r %r', server_url, association)

        try:
            assoc = OpenIDAssociation.objects.get(server_url=server_url,
                                                 handle=association.handle)
        except OpenIDAssociation.DoesNotExist:
            assoc = OpenIDAssociation(server_url=server_url,
                                      handle=association.handle,
                                      secret=base64.encodestring(association.secret).decode(),
                                      issued=association.issued,
                                      lifetime=association.lifetime,
                                      assoc_type=association.assoc_type)
        else:
            assoc.secret = base64.encodestring(association.secret).decode()
            assoc.issued = association.issued
            assoc.lifetime = association.lifetime
            assoc.assoc_type = association.assoc_type

        assoc.save()

    def getAssociation(self, server_url, handle=None):
        logger.info('getAssociation %r %r', server_url, handle)

        if handle is not None:
            assocs = OpenIDAssociation.objects.filter(server_url=server_url,
                                                     handle=handle)
        else:
            assocs = OpenIDAssociation.objects.filter(server_url=server_url)

        active = []
        expired = []

        for a in assocs:
            assoc = association.Association(a.handle,
                                            base64.decodestring(a.secret.encode()),
                                            a.issued,
                                            a.lifetime,
                                            a.assoc_type)

            try:
                expires_in = assoc.getExpiresIn()
            except AttributeError:
                expires_in = assoc.expiresIn

            if expires_in == 0:
                expired.append(a)
            else:
                active.append((assoc.issued, assoc))

        for e in expired:
            e.delete()

        if len(active) == 0:
            return None

        active.sort()

        return active[-1][1]

    def removeAssociation(self, server_url, handle):
        assocs = OpenIDAssociations.objects.filter(server_url=server_url, handle=handle)

        for a in assocs:
            a.delete()

        return len(assocs) > 0

    def useNonce(self, server_url, timestamp, salt):
        if abs(timestamp - time.time()) > nonce.SKEW:
            return False

        try:
            ononce = OpenIDNonce.objects.get(server_url__exact=server_url,
                                             timestamp__exact=timestamp,
                                             salt__exact=salt)
        except OpenIDNonce.DoesNotExist:
            ononce = OpenIDNonce.objects.create(server_url=server_url,
                                                timestamp=timestamp,
                                                salt=salt)
            return True

        return False

    def cleanupNonces(self, now=None):
        if now is None:
            now = int(time.time())
        expired = OpenIDNonce.objects.filter(timestamp__lt=now-nonce.SKEW)
        count = expired.count()
        if count:
            expired.delete()
        return count

    def cleanupAssociations(self):
        now = int(time.time())
        expired = OpenIDAssociation.objects.extra(where=['issued + lifetime < %d' % now])
        count = expired.count()
        if count:
            expired.delete()
        return count

class OpenIDNonce(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE, null=True)
    server_url = models.CharField(max_length=2048)
    timestamp = models.IntegerField()
    salt = models.CharField(max_length=40)

    class Meta(object):
        unique_together = ('server_url', 'timestamp', 'salt')

    def __str__(self):
        return '{0.server_url}'.format(self)

class OpenIDAssociation(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE, null=True)
    server_url = models.CharField(max_length=2048)
    handle = models.CharField(max_length=256)
    secret = models.TextField(max_length=256)
    issued = models.IntegerField()
    lifetime = models.IntegerField()
    assoc_type = models.TextField(max_length=64)

    class Meta(object):
        unique_together = ('server_url', 'handle')

    def __str__(self):
        return ('{0.server_url} {0.handle}'.format(self))

class File(models.Model):
    name = models.CharField(max_length=256)
    host = models.CharField(max_length=256)
    variable = models.CharField(max_length=64)
    url = models.TextField()
    requested = models.PositiveIntegerField(default=0)

    class Meta(object):
        unique_together = ('name', 'host')

    @staticmethod
    def track(user, variable):
        url_parts = urlparse(variable.uri)

        parts = url_parts[2].split('/')

        file_obj, _ = File.objects.get_or_create(
            name=parts[-1],
            host=url_parts[1],
            defaults={
                'variable': variable.var_name,
                'url': variable.uri,
            }
        )

        file_obj.requested = F('requested') + 1

        file_obj.save()

        user_file_obj, _ = UserFile.objects.get_or_create(user=user, file=file_obj)

        user_file_obj.requested = F('requested') + 1

        user_file_obj.save()

    def to_json(self):
        return {
            'name': self.name,
            'host': self.host,
            'variable': self.variable,
            'url': self.url,
            'requested': self.requested
        }

    def __str__(self):
        return '{0.name}'.format(self)

class UserFile(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    file = models.ForeignKey(File, on_delete=models.CASCADE)
    requested_date = models.DateTimeField(auto_now=True)
    requested = models.PositiveIntegerField(default=0)

    def to_json(self):
        data = self.file.to_json()

        data['requested'] = self.requested

        data['requested_date'] = self.requested_date

        return data

    def __str__(self):
        return '{0.file.name}'.format(self)

class Auth(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE)

    openid_url = models.CharField(max_length=256)
    type = models.CharField(max_length=64)
    cert = models.TextField()
    api_key = models.CharField(max_length=128)
    extra = models.TextField()

    def update(self, auth_type, certs, **kwargs):
        if self.api_key == '':
            self.api_key = ''.join(random.choice(string.ascii_letters+string.digits) for _ in range(64))

        self.type = auth_type

        self.cert = ''.join(list(x.decode() for x in certs))

        extra = json.loads(self.extra or '{}')

        extra.update(kwargs)

        self.extra = json.dumps(extra)

        self.save()

    def __str__(self):
        return '{0.openid_url} {0.type}'.format(self)

class Process(models.Model):
    identifier = models.CharField(max_length=128, blank=False)
    backend = models.CharField(max_length=128)
    abstract = models.TextField()
    metadata = models.TextField()
    version = models.CharField(max_length=128, blank=False, default=None)

    class Meta(object):
        unique_together = (('identifier', 'version'),)

    def decode_metadata(self):
        try:
            return json.loads(self.metadata)
        except ValueError:
            return {}

    def track(self, user):
        user_process_obj, _ = UserProcess.objects.get_or_create(user=user, process=self)

        user_process_obj.requested = F('requested') + 1

        user_process_obj.save()

    def to_json(self, usage=False):
        return {
            'identifier': self.identifier,
            'backend': self.backend,
        }

    def __str__(self):
        return '{0.identifier}'.format(self)

class UserProcess(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    process = models.ForeignKey(Process, on_delete=models.CASCADE)
    requested_date = models.DateTimeField(auto_now=True)
    requested = models.PositiveIntegerField(default=0)

    def to_json(self):
        data = self.process.to_json()

        data['requested'] = self.requested

        data['requested_date'] = self.requested_date

        return data

    def __str__(self):
        return '{0.process.identifier}'.format(self)

class Server(models.Model):
    host = models.CharField(max_length=128, unique=True)
    added_date = models.DateTimeField(auto_now_add=True)
    status = models.IntegerField(default=1)
    capabilities = models.TextField()

    processes = models.ManyToManyField(Process)

    def __str__(self):
        return '{0.host}'.format(self)

class Job(models.Model):
    server = models.ForeignKey(Server, on_delete=models.CASCADE)
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    process = models.ForeignKey(Process, on_delete=models.CASCADE, null=True)
    steps_completed = models.IntegerField(default=0)
    steps_total = models.IntegerField(default=0)
    extra = models.TextField(null=True)

    @property
    def details(self):
        return {
            'id': self.id,
            'server': self.server.host,
            'process': self.process.identifier,
            'elapsed': self.elapsed,
            #'status': self.status
        }

    #@property
    #def status(self):
    #    return [
    #        {
    #            'created_date': x.created_date,
    #            'updated_date': x.updated_date,
    #            'status': x.status,
    #            'exception': x.exception,
    #            'output': x.output,
    #            'messages': [y.details for y in x.messages.all().order_by('created_date')]
    #        } for x in self.status.all().order_by('created_date')
    #    ]

    @property
    def latest_status(self):
        latest = self.status.latest('created_date')

        return latest.status

    @property
    def accepted_on(self):
        accepted = self.status.filter(status=ProcessAccepted)

        if len(accepted) == 0:
            return 'Unknown'

        return accepted[0].created_date.isoformat()

    @property
    def elapsed(self):
        started = self.status.filter(status='ProcessStarted')

        ended = self.status.filter(Q(status='ProcessSucceeded') | Q(status='ProcessFailed'))

        if len(started) == 0 or len(ended) == 0:
            return 'No elapsed'

        elapsed = ended[0].created_date - started[0].created_date

        return '{}.{}'.format(elapsed.seconds, elapsed.microseconds)

    @property
    def report(self):
        latest = self.status.latest('updated_date')

        earliest = self.status.earliest('created_date')

        kwargs = {
            'status_location': settings.WPS_STATUS_LOCATION.format(job_id=self.id),
            'instance': settings.WPS_ENDPOINT,
            'latest': latest,
            'earliest': earliest,
            'process': self.process,
        }

        data_inputs = json.loads(self.extra)

        kwargs.update(data_inputs)

        return wps_response.execute(**kwargs)

    @property
    def is_started(self):
        latest = self.status.latest('created_date')

        return latest is not None and latest.status == ProcessStarted

    @property
    def is_failed(self):
        latest = self.status.latest('created_date')

        return latest is not None and latest.status == ProcessFailed

    @property
    def is_succeeded(self):
        latest = self.status.latest('created_date')

        return latest is not None and latest.status == ProcessSucceeded

    @property
    def progress(self):
        try:
            return old_div((self.steps_completed + 1) * 100.0, self.steps_total)
        except ZeroDivisionError:
            return 0.0

    def step_inc(self, steps=None):
        if steps is None:
            steps = 1

        self.steps_total = F('steps_total') + steps

        self.save()

        self.refresh_from_db()

    def step_complete(self):
        self.steps_completed = F('steps_completed') + 1

        self.save()

        self.refresh_from_db()

    def accepted(self):
        self.status.create(status=ProcessAccepted)

        metrics.WPS_JOBS_ACCEPTED.inc()

    def started(self):
        # Guard against duplicates
        if not self.is_started:
            status = self.status.create(status=ProcessStarted)

            status.set_message('Job Started', self.progress)

            metrics.WPS_JOBS_STARTED.inc()

    def succeeded(self, output=None):
        # Guard against duplicates
        if not self.is_succeeded:
            if output is None:
                output = ''

            status = self.status.create(status=ProcessSucceeded)

            status.output = output

            status.save()

            metrics.WPS_JOBS_SUCCEEDED.inc()

    def failed(self, exception):
        # Guard against duplicates
        if not self.is_failed:
            status = self.status.create(status=ProcessFailed)

            status.exception = exception

            status.save()

            metrics.WPS_JOBS_FAILED.inc()

    def retry(self, exception):
        self.update('Retrying... {}', exception)

    def update(self, message,  *args, **kwargs):
        percent = kwargs.pop('percent', 0)

        message = message.format(*args, **kwargs)

        started = self.status.filter(status=ProcessStarted).latest('created_date')

        started.set_message(message, percent)

    def statusSince(self, date):
        return [
            {
                'created_date': x.created_date,
                'updated_date': x.updated_date,
                'status': x.status,
                'exception': x.exception,
                'output': x.output,
                'messages': [y.details for y in x.messages.all().order_by('created_date')]
            } for x in self.status.filter(updated_date__gt=date)
        ]

class Status(models.Model):
    job = models.ForeignKey(Job, related_name='status', on_delete=models.CASCADE)

    created_date = models.DateTimeField(auto_now_add=True)
    updated_date = models.DateTimeField(auto_now_add=True)
    status = models.CharField(max_length=128)
    exception = models.TextField(null=True)
    output = models.TextField(null=True)

    @property
    def latest_message(self):
        try:
            latest = self.messages.latest('created_date')
        except Message.DoesNotExist:
            message = ''
        else:
            message = latest.message

        return message

    @property
    def latest_percent(self):
        try:
            latest = self.messages.latest('created_date')
        except Message.DoesNotExist:
            percent = ''
        else:
            percent = latest.percent

        return percent

    @property
    def exception_clean(self):
        removed_tag = re.sub('<\\?xml.*>\\n', '', self.exception)

        cleaned = re.sub('ExceptionReport.*>', 'ExceptionReport>', removed_tag)

        return cleaned

    def set_message(self, message, percent=None):
        self.messages.create(message=message, percent=percent)

        self.updated_date = timezone.now()

        self.save()

    def __str__(self):
        return '{0.status}'.format(self)

class Message(models.Model):
    status = models.ForeignKey(Status, related_name='messages', on_delete=models.CASCADE)

    created_date = models.DateTimeField(auto_now_add=True)
    percent = models.PositiveIntegerField(null=True)
    message = models.TextField(null=True)

    @property
    def details(self):
        return {
            'created_date': self.created_date,
            'percent': self.percent or 0,
            'message': self.message
        }

    def __str__(self):
        return '{0.message}'.format(self)
