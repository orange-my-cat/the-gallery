"""Give every single-file asset the kind its file actually holds.

Until now the kind came from the extension alone. Live Photo pairs renamed by
a tool that swapped the halves' extensions (the clip in `IMG_1234.JPG`, the
still in `IMG_1234.MOV`) were indexed backwards: the clips failed every
thumbnail as photos, and the stills were offered as videos. The indexer now
asks ExifTool's MIMEType; this brings the existing rows into line, and gives
the corrected clips' failed thumbnails a fresh set of attempts.

Single files only (role `primary`): a RAW+JPEG pair is a photo by
construction. RAWs are left alone - their MIME type is an image one anyway.

Also collapses the failed `index_file` jobs that piled up one per scan for
the same unreadable file, keeping the newest - the walk now replaces them as
it goes, so this only tidies what is already there.
"""

from django.db import migrations
from django.db.models import F
from django.utils import timezone

RAW_EXTENSIONS = {"cr2", "cr3", "nef", "arw", "raf", "orf", "rw2", "dng"}


def _extension(rel_path):
    name = rel_path.rsplit("/", 1)[-1]
    return name.rsplit(".", 1)[-1].lower() if "." in name else ""


def forwards(apps, schema_editor):
    Asset = apps.get_model("library", "Asset")
    MediaFile = apps.get_model("library", "MediaFile")
    Job = apps.get_model("jobs", "Job")

    singles = MediaFile.objects.filter(role="primary", asset__primary_file=F("pk"))
    to_video = [
        (m.asset_id, m.pk)
        for m in singles.filter(mime__startswith="video/", asset__kind="photo").only("pk", "asset_id", "rel_path")
        if _extension(m.rel_path) not in RAW_EXTENSIONS
    ]
    to_photo = [
        m.asset_id
        for m in singles.filter(mime__startswith="image/", asset__kind="video").only("asset_id", "rel_path")
        if _extension(m.rel_path) not in RAW_EXTENSIONS
    ]
    Asset.objects.filter(pk__in=[a for a, _ in to_video]).update(kind="video")
    Asset.objects.filter(pk__in=to_photo).update(kind="photo")

    # The clips' thumbnails failed only because they were opened as photos.
    clip_files = [f for _, f in to_video]
    Job.objects.filter(
        kind__in=["thumb_small", "thumb_large"], state="failed", payload__file__in=clip_files
    ).update(state="queued", attempts=0, error="", run_after=timezone.now(), locked_at=None, locked_by="")

    seen = set()
    duplicates = []
    for pk, path in (
        Job.objects.filter(kind="index_file", state="failed").order_by("-id").values_list("pk", "payload__path")
    ):
        if path in seen:
            duplicates.append(pk)
        seen.add(path)
    Job.objects.filter(pk__in=duplicates).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("jobs", "0002_worker_beat"),
        ("library", "0004_asset_date_edited_mediafile_trash_operation_and_more"),
    ]

    operations = [migrations.RunPython(forwards, migrations.RunPython.noop)]
