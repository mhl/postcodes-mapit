from django.contrib.gis.db import models as gis_models
from django.db import models


class VoronoiRegion(models.Model):
    polygon = gis_models.PolygonField(srid=27700, null=True)


class NSULRow(models.Model):
    point = gis_models.PointField(srid=27700)
    uprn = models.CharField(max_length=12, unique=True)
    postcode = models.CharField(max_length=8)
    voronoi_region = models.ForeignKey(
        VoronoiRegion, on_delete=models.CASCADE, null=True
    )
    region_code = models.CharField(max_length=2)

    def __repr__(self):
        return (
            f"NSULRow(point=Point({int(self.point.x)}, {int(self.point.y)}), "
            + f"uprn={repr(self.uprn)}, "
            + f"postcode={repr(self.postcode)}, "
            + f"region_code={repr(self.region_code)})"
        )
