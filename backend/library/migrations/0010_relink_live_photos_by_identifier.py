"""Move each Live Photo clip that was paired by name onto the still its identifier names.

Pairing used to accept a same-named still with no identifier even when the
clip's own still - the one carrying the clip's identifier - was in the folder
too. A folder gathering several phones' imports holds unrelated `IMG_0904`s,
and some export tools number a pair's halves one apart, so clips played over
the wrong photos. The indexer now lets the identifier win
(`library.services.live`); this repairs what it already did.

A clip moves when its still lacks the clip's identifier and a still in the
same folder carries it. Every such clip is lifted first, so a chain of
off-by-one pairs settles whatever the order, and each is then placed on a
still with its identifier and no clip - same name first, then the oldest. A
clip with nowhere free to go stays where it is. No item is created or
deleted: the photo a clip leaves keeps its own still.

The rules are restated here rather than imported, so this migration keeps
meaning what it meant if the service changes later.
"""

from collections import defaultdict

from django.db import migrations

PHOTO = {"jpg", "jpeg", "png", "heic", "heif", "webp", "tif", "tiff", "gif"}
RAW = {"cr2", "cr3", "nef", "arw", "raf", "orf", "rw2", "dng"}


def _is_still(rel, mime):
    name = rel.rsplit("/", 1)[-1]
    ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
    if ext in RAW:
        return True
    if ext not in PHOTO and ext not in {"mp4", "mov", "m4v", "mkv", "avi", "3gp"}:
        return False
    if mime.startswith("video/"):
        return False
    if mime.startswith("image/"):
        return True
    return ext in PHOTO


def _folder(rel):
    return rel.rpartition("/")[0]


def relink(MediaFile):
    files = list(
        MediaFile.objects.exclude(state="trashed").values(
            "pk", "asset_id", "rel_path", "mime", "role", "live_id", "pair_key"
        )
    )
    stills_by_id = defaultdict(list)
    ids_on_asset = defaultdict(set)
    clip_on = {}
    for f in files:
        if f["role"] == "live_video":
            clip_on[f["asset_id"]] = f
        elif f["live_id"] and _is_still(f["rel_path"], f["mime"]):
            stills_by_id[(_folder(f["rel_path"]), f["live_id"])].append(f)
            ids_on_asset[f["asset_id"]].add(f["live_id"])

    misplaced = [
        clip
        for clip in clip_on.values()
        if clip["live_id"]
        and clip["live_id"] not in ids_on_asset[clip["asset_id"]]
        and stills_by_id.get((_folder(clip["rel_path"]), clip["live_id"]))
    ]
    for clip in misplaced:
        del clip_on[clip["asset_id"]]

    for clip in sorted(misplaced, key=lambda f: f["pk"]):
        free = [
            s
            for s in stills_by_id[(_folder(clip["rel_path"]), clip["live_id"])]
            if s["asset_id"] not in clip_on
        ]
        if not free:
            clip_on.setdefault(clip["asset_id"], clip)  # nowhere better: stays
            continue
        still = min(free, key=lambda s: (s["pair_key"] != clip["pair_key"], s["pk"]))
        MediaFile.objects.filter(pk=clip["pk"]).update(asset_id=still["asset_id"])
        clip_on[still["asset_id"]] = clip


def forwards(apps, schema_editor):
    relink(apps.get_model("library", "MediaFile"))


class Migration(migrations.Migration):
    dependencies = [
        ("library", "0009_remove_mediafile_phash_mediafile_pixel_hash"),
    ]

    operations = [migrations.RunPython(forwards, migrations.RunPython.noop)]
