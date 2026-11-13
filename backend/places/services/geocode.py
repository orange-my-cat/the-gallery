"""Place names from GPS positions, offline (MET-03, HLD "Place names").

- `load()` fills Place from the bundled GeoNames files. The worker does it by
  itself the first time it finds the table empty.
- `nearest(lat, lon)` is the closest town: candidates come from a box of
  +/-0.5 degrees on the indexed position, ordered by distance, and the box
  widens until the nearest candidate is inside the circle it can vouch for.
- The worker geocodes items in batches: any item with a position and no
  place worked out yet. A changed position (a re-read file, an edit, a
  merge) clears the old answer, so it is simply worked out again.
"""

from __future__ import annotations

import io
import logging
import math
import zipfile
from pathlib import Path

from django.db import transaction
from django.db.models import ExpressionWrapper, F, FloatField, Value

from jobs.models import Job, JobState
from jobs.services import enqueue
from library.models import Asset
from places.models import Place

logger = logging.getLogger("gallery")

DATA = Path(__file__).resolve().parent.parent / "data"
FIRST_SPAN = 0.5  # degrees of latitude either side
LAST_SPAN = 8.0  # beyond this, at sea or in a desert: no name rather than a far one
BATCH = 2000
_LOAD_BATCH = 5000


# ---------------------------------------------------------------------------
# Loading GeoNames
# ---------------------------------------------------------------------------


def _lines(path: Path):
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            if line.strip() and not line.startswith("#"):
                yield line.rstrip("\n").split("\t")


def _countries(data: Path) -> dict[str, str]:
    return {row[0]: row[4] for row in _lines(data / "countryInfo.txt") if len(row) > 4}


def _regions(data: Path) -> dict[str, str]:
    return {row[0]: row[1] for row in _lines(data / "admin1CodesASCII.txt") if len(row) > 1}


def load(data: Path | None = None) -> int:
    """Replace Place with the bundled list. Returns the number of places."""
    data = data or DATA
    countries = _countries(data)
    regions = _regions(data)
    with zipfile.ZipFile(data / "cities1000.zip") as archive:
        name = next(n for n in archive.namelist() if n.endswith(".txt"))
        with archive.open(name) as raw, transaction.atomic():
            Asset.objects.filter(geo_place__isnull=False).update(geo_place=None, geocoded=False)
            Place.objects.all().delete()
            batch: list[Place] = []
            total = 0
            for line in io.TextIOWrapper(raw, encoding="utf-8"):
                row = line.rstrip("\n").split("\t")
                if len(row) < 15:
                    continue
                code = row[8]
                batch.append(
                    Place(
                        id=int(row[0]),
                        name=row[1][:200],
                        region=regions.get(f"{code}.{row[10]}", "")[:200],
                        country=countries.get(code, code)[:100],
                        country_code=code[:2],
                        lat=float(row[4]),
                        lon=float(row[5]),
                        population=int(row[14] or 0),
                    )
                )
                if len(batch) >= _LOAD_BATCH:
                    Place.objects.bulk_create(batch)
                    total += len(batch)
                    batch = []
            Place.objects.bulk_create(batch)
            total += len(batch)
    logger.info("places: loaded %s from GeoNames", total)
    return total


def load_job(job: Job) -> None:
    if not Place.objects.exists():
        load()


# ---------------------------------------------------------------------------
# Nearest place
# ---------------------------------------------------------------------------


def nearest(lat: float, lon: float) -> Place | None:
    """The closest town to a position, or None when there is none for miles."""
    squeeze = max(math.cos(math.radians(lat)), 0.01)  # a degree of longitude, in degrees of latitude
    span = FIRST_SPAN
    best: Place | None = None
    while span <= LAST_SPAN:
        lon_span = min(span / squeeze, 180.0)
        distance = ExpressionWrapper(
            (F("lat") - Value(lat)) * (F("lat") - Value(lat))
            + (F("lon") - Value(lon)) * (F("lon") - Value(lon)) * Value(squeeze * squeeze),
            output_field=FloatField(),
        )
        best = (
            Place.objects.filter(lat__range=(lat - span, lat + span), lon__range=(lon - lon_span, lon + lon_span))
            .annotate(d2=distance)
            .order_by("d2", "-population")
            .first()
        )
        # Nearer than the box's edge: nothing outside the box can be nearer.
        if best is not None and best.d2 <= span * span:
            return best
        span *= 2
    return best


def geocode(asset_ids) -> int:
    """Work out the place of these items. Returns how many got one."""
    named = 0
    for asset in Asset.objects.filter(pk__in=list(asset_ids)).only("pk", "lat", "lon"):
        place = nearest(asset.lat, asset.lon) if asset.lat is not None and asset.lon is not None else None
        # Only if the position is still the one looked up.
        Asset.objects.filter(pk=asset.pk, lat=asset.lat, lon=asset.lon).update(geo_place=place, geocoded=True)
        named += place is not None
    return named


def geocode_job(job: Job) -> None:
    geocode(job.payload.get("assets", []))


def _busy(kind: str) -> bool:
    return Job.objects.filter(kind=kind, state__in=[JobState.QUEUED, JobState.RUNNING]).exists()


def schedule_due(now=None) -> str | None:
    """Worker scheduler: load the places once, then geocode what needs it."""
    if not Place.objects.exists():
        if not _busy("load_places"):
            enqueue("load_places", {})
            return "load"
        return None
    if _busy("geocode"):
        return None
    ids = list(
        Asset.objects.filter(geocoded=False, lat__isnull=False, lon__isnull=False)
        .order_by("pk")
        .values_list("pk", flat=True)[:BATCH]
    )
    if ids:
        enqueue("geocode", {"assets": ids})
        return "geocode"
    return None
