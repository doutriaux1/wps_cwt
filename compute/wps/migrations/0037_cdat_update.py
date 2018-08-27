# -*- coding: utf-8 -*-
# Generated by Django 1.11.9 on 2018-08-27 16:58
from __future__ import unicode_literals

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('wps', '0036_process_timing'),
    ]

    operations = [
        migrations.RemoveField(
            model_name='notification',
            name='user',
        ),
        migrations.RemoveField(
            model_name='processusage',
            name='process',
        ),
        migrations.RemoveField(
            model_name='timing',
            name='process',
        ),
        migrations.RemoveField(
            model_name='process',
            name='process_rate',
        ),
        migrations.DeleteModel(
            name='Notification',
        ),
        migrations.DeleteModel(
            name='ProcessUsage',
        ),
        migrations.DeleteModel(
            name='Timing',
        ),
    ]
