"""Detection end to end: only identical copies are duplicates (owner's rule)."""

from __future__ import annotations

import io

import pytest
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


def test_raw_and_video_match_by_their_bytes_only(library, tags):
    write(library / "a" / "clip.mp4", b"video bytes")
    write(library / "b" / "clip.mp4", b"video bytes")
    write(library / "c" / "clip.mp4", b"video bytes, re-muxed")
    tags["clip.mp4"] = {"File:MIMEType": "video/mp4"}
    scan()
    (group,) = groups()
    assert group.kind == GroupKind.EXACT and len(group.asset_ids) == 2
    assert all(f.pixel_hash == f.sha256 for f in MediaFile.objects.all()), "never decoded"


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
