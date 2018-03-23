# Generated by Django 2.0.3 on 2018-03-23 14:07

from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    initial = True

    dependencies = [
    ]

    operations = [
        migrations.CreateModel(
            name='Action',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('recording_date', models.DateTimeField(auto_now=True)),
                ('occurrence_date', models.DateTimeField(null=True)),
                ('actor_name', models.CharField(max_length=255, null=True)),
                ('actor_email_address', models.EmailField(max_length=255, null=True)),
                ('actor_business_sso_uri', models.URLField(max_length=255, null=True)),
                ('source', models.CharField(max_length=255, null=True)),
            ],
            options={
                'db_table': 'action',
            },
        ),
        migrations.CreateModel(
            name='ActionDetail',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('key', models.CharField(max_length=255)),
                ('value', models.TextField()),
                ('action', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='action_details', to='core.Action')),
            ],
            options={
                'db_table': 'action_detail',
            },
        ),
        migrations.CreateModel(
            name='ActionType',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('name', models.CharField(max_length=255, unique=True)),
            ],
            options={
                'db_table': 'action_type',
            },
        ),
        migrations.CreateModel(
            name='AddActionDetailRequest',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('key', models.CharField(max_length=255)),
                ('value', models.TextField()),
            ],
            options={
                'db_table': 'add_action_detail_request',
            },
        ),
        migrations.CreateModel(
            name='AddActionRequest',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('source', models.CharField(max_length=255, null=True)),
                ('occurrence_date', models.DateTimeField(null=True)),
                ('actor_name', models.CharField(max_length=255, null=True)),
                ('actor_email_address', models.EmailField(max_length=255, null=True)),
                ('actor_business_sso_uri', models.URLField(max_length=255, null=True)),
            ],
            options={
                'db_table': 'add_action_request',
            },
        ),
        migrations.AddField(
            model_name='addactiondetailrequest',
            name='add_action_request',
            field=models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='details', to='core.AddActionRequest'),
        ),
        migrations.AddField(
            model_name='action',
            name='action_type',
            field=models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, to='core.ActionType'),
        ),
    ]
