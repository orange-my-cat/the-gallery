"""Timeline filters (BRW-06): date range, media type, camera, tag, rating,
favourite and has-location. Album, folder, people and map area are filters
of their own, older than these (see `browse.timeline_page`).

Every filter narrows the timeline further: they combine with each other, with
search and with those older ones. `facets()` lists what can be chosen - the
cameras and tags that the visible library has - with how many items each.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from django.conf import settings
from django.db.models import Count, DateField, Exists, OuterRef, Q, QuerySet
from django.db.models.expressions import RawSQL

from library.models import Asset, AssetKind, FileState, MediaFile
from library.services.paths import RAW_EXTENSIONS
from organise.models import AssetTag, Tag

MEDIA = ("photo", "video", "raw")
_RAW_PATH = r"\.(" + "|".join(sorted(RAW_EXTENSIONS)) + r")$"


@dataclass(frozen=True)
class Filters:
    since: date | None = None  # capture day, inclusive
    until: date | None = None  # capture day, inclusive
    media: str = ""  # photo | video | raw
    camera: str = ""  # as `camera_name` shows it
    tag: str = ""
    rating: int = 0  # at least this many stars
    favourites: bool = False
    located: bool | None = None  # has a GPS position, or has none

    @property
    def empty(self) -> bool:
        return self == Filters()


def _local_day() -> RawSQL:
    """The day it was taken, where it was taken - the timeline's own day: by
    the photo's UTC offset when known, else the library's time zone."""
    t = Asset._meta.db_table
    return RawSQL(
        f'CASE WHEN "{t}"."capture_offset" IS NOT NULL '
        f"""THEN (("{t}"."capture_at" AT TIME ZONE 'UTC') + "{t}"."capture_offset" * interval '1 minute')::date """
        f'ELSE ("{t}"."capture_at" AT TIME ZONE %s)::date END',
        [settings.TIME_ZONE],
        output_field=DateField(),
    )


def _camera_pairs(qs: QuerySet[Asset], name: str) -> list[tuple[str, str]]:
    from library.services.browse import camera_name

    pairs = qs.exclude(camera_make="", camera_model="").values_list("camera_make", "camera_model").distinct()
    want = name.strip().lower()
    return [(make, model) for make, model in pairs if camera_name(make, model).lower() == want]


def apply(qs: QuerySet[Asset], f: Filters) -> QuerySet[Asset]:
    if f.empty:
        return qs
    if f.since or f.until:
        qs = qs.annotate(local_day=_local_day())
        if f.since:
            qs = qs.filter(local_day__gte=f.since)
        if f.until:
            qs = qs.filter(local_day__lte=f.until)
    raw = MediaFile.objects.filter(asset=OuterRef("pk"), state=FileState.PRESENT, rel_path__iregex=_RAW_PATH)
    if f.media == "video":
        qs = qs.filter(kind=AssetKind.VIDEO)
    elif f.media == "photo":
        qs = qs.filter(kind=AssetKind.PHOTO)
    elif f.media == "raw":
        qs = qs.filter(Exists(raw))
    if f.camera:
        match = Q(pk__in=[])
        for make, model in _camera_pairs(qs, f.camera):
            match |= Q(camera_make=make, camera_model=model)
        qs = qs.filter(match)
    if f.tag:
        qs = qs.filter(Exists(AssetTag.objects.filter(asset=OuterRef("pk"), tag__name__iexact=f.tag.strip())))
    if f.rating:
        qs = qs.filter(rating__gte=f.rating)
    if f.favourites:
        qs = qs.filter(favourite=True)
    if f.located is True:
        qs = qs.filter(lat__isnull=False, lon__isnull=False)
    elif f.located is False:
        qs = qs.filter(Q(lat__isnull=True) | Q(lon__isnull=True))
    return qs


def facets(qs: QuerySet[Asset]) -> dict:
    """What the filters can choose from, with how many items each."""
    from library.services.browse import camera_name

    cameras: dict[str, int] = {}
    for make, model, n in (
        qs.exclude(camera_make="", camera_model="")
        .values_list("camera_make", "camera_model")
        .annotate(n=Count("pk"))
    ):
        name = camera_name(make, model)
        if name:
            cameras[name] = cameras.get(name, 0) + n
    tags = (
        Tag.objects.annotate(n=Count("asset_tags", filter=Q(asset_tags__asset__in=qs)))
        .filter(n__gt=0)
        .order_by("-n", "name")
        .values_list("name", "n")
    )
    return {
        "cameras": [{"name": k, "count": v} for k, v in sorted(cameras.items(), key=lambda kv: (-kv[1], kv[0].lower()))],
        "tags": [{"name": name, "count": n} for name, n in tags],
    }
