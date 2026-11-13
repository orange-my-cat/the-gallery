"""Scanning end to end: walk, index, thumbnails, reconcile (LIB-02 to LIB-06)."""

from __future__ import annotations

import shutil
from datetime import timedelta

import pytest
from django.utils import timezone

from core.models import AppSettings
from jobs.models import Job, JobState
from library.models import Asset, FileRole, FileState, MediaFile, ScanRun, ScanState, ScanTrigger
from library.services import metadata
from library.services import scan as scan_service
from library.services.scan import schedule_due, start_scan
from library.services.thumbnails import cache_path

from .conftest import drain, jpeg_bytes, write

pytestmark = pytest.mark.django_db


def scan() -> ScanRun:
    run = start_scan()
    drain()
    run.refresh_from_db()
    return run


def test_first_scan_indexes_every_supported_file(library, tags):
    write(library / "2024" / "a.jpg", jpeg_bytes(color=(1, 2, 3)))
    write(library / "2024" / "b.png", jpeg_bytes(color=(4, 5, 6)))  # content does not matter to the walk
    write(library / "2024" / "notes.txt", b"not media")
    write(library / "2024" / "._a.jpg", b"macOS resource fork")
    tags["a.jpg"] = {
        "File:MIMEType": "image/jpeg",
        "EXIF:DateTimeOriginal": "2024:03:01 10:00:00",
        "EXIF:OffsetTimeOriginal": "+08:00",
        "Composite:ImageSize": "640 480",
        "EXIF:Make": "Canon",
    }

    run = scan()

    assert run.state == ScanState.DONE
    assert run.first_scan is True
    assert (run.seen, run.queued, run.indexed, run.new) == (2, 2, 2, 2)
    assert sorted(MediaFile.objects.values_list("rel_path", flat=True)) == ["2024/a.jpg", "2024/b.png"]
    a = MediaFile.objects.get(rel_path="2024/a.jpg")
    assert a.asset.primary_file_id == a.pk
    assert a.asset.camera_make == "Canon"
    assert a.asset.capture_offset == 480
    assert (a.width, a.height) == (640, 480)
    assert a.metadata.raw["EXIF:Make"] == "Canon"
    assert cache_path(a.sha256, "small").is_file()
    assert cache_path(a.sha256, "large").is_file()
    assert not Job.objects.exists(), "finished jobs are deleted"


def test_a_rescan_of_an_unchanged_library_indexes_nothing(library, tags):
    write(library / "a.jpg", jpeg_bytes())
    scan()

    second = scan()

    assert second.first_scan is False
    assert (second.seen, second.queued, second.indexed) == (1, 0, 0)
    assert MediaFile.objects.get().last_seen_scan_id == second.pk


def test_a_changed_file_is_reindexed_and_keeps_its_asset(library, tags):
    path = write(library / "a.jpg", jpeg_bytes(color=(1, 1, 1)), mtime_ns=1_700_000_000_000_000_000)
    scan()
    before = MediaFile.objects.get()
    tags["a.jpg"] = {"XMP:Title": "Edited elsewhere"}

    write(path, jpeg_bytes(color=(9, 9, 9)), mtime_ns=1_700_000_100_000_000_000)
    run = scan()

    after = MediaFile.objects.get()
    assert run.changed == 1
    assert after.asset_id == before.asset_id
    assert after.sha256 != before.sha256
    assert after.asset.title == "Edited elsewhere"


def test_a_touched_but_identical_file_is_not_counted_as_changed(library, tags):
    path = write(library / "a.jpg", jpeg_bytes(), mtime_ns=1_700_000_000_000_000_000)
    scan()
    write(path, path.read_bytes(), mtime_ns=1_700_000_500_000_000_000)

    run = scan()

    assert (run.queued, run.indexed, run.changed) == (1, 1, 0)


def test_a_file_moved_outside_the_app_keeps_its_asset(library, tags):
    """LIB-04: albums, tags and people hang off the asset, so it must survive."""
    write(library / "inbox" / "a.jpg", jpeg_bytes())
    scan()
    asset = Asset.objects.get()
    asset.favourite = True
    asset.save()

    (library / "2024").mkdir()
    shutil.move(library / "inbox" / "a.jpg", library / "2024" / "renamed.jpg")
    run = scan()

    media = MediaFile.objects.get()
    assert run.moved == 1 and run.new == 0 and run.missing == 0
    assert media.rel_path == "2024/renamed.jpg"
    assert media.asset_id == asset.pk
    assert Asset.objects.get().favourite is True


def test_a_copy_is_a_new_asset_not_a_move(library, tags):
    write(library / "a.jpg", jpeg_bytes())
    scan()
    shutil.copy(library / "a.jpg", library / "copy.jpg")

    run = scan()

    assert run.new == 1 and run.moved == 0
    assert Asset.objects.count() == 2


def test_a_removed_file_goes_missing_and_is_never_deleted(library, tags):
    write(library / "a.jpg", jpeg_bytes())
    scan()
    (library / "a.jpg").unlink()

    first = scan()
    media = MediaFile.objects.get()
    assert first.missing == 1
    assert (media.state, media.missed_scans) == (FileState.MISSING, 1)

    second = scan()
    media.refresh_from_db()
    assert second.missing == 0, "only newly missing files are counted"
    assert media.missed_scans == 2

    write(library / "a.jpg", jpeg_bytes())
    scan()
    media.refresh_from_db()
    assert (media.state, media.missed_scans) == (FileState.PRESENT, 0)


def _during_walk(monkeypatch, action) -> None:
    """Run `action` mid-scan: after the walk read the known files and listed
    the folders, before it writes back what it saw."""
    real = scan_service._walk

    def walk(*args, **kwargs):
        found = list(real(*args, **kwargs))
        action()
        yield from found

    monkeypatch.setattr(scan_service, "_walk", walk)


def test_a_file_sent_to_trash_during_a_scan_stays_in_trash(library, tags, monkeypatch):
    write(library / "trip" / "a.jpg", jpeg_bytes())
    scan()
    media = MediaFile.objects.get()

    def trash():
        # What the Trash step leaves behind, written after the walk read `known`
        # but with the file still listed at its old path.
        MediaFile.objects.filter(pk=media.pk).update(
            state=FileState.TRASHED, rel_path=".gallery-trash/1/trip/a.jpg", trashed_from="trip/a.jpg"
        )

    _during_walk(monkeypatch, trash)
    scan()
    media.refresh_from_db()
    assert media.state == FileState.TRASHED


def test_a_file_restored_during_a_scan_is_not_marked_missing(library, tags, monkeypatch):
    write(library / "trip" / "a.jpg", jpeg_bytes())
    scan()
    media = MediaFile.objects.get()
    trashed = write(library / ".gallery-trash" / "1" / "trip" / "a.jpg", (library / "trip" / "a.jpg").read_bytes())
    (library / "trip" / "a.jpg").unlink()
    MediaFile.objects.filter(pk=media.pk).update(
        state=FileState.TRASHED, rel_path=".gallery-trash/1/trip/a.jpg", trashed_from="trip/a.jpg"
    )

    def restore():
        # Put back after the walk had already listed trip/ without it.
        shutil.move(trashed, library / "trip" / "a.jpg")
        restored = MediaFile.objects.get(pk=media.pk)
        restored.state, restored.rel_path, restored.trashed_from = FileState.PRESENT, "trip/a.jpg", ""
        restored.save()

    _during_walk(monkeypatch, restore)
    run = scan()
    media.refresh_from_db()
    assert run.missing == 0
    assert media.state == FileState.PRESENT


@pytest.mark.parametrize("raw_first", [True, False])
def test_raw_and_jpeg_with_one_name_are_one_asset(library, tags, raw_first):
    """LIB-05, whichever half the queue reaches first."""
    raw_path = library / "trip" / "IMG_0001.CR3"
    jpg_path = library / "trip" / "img_0001.JPG"
    tags["img_0001.JPG"] = {"XMP:Title": "From the JPEG"}
    tags["IMG_0001.CR3"] = {"XMP:Title": "From the RAW", "File:MIMEType": "image/x-canon-cr3"}
    first, second = (raw_path, jpg_path) if raw_first else (jpg_path, raw_path)
    write(first, b"RAW-or-JPEG-1" if first == raw_path else jpeg_bytes())
    scan()
    write(second, b"RAW-or-JPEG-1" if second == raw_path else jpeg_bytes())
    scan()

    asset = Asset.objects.get()
    roles = dict(asset.files.values_list("rel_path", "role"))
    assert roles == {"trip/IMG_0001.CR3": FileRole.RAW, "trip/img_0001.JPG": FileRole.PAIRED_JPEG}
    assert asset.primary_file.rel_path == "trip/img_0001.JPG"
    assert asset.title == "From the JPEG"


def test_a_png_does_not_pair_with_a_raw(library, tags):
    write(library / "x.png", jpeg_bytes())
    write(library / "x.nef", b"raw")
    scan()
    assert Asset.objects.count() == 2


def test_files_with_the_wrong_extension_are_read_by_content(library, tags, monkeypatch):
    """Seen in the wild: a renaming tool put clips in .JPG and stills in .MOV.
    (Two such halves with one name are a Live Photo - see test_live.)"""
    from library.services import thumbnails

    videos = []

    def fake_video(path):
        videos.append(path.name)
        return thumbnails.Image.new("RGB", (1920, 1440))

    monkeypatch.setattr(thumbnails, "_open_video", fake_video)
    tags["IMG_2894.JPG"] = {"File:MIMEType": "video/quicktime", "QuickTime:Duration": 2.9}
    tags["IMG_2899.MOV"] = {"File:MIMEType": "image/jpeg"}
    write(library / "IMG_2894.JPG", b"\x00\x00\x00\x14ftypqt   a clip, whatever its name")
    write(library / "IMG_2899.MOV", jpeg_bytes())  # a real JPEG: PIL must be the one to open it

    scan()

    kinds = {m.name: m.asset.kind for m in MediaFile.objects.select_related("asset")}
    assert kinds == {"IMG_2894.JPG": "video", "IMG_2899.MOV": "photo"}
    assert videos == ["IMG_2894.JPG", "IMG_2894.JPG"]  # small and large, both through ffmpeg
    assert not Job.objects.filter(state=JobState.FAILED).exists()
    for media in MediaFile.objects.all():
        assert cache_path(media.sha256, "small").is_file()


def test_a_clip_named_jpg_does_not_pair_with_a_raw(library, tags, monkeypatch):
    from library.services import thumbnails

    monkeypatch.setattr(thumbnails, "_open_video", lambda path: thumbnails.Image.new("RGB", (64, 48)))
    tags["IMG_7.JPG"] = {"File:MIMEType": "video/quicktime"}
    tags["IMG_7.CR3"] = {"File:MIMEType": "image/x-canon-cr3"}
    write(library / "IMG_7.JPG", b"\x00\x00\x00\x14ftypqt  ")
    write(library / "IMG_7.CR3", b"raw")
    scan()
    assert sorted(Asset.objects.values_list("kind", flat=True)) == ["photo", "video"]
    assert set(MediaFile.objects.values_list("role", flat=True)) == {FileRole.PRIMARY}


def test_a_file_that_never_indexes_counts_as_one_failure_however_many_scans(library, tags, monkeypatch):
    write(library / "good.jpg", jpeg_bytes())
    write(library / "empty.MOV", b"")
    real_read = metadata.read

    def read(path):
        if path.name == "empty.MOV":
            raise RuntimeError("execute returned a non-zero exit status: 1")
        return real_read(path)

    monkeypatch.setattr(metadata, "read", read)
    for _ in range(3):
        scan()

    failed = Job.objects.filter(state=JobState.FAILED)
    assert [(j.kind, j.payload["path"]) for j in failed] == [("index_file", "empty.MOV")]


def test_migration_gives_misfiled_assets_their_real_kind(library, tags, monkeypatch):
    import importlib

    from django.apps import apps

    from library.services import thumbnails

    migration = importlib.import_module("library.migrations.0005_asset_kind_from_content")
    tags["clip.JPG"] = {"File:MIMEType": "video/quicktime"}
    tags["still.MOV"] = {"File:MIMEType": "image/jpeg"}
    tags["IMG_1.CR3"] = {"File:MIMEType": "image/x-canon-cr3"}
    write(library / "clip.JPG", b"\x00\x00\x00\x14ftypqt  ")
    write(library / "still.MOV", jpeg_bytes(color=(1, 1, 1)))
    write(library / "IMG_1.CR3", b"raw")
    write(library / "IMG_1.JPG", jpeg_bytes(color=(2, 2, 2)))
    monkeypatch.setattr(thumbnails, "_open_video", lambda path: thumbnails.Image.new("RGB", (64, 48)))
    scan()
    clip = MediaFile.objects.get(rel_path="clip.JPG")
    # As the old, extension-only indexer left them, with the clip's thumbnails failed.
    Asset.objects.filter(pk=clip.asset_id).update(kind="photo")
    Asset.objects.filter(files__rel_path="still.MOV").update(kind="video")
    for kind in ("thumb_small", "thumb_large"):
        Job.objects.create(kind=kind, payload={"file": clip.pk}, state=JobState.FAILED, attempts=3,
                           error="PIL.UnidentifiedImageError", run_after=timezone.now())
    for _ in range(3):
        Job.objects.create(kind="index_file", payload={"path": "empty.MOV"}, state=JobState.FAILED,
                           attempts=3, run_after=timezone.now())

    migration.forwards(apps, None)

    kinds = {m.rel_path: m.asset.kind for m in MediaFile.objects.select_related("asset")}
    assert kinds == {"clip.JPG": "video", "still.MOV": "photo", "IMG_1.CR3": "photo", "IMG_1.JPG": "photo"}
    thumbs = Job.objects.filter(kind__startswith="thumb")
    assert {(j.state, j.attempts) for j in thumbs} == {(JobState.QUEUED, 0)}
    assert Job.objects.filter(kind="index_file").count() == 1


def test_exclusions_and_the_trash_are_not_scanned(library, tags):
    write(library / "keep" / "a.jpg", jpeg_bytes(color=(1, 0, 0)))
    write(library / "Exports" / "b.jpg", jpeg_bytes(color=(0, 1, 0)))
    write(library / ".gallery-trash" / "7" / "c.jpg", jpeg_bytes(color=(0, 0, 1)))
    AppSettings.objects.update_or_create(pk=1, defaults={"exclusions": ["exports"]})

    run = scan()

    assert run.seen == 1
    assert list(MediaFile.objects.values_list("rel_path", flat=True)) == ["keep/a.jpg"]


def test_a_subfolder_narrows_the_scan_but_paths_stay_relative_to_the_root(library, tags):
    write(library / "Photos" / "a.jpg", jpeg_bytes())
    write(library / "Other" / "b.jpg", jpeg_bytes(color=(3, 3, 3)))
    AppSettings.objects.update_or_create(pk=1, defaults={"library_subfolder": "Photos"})

    scan()

    assert list(MediaFile.objects.values_list("rel_path", flat=True)) == ["Photos/a.jpg"]


def test_a_video_is_a_video_asset(library, tags, monkeypatch):
    from library.services import thumbnails

    monkeypatch.setattr(thumbnails, "_open_video", lambda path: thumbnails.Image.new("RGB", (1920, 1080)))
    tags["clip.mp4"] = {"File:MIMEType": "video/mp4", "QuickTime:Duration": 4.2}
    write(library / "clip.mp4", b"\x00\x00\x00\x18ftypmp42 not really a video")

    scan()

    asset = Asset.objects.get()
    assert asset.kind == "video"
    assert asset.duration == 4.2
    assert cache_path(asset.primary_file.sha256, "small").is_file()


def test_an_unreadable_file_fails_after_three_attempts_and_is_not_missing(library, tags, monkeypatch):
    write(library / "good.jpg", jpeg_bytes(color=(1, 1, 1)))
    write(library / "bad.jpg", jpeg_bytes(color=(2, 2, 2)))
    scan()
    write(library / "bad.jpg", jpeg_bytes(color=(5, 5, 5)), mtime_ns=1_800_000_000_000_000_000)

    real_read = metadata.read

    def flaky(path):
        if path.name == "bad.jpg":
            raise RuntimeError("ExifTool choked")
        return real_read(path)

    monkeypatch.setattr(metadata, "read", flaky)
    run = scan()

    failed = Job.objects.get(state=JobState.FAILED)
    assert failed.kind == "index_file" and failed.attempts == 3
    assert "ExifTool choked" in failed.error
    assert run.state == ScanState.DONE
    assert run.missing == 0
    assert MediaFile.objects.get(rel_path="bad.jpg").state == FileState.PRESENT


def test_renormalise_applies_new_rules_without_touching_files(library, tags, monkeypatch):
    from django.core.management import call_command

    from library.models import CaptureSource
    from library.services import indexer

    write(library / "IMG_20170608_084734.jpg", jpeg_bytes())
    scan()
    asset = Asset.objects.get()
    assert asset.capture_source == CaptureSource.FILE_NAME
    # Pretend it was indexed under the old rules, before names were read.
    Asset.objects.update(capture_source=CaptureSource.FILE_TIME, capture_at=timezone.now())
    (library / "IMG_20170608_084734.jpg").unlink()  # proof that no file is opened
    monkeypatch.setattr(metadata, "read", lambda p: pytest.fail("ExifTool was run"))

    call_command("renormalise")
    assert indexer.renormalise() == 0, "a second run changes nothing"

    asset.refresh_from_db()
    assert asset.capture_source == CaptureSource.FILE_NAME
    assert (asset.capture_at.year, asset.capture_at.month, asset.capture_at.day) == (2017, 6, 8)


def test_missing_library_fails_the_scan_with_a_reason(settings, tags, db):
    settings.LIBRARY_ROOT = None
    run = scan()
    assert run.state == ScanState.FAILED
    assert "LIBRARY_ROOT" in run.error


def test_only_one_scan_runs_at_a_time(library, tags):
    first = start_scan()
    assert start_scan().pk == first.pk
    assert ScanRun.objects.count() == 1
    drain()
    assert start_scan().pk != first.pk


def test_schedule(library, tags, settings):
    assert schedule_due().trigger == ScanTrigger.SCHEDULE
    drain()
    assert schedule_due() is None, "the interval has not passed"
    later = timezone.now() + timedelta(minutes=61)
    assert schedule_due(now=later) is not None
    drain()

    AppSettings.objects.filter(pk=1).update(scan_interval_minutes=0)
    assert schedule_due(now=later + timedelta(days=1)) is None

    AppSettings.objects.filter(pk=1).update(scan_interval_minutes=60)
    settings.LIBRARY_ROOT = None
    assert schedule_due(now=later + timedelta(days=1)) is None
