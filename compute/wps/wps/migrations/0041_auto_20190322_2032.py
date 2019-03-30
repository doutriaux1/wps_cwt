# Generated by Django 2.1.7 on 2019-03-22 20:32

from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('wps', '0040_update_process'),
    ]

    operations = [
        migrations.AlterField(
            model_name='message',
            name='status',
            field=models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='messages', to='wps.Status'),
        ),
        migrations.AlterField(
            model_name='status',
            name='job',
            field=models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='status', to='wps.Job'),
        ),
    ]