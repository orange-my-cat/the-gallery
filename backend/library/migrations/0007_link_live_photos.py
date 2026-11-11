"""Find the Live Photos already in the library and make each one item.

Until now a Live Photo's still and clip were two items, side by side in the
timeline. This fills `live_id` from the tags ExifTool already read (no file is
opened), then folds each clip into its still under the role `live_video`, by
the same rules the indexer now follows (`library.services.live`): within one
folder, by identifier, or else by name for a clip of 6 seconds or less; never
a clip the owner has made something of (album, tag, rating, edit).

The rules are restated here rather than imported, so this migration keeps
meaning what it meant if the service changes later.
"""

from collections import Counter, defaultdict

from django.db import migrations
from django.db.models import Q

LIVE_ID_TAGS = (
    "QuickTime:ContentIdentifier",
    "Keys:ContentIdentifier",
    "MakerNotes:MediaGroupUUID",
    "MakerNotes:ContentIdentifier",
)
PHOTO = {"jpg", "jpeg", "png", "heic", "heif", "webp", "tif", "tiff", "gif"}
RAW = {"cr2", "cr3", "nef", "arw", "raf", "orf", "rw2", "dng"}
VIDEO = {"mp4", "mov", "m4v", "mkv", "avi", "3gp"}
LIVE_MAX_SECONDS = 6.0
BATCH = 1000


def _kind(rel, mime):
    name = rel.rsplit("/", 1)[-1]
    ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
    if ext in RAW:
        return "raw"
    if ext not in PHOTO and ext not in VIDEO:
        return None
    if mime.startswith("video/"):
        return "video"
    if mime.startswith("image/"):
        return "photo"
    return "video" if ext in VIDEO else "photo"


def _live_id(raw):
    for key in LIVE_ID_TAGS:
        value = raw.get(key)
        if value is not None and str(value).strip():
            return str(value).strip().upper()[:64]
    return ""


def _folder(rel):
    return rel.rpartition("/")[0]


def fill_live_ids(MediaFile):
    changed = []
    files = MediaFile.objects.filter(metadata__isnull=False).select_related("metadata")
    for f in files.iterator(chunk_size=BATCH):
        live_id = _live_id(f.metadata.raw or {})
        if live_id != f.live_id:
            f.live_id = live_id
            changed.append(f)
        if len(changed) >= BATCH:
            MediaFile.objects.bulk_update(changed, ["live_id"])
            changed = []
    if changed:
        MediaFile.objects.bulk_update(changed, ["live_id"])


def link(MediaFile, Asset, Job):
    files = list(
        MediaFile.objects.exclude(state="trashed").values(
            "pk", "asset_id", "rel_path", "mime", "role", "live_id", "pair_key", "asset__duration"
        )
    )
    per_asset = Counter(f["asset_id"] for f in files)
    touched = set(
        Asset.objects.filter(
            Q(favourite=True)
            | Q(rating__gt=0)
            | Q(date_edited=True)
            | ~Q(title="")
            | ~Q(description="")
            | Q(asset_tags__isnull=False)
            | Q(album_items__isnull=False)
        ).values_list("pk", flat=True)
    )
    has_live = {f["asset_id"] for f in files if f["role"] == "live_video"}

    by_id = defaultdict(list)
    by_key = defaultdict(list)
    clips = []
    for f in files:
        kind = _kind(f["rel_path"], f["mime"])
        if f["role"] != "live_video" and kind in ("photo", "raw"):
            if f["live_id"]:
                by_id[(_folder(f["rel_path"]), f["live_id"])].append(f)
            by_key[f["pair_key"]].append(f)
        elif (
            kind == "video"
            and f["role"] == "primary"
            and per_asset[f["asset_id"]] == 1
            and f["asset_id"] not in touched
        ):
            clips.append(f)

    def belong(still, clip):
        if still["live_id"] and clip["live_id"]:
            return still["live_id"] == clip["live_id"]
        seconds = clip["asset__duration"]
        return still["pair_key"] == clip["pair_key"] and seconds is not None and 0 < seconds <= LIVE_MAX_SECONDS

    for clip in sorted(clips, key=lambda f: f["pk"]):
        candidates = by_key.get(clip["pair_key"], [])
        if clip["live_id"]:
            candidates = by_id.get((_folder(clip["rel_path"]), clip["live_id"]), []) + candidates
        stills = [s for s in candidates if s["asset_id"] not in has_live and belong(s, clip)]
        if not stills:
            continue
        still = min(stills, key=lambda s: (s["pair_key"] != clip["pair_key"], s["pk"]))
        MediaFile.objects.filter(pk=clip["pk"]).update(asset_id=still["asset_id"], role="live_video")
        Job.objects.filter(
            kind__in=["thumb_small", "thumb_large"], state__in=["queued", "failed"], payload__file=clip["pk"]
        ).delete()
        Asset.objects.filter(pk=clip["asset_id"]).delete()
        has_live.add(still["asset_id"])


def forwards(apps, schema_editor):
    MediaFile = apps.get_model("library", "MediaFile")
    fill_live_ids(MediaFile)
    link(MediaFile, apps.get_model("library", "Asset"), apps.get_model("jobs", "Job"))


class Migration(migrations.Migration):
    dependencies = [
        ("jobs", "0002_worker_beat"),
        ("organise", "0001_initial"),
        ("library", "0006_live_photos"),
    ]

    operations = [migrations.RunPython(forwards, migrations.RunPython.noop)]
