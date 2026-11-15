"""Clear the fingerprint of every video so it is worked out again from its streams.

A video's fingerprint used to be its byte hash; it is now the hash of its
encoded streams (`duplicates.services.fingerprint`), so a clip with other
metadata, or moved from .MOV to .MP4, is found as a copy. Emptied, the
duplicate scheduler's backfill picks each one up in the background; until
then a video still matches its byte-identical copies.

A Live Photo's clip keeps its byte hash, as before. What counts as a video is
restated here rather than imported, so this migration keeps meaning what it
meant if the service changes later: by the extension the library accepts, then
by the MIME type ExifTool read; a RAW is never one.
"""

from django.db import migrations
from django.db.models import F

PHOTO = {"jpg", "jpeg", "png", "heic", "heif", "webp", "tif", "tiff", "gif"}
VIDEO = {"mp4", "mov", "m4v", "mkv", "avi", "3gp"}


def _is_video(rel, mime):
    name = rel.rsplit("/", 1)[-1]
    ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
    if ext not in PHOTO and ext not in VIDEO:
        return False  # a RAW, or a file the library ignores
    if mime.startswith("video/"):
        return True
    if mime.startswith("image/"):
        return False
    return ext in VIDEO


def forwards(apps, schema_editor):
    MediaFile = apps.get_model("library", "MediaFile")
    candidates = (
        MediaFile.objects.exclude(role="live_video")
        .filter(pixel_hash=F("sha256"))
        .values_list("pk", "rel_path", "mime")
    )
    videos = [pk for pk, rel, mime in candidates.iterator() if _is_video(rel, mime)]
    for start in range(0, len(videos), 5000):
        MediaFile.objects.filter(pk__in=videos[start : start + 5000]).update(pixel_hash="")


class Migration(migrations.Migration):
    dependencies = [
        ("library", "0010_relink_live_photos_by_identifier"),
    ]

    # Backwards: an empty fingerprint is simply worked out again.
    operations = [migrations.RunPython(forwards, migrations.RunPython.noop)]
