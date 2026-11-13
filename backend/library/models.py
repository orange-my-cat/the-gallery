"""What the owner sees (Asset) kept apart from the files on disk (MediaFile).

Albums, tags, ratings and people attach to the Asset, so a RAW+JPEG pair is one
record and a merge only has to move files under one Asset (HLD, "Data model").

Paths are stored relative to LIBRARY_ROOT with forward slashes, so the same
rows mean the same files on Windows in development and Linux in production.
"""

from __future__ import annotations

from django.db import models


class AssetKind(models.TextChoices):
    PHOTO = "photo"
    VIDEO = "video"


class CaptureSource(models.TextChoices):
    """Where `capture_at` came from - shown in the info panel, and the reason
    an oddly dated photo is where it is."""

    METADATA = "metadata"  # a capture date tag in the file
    FILE_NAME = "file_name"  # no tag; a date spelled in the name (IMG_20170608_084734.jpg)
    # Neither; the dated folder above it (`180221 Penang`). Approximate: a
    # trip's first day or a whole month/year, never the photo's own date.
    FOLDER = "folder"
    FILE_TIME = "file_time"  # none of these; the modification time


class Asset(models.Model):
    kind = models.CharField(max_length=8, choices=AssetKind.choices)
    # The instant of capture. When the file states no offset, the wall-clock
    # time is read in the application's TIME_ZONE and `capture_offset` is null.
    capture_at = models.DateTimeField()
    # Minutes east of UTC at the place of capture, when the file says.
    capture_offset = models.SmallIntegerField(null=True, blank=True)
    capture_source = models.CharField(max_length=16, choices=CaptureSource.choices)
    # The owner set the date by hand (ORG-04); shown as "edited".
    date_edited = models.BooleanField(default=False)

    lat = models.FloatField(null=True, blank=True)
    lon = models.FloatField(null=True, blank=True)
    # A place name the file states (XMP Location) or the owner typed.
    place = models.CharField(max_length=255, blank=True, default="")
    # MET-03: the nearest town to lat/lon, worked out by the worker from the
    # GeoNames list. Shown when the file (or the owner) names no place itself.
    geo_place = models.ForeignKey("places.Place", null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    # False until the position has been looked up; cleared when it changes.
    geocoded = models.BooleanField(default=False)

    title = models.CharField(max_length=500, blank=True, default="")
    description = models.TextField(blank=True, default="")
    rating = models.PositiveSmallIntegerField(default=0)
    favourite = models.BooleanField(default=False)

    # Normalised for the info panel (BRW-05). The raw tags are on FileMetadata.
    camera_make = models.CharField(max_length=128, blank=True, default="")
    camera_model = models.CharField(max_length=128, blank=True, default="")
    lens = models.CharField(max_length=255, blank=True, default="")
    exposure_time = models.FloatField(null=True, blank=True)  # seconds
    f_number = models.FloatField(null=True, blank=True)
    iso = models.PositiveIntegerField(null=True, blank=True)
    focal_length = models.FloatField(null=True, blank=True)  # mm
    duration = models.FloatField(null=True, blank=True)  # seconds, video only

    # The file shown for this asset: the JPEG of a RAW+JPEG pair.
    primary_file = models.ForeignKey(
        "MediaFile", null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [
            # The timeline's cursor, newest first (BRW-01).
            models.Index(fields=["-capture_at", "-id"], name="asset_timeline_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.kind} {self.pk} @ {self.capture_at:%Y-%m-%d}"


class FileRole(models.TextChoices):
    PRIMARY = "primary"  # an ordinary file, alone in its asset
    RAW = "raw"  # the RAW half of a RAW+JPEG pair
    PAIRED_JPEG = "paired_jpeg"  # the JPEG half, which the asset displays
    # A Live Photo's motion clip. The asset is the photo, shown through its
    # still; the clip plays over it in the viewer and is never a tile of its own.
    LIVE_VIDEO = "live_video"


class FileState(models.TextChoices):
    PRESENT = "present"
    # Not seen by the last scan(s). Never deleted: it may come back, or turn
    # out to have moved (LIB-04).
    MISSING = "missing"
    TRASHED = "trashed"  # Phase 1b


class MediaFile(models.Model):
    asset = models.ForeignKey(Asset, on_delete=models.CASCADE, related_name="files")
    rel_path = models.TextField(unique=True)
    # Lower-cased folder + stem, for finding a RAW's JPEG partner (LIB-05).
    pair_key = models.TextField(db_index=True)
    role = models.CharField(max_length=16, choices=FileRole.choices, default=FileRole.PRIMARY)
    # Apple's Live Photo identifier, in both the still and its clip; how the
    # two are found for each other whatever they are named.
    live_id = models.CharField(max_length=64, blank=True, default="", db_index=True)

    size = models.BigIntegerField()
    # Nanoseconds, exactly as os.stat reports it: an unchanged file must
    # compare equal on every scan, so no rounding through a datetime.
    mtime_ns = models.BigIntegerField()
    sha256 = models.CharField(max_length=64, db_index=True)
    phash = models.BigIntegerField(null=True, blank=True)  # Phase 1c

    width = models.PositiveIntegerField(null=True, blank=True)
    height = models.PositiveIntegerField(null=True, blank=True)
    mime = models.CharField(max_length=100, blank=True, default="")

    state = models.CharField(max_length=16, choices=FileState.choices, default=FileState.PRESENT)
    # Consecutive scans that have not found the file. At 2 it drops out of views.
    missed_scans = models.PositiveSmallIntegerField(default=0)
    # While trashed (FOP-02): `rel_path` is where it sits inside the trash
    # folder, and these say where it came from, when, and by which operation.
    trashed_from = models.TextField(blank=True, default="")
    trashed_at = models.DateTimeField(null=True, blank=True)
    trash_operation = models.ForeignKey(
        "fileops.Operation", null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    last_seen_scan = models.ForeignKey(
        "ScanRun", null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [models.Index(fields=["state", "missed_scans"], name="mediafile_state_idx")]

    def __str__(self) -> str:
        return self.rel_path

    @property
    def name(self) -> str:
        return self.rel_path.rsplit("/", 1)[-1]


class FileMetadata(models.Model):
    """ExifTool's full output for a file, kept so merges lose nothing (MET-01)."""

    file = models.OneToOneField(MediaFile, on_delete=models.CASCADE, related_name="metadata")
    raw = models.JSONField()
    extracted_at = models.DateTimeField(auto_now=True)


class ScanState(models.TextChoices):
    QUEUED = "queued"
    WALKING = "walking"
    INDEXING = "indexing"  # walk done; the index jobs it queued are running
    DONE = "done"
    FAILED = "failed"


ACTIVE_SCAN_STATES = (ScanState.QUEUED, ScanState.WALKING, ScanState.INDEXING)


class ScanTrigger(models.TextChoices):
    SCHEDULE = "schedule"
    MANUAL = "manual"


class ScanRun(models.Model):
    trigger = models.CharField(max_length=16, choices=ScanTrigger.choices)
    state = models.CharField(max_length=16, choices=ScanState.choices, default=ScanState.QUEUED)
    # True when the library had no files before this scan: the timeline shows
    # the "first scan" banner and fills in as photos are found.
    first_scan = models.BooleanField(default=False)
    started_at = models.DateTimeField(auto_now_add=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    error = models.TextField(blank=True, default="")

    # Counts (LIB-06). `seen` is every supported file the walk found, and
    # `queued` those it handed to index_file.
    seen = models.PositiveIntegerField(default=0)
    queued = models.PositiveIntegerField(default=0)
    indexed = models.PositiveIntegerField(default=0)
    new = models.PositiveIntegerField(default=0)
    changed = models.PositiveIntegerField(default=0)
    moved = models.PositiveIntegerField(default=0)
    missing = models.PositiveIntegerField(default=0)

    class Meta:
        constraints = [
            # One scan at a time, however many workers and buttons ask.
            # A unique index on a constant: at most one row may match.
            models.UniqueConstraint(
                models.Value(1),
                condition=models.Q(state__in=["queued", "walking", "indexing"]),
                name="scanrun_one_active",
            ),
        ]

    def __str__(self) -> str:
        return f"scan {self.pk} ({self.state})"
