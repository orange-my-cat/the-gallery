"""Places (MET-03): the GeoNames list of every town of 1,000 people or more.

Loaded once from the files bundled in `places/data` (GeoNames, CC BY 4.0), so
turning a photo's position into a name never asks an online service.
"""

from __future__ import annotations

from django.db import models


class Place(models.Model):
    # GeoNames' own id, so a reload updates rows in place.
    id = models.IntegerField(primary_key=True)
    name = models.CharField(max_length=200)
    region = models.CharField(max_length=200, blank=True, default="")
    country = models.CharField(max_length=100, blank=True, default="")
    country_code = models.CharField(max_length=2, blank=True, default="")
    lat = models.FloatField()
    lon = models.FloatField()
    population = models.BigIntegerField(default=0)

    class Meta:
        indexes = [models.Index(fields=["lat", "lon"], name="place_position_idx")]

    def __str__(self) -> str:
        return self.label

    @property
    def label(self) -> str:
        """'George Town, Penang, Malaysia' - each part once."""
        parts: list[str] = []
        for part in (self.name, self.region, self.country):
            if part and part not in parts:
                parts.append(part)
        return ", ".join(parts)
