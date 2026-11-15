"""Detection end to end: only identical copies are duplicates (owner's rule)."""

from __future__ import annotations

import importlib
import io
import shutil
import subprocess

import pytest
from django.apps import apps
from django.conf import settings
from django.db.models import F
from PIL import Image

from core.models import AppSettings
from duplicates.models import DuplicateGroup, GroupKind, GroupState
from duplicates.services import review
from duplicates.services.fingerprint import of_pixels
from library.models import MediaFile
from library.services.scan import start_scan

from .conftest import camera, drain, exif, picture, stripped, trailer, write  # noqa: F401 - fixtures

pytestmark = pytest.mark.django_db


def scan() -> None:
    start_scan()
    drain()


def groups(state=GroupState.OPEN):
    return list(DuplicateGroup.objects.filter(state=state).order_by("pk"))


def names(group) -> list[str]:
    return sorted(MediaFile.objects.get(asset_id=a, role__in=["primary", "paired_jpeg"]).name for a in group.asset_ids)


def test_byte_identical_copies_are_an_exact_group(library, tags):
    data = picture(1)
    write(library / "2024" / "IMG_0412.jpg", data)
    write(library / "Downloads" / "IMG_0412 (1).jpg", data)
    write(library / "2024" / "other.jpg", picture(2))
    tags["IMG_0412.jpg"] = tags["IMG_0412 (1).jpg"] = camera()
    scan()

    (group,) = groups()
    assert group.kind == GroupKind.EXACT
    assert group.title == "2 copies of IMG_0412"
    assert names(group) == ["IMG_0412 (1).jpg", "IMG_0412.jpg"]


def test_the_same_pixels_with_other_metadata_are_a_pixel_group(library, tags):
    data = picture(3)
    write(library / "DSCF4821.JPG", data)
    # The same picture, its bytes changed by metadata alone (after the image).
    write(library / "Phone" / "DSCF4821.JPG", data + b"\x00other tags")
    scan()
    (group,) = groups()
    assert group.kind == GroupKind.PIXELS
    a, b = MediaFile.objects.order_by("pk")
    assert a.sha256 != b.sha256 and a.pixel_hash == b.pixel_hash


def test_look_alikes_are_never_grouped(library, tags):
    # Resized and recompressed; two quick shots 0.6 s apart.
    write(library / "big.jpg", picture(4, (1600, 1200), quality=95))
    write(library / "small.jpg", picture(4, (800, 600), quality=70))
    write(library / "IMG_4185.JPG", picture(5, quality=95))
    write(library / "IMG_4186.JPG", picture(5, quality=90))
    tags["IMG_4185.JPG"] = camera(when="2025:04:20 06:51:43", **{"EXIF:SubSecTimeOriginal": "809"})
    tags["IMG_4186.JPG"] = camera(when="2025:04:20 06:51:44", **{"EXIF:SubSecTimeOriginal": "423"})
    scan()
    assert groups() == []


def test_a_raw_and_its_jpeg_are_one_item_not_duplicates(library, tags):
    write(library / "trip" / "IMG_1.CR3", b"raw bytes")
    write(library / "trip" / "IMG_1.JPG", picture(7))
    tags["IMG_1.CR3"] = {"File:MIMEType": "image/x-canon-cr3", "Composite:ImageSize": "640 480"}
    tags["IMG_1.JPG"] = stripped()
    scan()
    assert groups() == []


def test_a_video_ffmpeg_cannot_read_matches_by_its_bytes_only(library, tags):
    write(library / "a" / "clip.mp4", b"video bytes")
    write(library / "b" / "clip.mp4", b"video bytes")
    write(library / "c" / "clip.mp4", b"video bytes, re-muxed")
    tags["clip.mp4"] = {"File:MIMEType": "video/mp4"}
    scan()
    (group,) = groups()
    assert group.kind == GroupKind.EXACT and len(group.asset_ids) == 2
    assert all(f.pixel_hash == f.sha256 for f in MediaFile.objects.all()), "no streams to hash"


def test_without_ffmpeg_videos_match_by_their_bytes(library, tags, settings, monkeypatch):
    from duplicates.services import fingerprint

    warned: list[str] = []
    monkeypatch.setattr(fingerprint.logger, "warning", lambda msg, *args: warned.append(msg % args))
    settings.FFMPEG_PATH = "definitely-not-ffmpeg-xyz"
    write(library / "clip.mov", b"video bytes")
    tags["clip.mov"] = {"File:MIMEType": "video/quicktime"}
    scan()
    clip = MediaFile.objects.get()
    assert clip.pixel_hash == clip.sha256
    assert any("ffmpeg could not be started (definitely-not-ffmpeg-xyz)" in w for w in warned)


def make_clip(path, *args: str) -> None:
    """A one-second clip with sound, from ffmpeg's test sources, or `args`
    applied to an existing one (`-i`, ...)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    source = args or (
        "-f", "lavfi", "-i", "testsrc=size=160x120:rate=10:duration=1",
        "-f", "lavfi", "-i", "sine=duration=1",
        "-c:v", "mpeg4", "-c:a", "aac", "-shortest",
    )  # fmt: skip
    subprocess.run([settings.FFMPEG_PATH, "-v", "error", "-y", *source, str(path)], check=True, timeout=60)  # noqa: S603


@pytest.mark.skipif(shutil.which(settings.FFMPEG_PATH) is None, reason="ffmpeg is not installed on this machine")
def test_videos_with_the_same_streams_are_a_pixel_group(library, tags, monkeypatch):
    from library.services import thumbnails

    monkeypatch.setattr(thumbnails, "_open_video", lambda path: Image.new("RGB", (160, 120)))
    original = library / "2024" / "trip.mov"
    make_clip(original)
    # A title and date written in; moved to MP4; re-encoded smaller.
    make_clip(library / "Edited" / "retitled.mov", "-i", str(original), "-map", "0", "-c", "copy",
              "-metadata", "title=Edited", "-metadata", "creation_time=2020-01-01T00:00:00Z")  # fmt: skip
    make_clip(library / "Export" / "remuxed.mp4", "-i", str(original), "-map", "0", "-c", "copy")
    make_clip(library / "Shared" / "smaller.mov", "-i", str(original), "-c:v", "mpeg4", "-q:v", "20", "-c:a", "copy")
    for name in ("trip.mov", "retitled.mov", "smaller.mov"):
        tags[name] = {"File:MIMEType": "video/quicktime", "Composite:ImageSize": "160 120"}
    tags["remuxed.mp4"] = {"File:MIMEType": "video/mp4", "Composite:ImageSize": "160 120"}
    scan()

    (group,) = groups()
    assert group.kind == GroupKind.PIXELS
    assert names(group) == ["remuxed.mp4", "retitled.mov", "trip.mov"]
    assert len({f.sha256 for f in MediaFile.objects.all()}) == 4


def test_the_migration_clears_video_fingerprints_to_be_worked_out_again(library, tags):
    migration = importlib.import_module("library.migrations.0011_refingerprint_videos")
    write(library / "photo.jpg", picture(16))
    write(library / "IMG_1.CR3", b"raw bytes")
    write(library / "clip.mov", b"video bytes")
    write(library / "IMG_2.JPG", b"a clip, whatever its name")
    write(library / "IMG_3.MOV", b"a Live Photo's clip")
    tags["IMG_1.CR3"] = {"File:MIMEType": "image/x-canon-cr3", "Composite:ImageSize": "640 480"}
    tags["clip.mov"] = tags["IMG_3.MOV"] = {"File:MIMEType": "video/quicktime"}
    tags["IMG_2.JPG"] = {"File:MIMEType": "video/mp4"}
    scan()
    MediaFile.objects.filter(rel_path="IMG_3.MOV").update(role="live_video")
    MediaFile.objects.exclude(rel_path="photo.jpg").update(pixel_hash=F("sha256"))  # as fingerprinted before
    before = dict(MediaFile.objects.values_list("rel_path", "pixel_hash"))

    migration.forwards(apps, None)

    after = dict(MediaFile.objects.values_list("rel_path", "pixel_hash"))
    assert after["clip.mov"] == after["IMG_2.JPG"] == ""
    assert {k: v for k, v in after.items() if v} == {k: before[k] for k in ("photo.jpg", "IMG_1.CR3", "IMG_3.MOV")}


def test_the_fingerprint_is_of_the_picture_as_shown(tmp_path):
    image = Image.new("RGB", (40, 30), (10, 20, 30))
    image.paste((200, 0, 0), (0, 0, 10, 10))
    plain, again = tmp_path / "plain.png", tmp_path / "again.png"
    image.save(plain)
    image.save(again, optimize=True, compress_level=9)  # other bytes, same pixels
    turned = tmp_path / "turned.png"
    exif_data = Image.Exif()
    exif_data[0x0112] = 6  # shown turned 90 degrees
    image.save(turned, exif=exif_data)
    assert plain.read_bytes() != again.read_bytes()
    assert of_pixels(plain) == of_pixels(again)
    assert of_pixels(plain) != of_pixels(turned)


def test_a_dismissed_group_is_not_raised_again_until_a_new_copy_joins(library, tags):
    data = picture(8)
    write(library / "a" / "x.jpg", data)
    write(library / "b" / "x.jpg", data)
    scan()
    (group,) = groups()
    review.dismiss(group.pk)
    scan()
    assert groups() == [] and groups(GroupState.DISMISSED) == [group]

    write(library / "c" / "x.jpg", data)
    scan()
    (again,) = groups()
    assert again.pk != group.pk and len(again.asset_ids) == 3


def test_a_group_whose_copy_is_gone_leaves_the_queue(library, tags):
    data = picture(10)
    write(library / "a" / "x.jpg", data)
    write(library / "b" / "x.jpg", data)
    scan()
    assert review.queue()["total"] == 1
    (library / "b" / "x.jpg").unlink()
    scan()
    assert review.queue()["total"] == 0
    assert groups() == []


def test_detection_waits_for_fingerprints_still_being_made(library, tags):
    from jobs.models import Job
    from jobs.services import Retry, enqueue

    from duplicates.services.detect import dedupe_job

    enqueue("fingerprint", {"file": 1})
    with pytest.raises(Retry):
        dedupe_job(Job(kind="dedupe", payload={}))
    Job.objects.filter(kind="fingerprint").delete()  # finished jobs are removed
    dedupe_job(Job(kind="dedupe", payload={}))  # now it runs


def test_editing_a_copy_keeps_its_group(library, exif):
    """Its metadata changes, its pixels do not: still the same picture."""
    from fileops.services import ops

    data = picture(13)
    write(library / "a" / "x.jpg", data)
    write(library / "b" / "x.jpg", data)
    scan()
    (group,) = groups()
    edited = MediaFile.objects.get(rel_path="b/x.jpg")
    ops.edit([edited.asset_id], fields={"title": "Edited here"})
    drain()
    (after,) = groups()
    assert after.pk == group.pk and after.kind == GroupKind.PIXELS


def test_files_indexed_before_fingerprints_existed_are_backfilled(library, tags):
    from duplicates.services.detect import schedule_due

    write(library / "a.jpg", picture(11))
    write(library / "clip.mp4", b"video")
    tags["clip.mp4"] = {"File:MIMEType": "video/mp4"}
    scan()
    MediaFile.objects.update(pixel_hash="")
    assert schedule_due() == 2
    drain()
    photo, clip = MediaFile.objects.get(rel_path="a.jpg"), MediaFile.objects.get(rel_path="clip.mp4")
    assert len(photo.pixel_hash) == 64 and photo.pixel_hash != photo.sha256
    assert clip.pixel_hash == clip.sha256


def test_auto_merge_stays_with_exact_copies(library, tags):
    AppSettings.objects.update_or_create(pk=1, defaults={"auto_merge_exact": True})
    data = picture(15)
    write(library / "a" / "x.jpg", data)
    write(library / "b" / "x.jpg", data + b"\x00tags")
    scan()
    (group,) = groups()
    assert group.kind == GroupKind.PIXELS, "not merged by itself: only byte-identical copies are"
