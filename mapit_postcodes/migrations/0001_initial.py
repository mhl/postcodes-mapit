# Generated by Django 3.1.3 on 2021-06-11 13:42

import django.contrib.gis.db.models.fields
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    initial = True

    dependencies = [
    ]

    operations = [
        migrations.CreateModel(
            name='VoronoiRegion',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('polygon', django.contrib.gis.db.models.fields.PolygonField(null=True, srid=27700)),
            ],
        ),
        migrations.CreateModel(
            name='NSULRow',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('point', django.contrib.gis.db.models.fields.PointField(srid=27700)),
                ('uprn', models.CharField(max_length=12, unique=True)),
                ('postcode', models.CharField(max_length=8)),
                ('region_code', models.CharField(max_length=2)),
                ('voronoi_region', models.ForeignKey(null=True, on_delete=django.db.models.deletion.CASCADE, to='mapit_postcodes.voronoiregion')),
            ],
        ),
    ]