"""Operations end to end against a throwaway library: the files and the rows."""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.utils import timezone

from core.models import AppSettings
from fileops.models import Operation, OpState
from fileops.services import engine, ops
from library.models import Asset, FileState, MediaFile
from library.services.scan import start_scan
from organise.models import Album, Tag

from .conftest import drain, jpeg_bytes, trailer, write

pytestmark = pytest.mark.django_db


def scan():
    start_scan()
    drain()


def asset_at(rel: str) -> Asset:
    return MediaFile.objects.get(rel_path=rel).asset


def settle(op: Operation) -> Operation:
    drain()
    op.refresh_from_db()
    return op


# ---------------------------------------------------------------------------
# Favourites and edits
# ---------------------------------------------------------------------------


def test_favourite_is_immediate_and_undoable(library, exif):
    write(library / "a.jpg", jpeg_bytes())
    scan()
    a = asset_at("a.jpg")

    op = ops.set_favourite([a.pk], True)
    assert op.state == OpState.APPLIED
    a.refresh_from_db()
    assert a.favourite is True

    ops.undo(op.pk)
    a.refresh_from_db()
    assert a.favourite is False
    assert Operation.objects.get(pk=op.pk).state == OpState.UNDONE


def test_an_edit_reaches_the_database_at_once_and_the_file_through_the_worker(library, exif):
    write(library / "a.jpg", jpeg_bytes())
    scan()
    a = asset_at("a.jpg")
    sha_before = a.primary_file.sha256

    op = ops.edit([a.pk], fields={"title": "Harbour", "rating": 4})
    assert op.state == OpState.PENDING
    a.refresh_from_db()
    assert (a.title, a.rating) == ("Harbour", 4), "the screen sees it straight away"

    op = settle(op)
    assert op.state == OpState.APPLIED
    assert trailer(library / "a.jpg") == {"XMP-dc:Title": "Harbour", "XMP-xmp:Rating": 4}
    media = MediaFile.objects.get(rel_path="a.jpg")
    assert media.sha256 != sha_before, "re-indexed after the write"
    assert not list(library.glob(".*gallery-tmp*")), "no temporary copy left behind"

    undone = settle(ops.undo(op.pk))
    assert undone.state == OpState.UNDONE
    assert trailer(library / "a.jpg") == {}
    a.refresh_from_db()
    assert (a.title, a.rating) == ("", 0)


def test_tags_are_written_as_keywords_and_survive_a_rescan(library, exif):
    write(library / "a.jpg", jpeg_bytes())
    scan()
    a = asset_at("a.jpg")

    settle(ops.edit([a.pk], add_tags=["beach", "Family"]))
    assert trailer(library / "a.jpg")["XMP-dc:Subject"] == ["beach", "Family"]
    assert sorted(Tag.objects.values_list("name", flat=True), key=str.lower) == ["beach", "Family"]

    write(library / "a.jpg", (library / "a.jpg").read_bytes(), mtime_ns=1_900_000_000_000_000_000)
    scan()
    a.refresh_from_db()
    assert sorted(a.asset_tags.values_list("tag__name", flat=True), key=str.lower) == ["beach", "Family"]

    settle(ops.edit([a.pk], remove_tags=["BEACH"]))
    assert trailer(library / "a.jpg")["XMP-dc:Subject"] == "Family"
    assert list(Tag.objects.values_list("name", flat=True)) == ["Family"], "unused tags are forgotten"


def test_keywords_already_in_a_file_become_tags(library, exif):
    exif["a.jpg"] = {"XMP:Subject": ["sunset", "pier"]}
    write(library / "a.jpg", jpeg_bytes())
    scan()
    assert sorted(asset_at("a.jpg").asset_tags.values_list("tag__name", flat=True)) == ["pier", "sunset"]


def test_raw_gets_a_lightroom_style_sidecar_that_the_scanner_reads_back(library, exif):
    write(library / "DSC_1.NEF", b"raw bytes")
    scan()
    a = asset_at("DSC_1.NEF")

    op = settle(ops.edit([a.pk], fields={"title": "Ridge"}))
    assert op.state == OpState.APPLIED
    assert (library / "DSC_1.NEF").read_bytes() == b"raw bytes", "the RAW is never written"
    assert trailer(library / "DSC_1.xmp") == {"XMP-dc:Title": "Ridge"}

    # A rescan reads the sidecar, so the title stays even though the RAW has none.
    write(library / "DSC_1.NEF", b"raw bytes", mtime_ns=1_900_000_000_000_000_000)
    scan()
    a.refresh_from_db()
    assert a.title == "Ridge"

    settle(ops.undo(op.pk))
    assert not (library / "DSC_1.xmp").exists(), "a sidecar The Gallery created goes on undo"


def test_a_shared_stem_gets_the_unambiguous_sidecar_name(library, exif):
    write(library / "IMG_1.NEF", b"raw")
    write(library / "IMG_1.MOV", b"video")
    scan()
    raw = asset_at("IMG_1.NEF")
    settle(ops.edit([raw.pk], fields={"title": "x"}))
    assert (library / "IMG_1.NEF.xmp").exists()
    assert not (library / "IMG_1.xmp").exists()


def test_sidecars_only_mode_never_touches_a_jpeg(library, exif):
    AppSettings.objects.update_or_create(pk=1, defaults={"write_back_mode": "sidecar"})
    write(library / "a.jpg", jpeg_bytes())
    scan()
    before = (library / "a.jpg").read_bytes()
    settle(ops.edit([asset_at("a.jpg").pk], fields={"title": "x"}))
    assert (library / "a.jpg").read_bytes() == before
    assert trailer(library / "a.xmp") == {"XMP-dc:Title": "x"}


def test_editing_the_date(library, exif):
    exif["a.jpg"] = {"EXIF:DateTimeOriginal": "2020:01:01 10:00:00", "EXIF:OffsetTimeOriginal": "+08:00"}
    write(library / "a.jpg", jpeg_bytes())
    scan()
    a = asset_at("a.jpg")

    settle(ops.edit([a.pk], fields={"capturedAt": "2021-06-15T09:30:00"}))
    a.refresh_from_db()
    assert a.date_edited is True
    assert a.capture_at.isoformat() == "2021-06-15T01:30:00+00:00"
    assert trailer(library / "a.jpg")["EXIF:DateTimeOriginal"] == "2021:06:15 09:30:00"
    assert trailer(library / "a.jpg")["XMP-exif:DateTimeOriginal"] == "2021:06:15 09:30:00+08:00"


@pytest.mark.parametrize(
    "fields,field",
    [({"rating": 6}, "rating"), ({"rating": "5"}, "rating"), ({"capturedAt": "yesterday"}, "capturedAt"),
     ({"title": "x" * 501}, "title")],
)
def test_bad_edits_are_refused_before_anything_changes(library, exif, fields, field):
    write(library / "a.jpg", jpeg_bytes())
    scan()
    with pytest.raises(ops.InvalidRequest) as refused:
        ops.edit([asset_at("a.jpg").pk], fields=fields)
    assert refused.value.field == field
    assert not Operation.objects.exists()


def test_a_failed_write_reverses_the_database_change_too(library, exif, monkeypatch):
    write(library / "a.jpg", jpeg_bytes())
    scan()
    a = asset_at("a.jpg")

    def broken(path, args):
        raise OSError("disk is read-only")

    from library.services import metadata

    monkeypatch.setattr(metadata, "write_tags", broken)
    op = settle(ops.edit([a.pk], fields={"title": "Lost"}))
    assert op.state == OpState.FAILED
    assert "read-only" in op.error
    a.refresh_from_db()
    assert a.title == ""


# ---------------------------------------------------------------------------
# Trash, restore, purge
# ---------------------------------------------------------------------------


def test_trash_and_restore(library, exif):
    write(library / "2024" / "a.jpg", jpeg_bytes())
    scan()
    a = asset_at("2024/a.jpg")

    op = settle(ops.trash([a.pk]))
    assert op.state == OpState.APPLIED
    media = MediaFile.objects.get(asset=a)
    assert media.state == FileState.TRASHED
    assert media.rel_path == f".gallery-trash/{op.pk}/2024/a.jpg"
    assert (library / media.rel_path).is_file()
    assert not (library / "2024" / "a.jpg").exists()

    scan()
    media.refresh_from_db()
    assert media.state == FileState.TRASHED, "a rescan neither re-adds nor loses it"

    restored = settle(ops.restore([a.pk]))
    assert restored.state == OpState.APPLIED
    media.refresh_from_db()
    assert (media.state, media.rel_path, media.trashed_from) == (FileState.PRESENT, "2024/a.jpg", "")
    assert not (library / ".gallery-trash" / str(op.pk)).exists(), "the emptied trash folder is tidied"


def test_undoing_a_trash_puts_files_and_sidecars_back(library, exif):
    write(library / "DSC_1.NEF", b"raw")
    write(library / "DSC_1.JPG", jpeg_bytes())
    scan()
    pair = asset_at("DSC_1.NEF")
    settle(ops.edit([pair.pk], fields={"title": "t"}))
    assert (library / "DSC_1.xmp").exists()

    op = settle(ops.trash([pair.pk]))
    assert not (library / "DSC_1.NEF").exists() and not (library / "DSC_1.xmp").exists()
    assert len(list((library / ".gallery-trash" / str(op.pk)).iterdir())) == 3, "RAW, JPEG and sidecar together"

    settle(ops.undo(op.pk))
    assert (library / "DSC_1.NEF").exists() and (library / "DSC_1.JPG").exists() and (library / "DSC_1.xmp").exists()
    assert not (library / ".gallery-trash" / str(op.pk)).exists(), "empty trash folders are tidied"


def test_restore_never_overwrites_a_new_file_at_the_old_path(library, exif):
    write(library / "a.jpg", jpeg_bytes(color=(1, 1, 1)))
    scan()
    a = asset_at("a.jpg")
    settle(ops.trash([a.pk]))
    write(library / "a.jpg", jpeg_bytes(color=(9, 9, 9)))

    settle(ops.restore([a.pk]))
    assert MediaFile.objects.get(asset=a).rel_path == "a (1).jpg"
    assert (library / "a.jpg").exists() and (library / "a (1).jpg").exists()


def test_purge_is_final(library, exif):
    write(library / "a.jpg", jpeg_bytes())
    scan()
    a = asset_at("a.jpg")
    trash_op = settle(ops.trash([a.pk]))
    path = library / MediaFile.objects.get(asset=a).rel_path

    purge_op = settle(ops.purge([a.pk]))
    assert purge_op.state == OpState.APPLIED
    assert not path.exists()
    assert not Asset.objects.filter(pk=a.pk).exists()
    trash_op.refresh_from_db()
    assert trash_op.undoable is False
    with pytest.raises(engine.CannotUndo):
        ops.undo(trash_op.pk)
    with pytest.raises(engine.CannotUndo):
        ops.undo(purge_op.pk)


def test_the_trash_empties_itself_after_the_retention_period(library, exif):
    write(library / "old.jpg", jpeg_bytes(color=(1, 1, 1)))
    write(library / "new.jpg", jpeg_bytes(color=(2, 2, 2)))
    scan()
    settle(ops.trash([asset_at("old.jpg").pk, asset_at("new.jpg").pk]))
    MediaFile.objects.filter(trashed_from="old.jpg").update(trashed_at=timezone.now() - timedelta(days=31))

    assert ops.purge_due() is not None
    drain()
    assert list(MediaFile.objects.values_list("trashed_from", flat=True)) == ["new.jpg"]
    assert ops.purge_due() is None, "nothing else is due"


def test_only_trashed_items_can_be_restored_or_purged(library, exif):
    write(library / "a.jpg", jpeg_bytes())
    scan()
    with pytest.raises(Exception, match="not in the trash"):
        ops.restore([asset_at("a.jpg").pk])


# ---------------------------------------------------------------------------
# Move and rename
# ---------------------------------------------------------------------------


def test_move_creates_the_folder_and_undo_removes_it(library, exif):
    write(library / "inbox" / "a.jpg", jpeg_bytes(color=(1, 1, 1)))
    write(library / "2024" / "Trip" / "a.jpg", jpeg_bytes(color=(2, 2, 2)))
    scan()
    a = asset_at("inbox/a.jpg")

    op = settle(ops.move([a.pk], "2024/Trip/Day 2"))
    assert MediaFile.objects.get(asset=a).rel_path == "2024/Trip/Day 2/a.jpg"

    settle(ops.undo(op.pk))
    assert MediaFile.objects.get(asset=a).rel_path == "inbox/a.jpg"
    assert not (library / "2024" / "Trip" / "Day 2").exists()

    settle(ops.move([a.pk], "2024/Trip"))
    assert MediaFile.objects.get(asset=a).rel_path == "2024/Trip/a (1).jpg", "never overwrites"


def test_move_refuses_bad_folders(library, exif):
    write(library / "a.jpg", jpeg_bytes())
    scan()
    for folder in ("../out", "x/CON", ".gallery-trash"):
        with pytest.raises(Exception):
            ops.move([asset_at("a.jpg").pk], folder)


def test_rename_moves_a_pair_and_its_sidecar_together(library, exif):
    write(library / "DSC_1.NEF", b"raw")
    write(library / "DSC_1.JPG", jpeg_bytes())
    scan()
    pair = asset_at("DSC_1.NEF")
    settle(ops.edit([pair.pk], fields={"title": "t"}))

    op = settle(ops.rename(pair.pk, "Ridge at dawn"))
    assert op.state == OpState.APPLIED
    assert sorted(p.name for p in library.iterdir() if p.is_file()) == [
        "Ridge at dawn.JPG", "Ridge at dawn.NEF", "Ridge at dawn.xmp",
    ]
    settle(ops.undo(op.pk))
    assert sorted(p.name for p in library.iterdir() if p.is_file()) == ["DSC_1.JPG", "DSC_1.NEF", "DSC_1.xmp"]


# ---------------------------------------------------------------------------
# Failure and crash: all or nothing (NFR-03)
# ---------------------------------------------------------------------------


def test_a_failure_part_way_puts_back_what_was_done(library, exif):
    write(library / "a.jpg", jpeg_bytes(color=(1, 1, 1)))
    write(library / "b.jpg", jpeg_bytes(color=(2, 2, 2)))
    scan()
    a, b = asset_at("a.jpg"), asset_at("b.jpg")
    (library / "b.jpg").unlink()  # gone behind the library's back

    op = settle(ops.trash([a.pk, b.pk]))
    assert op.state == OpState.FAILED
    assert "no longer where" in op.error
    assert (library / "a.jpg").exists(), "the first file came back"
    assert MediaFile.objects.get(asset=a).state == FileState.PRESENT


def test_an_interrupted_operation_is_reversed_on_the_next_run(library, exif):
    write(library / "a.jpg", jpeg_bytes(color=(1, 1, 1)))
    write(library / "b.jpg", jpeg_bytes(color=(2, 2, 2)))
    scan()
    op = ops.trash([asset_at("a.jpg").pk, asset_at("b.jpg").pk])
    # Simulate the worker dying after its first step.
    op.refresh_from_db()
    op.request = {**op.request, "worker_started": True}
    op.save()
    engine.apply_steps(op, op.request["plan"][:1])
    assert not (library / "a.jpg").exists()

    from jobs.models import Job

    ops.operation_job(Job(payload={"op": op.pk}))
    op.refresh_from_db()
    assert op.state == OpState.FAILED
    assert "Interrupted" in op.error
    assert (library / "a.jpg").exists() and (library / "b.jpg").exists()


# ---------------------------------------------------------------------------
# Albums (database only)
# ---------------------------------------------------------------------------


def test_albums(library, exif):
    for name in ("a.jpg", "b.jpg"):
        write(library / name, jpeg_bytes(color=(len(name), 0, 0) if name == "a.jpg" else (0, 9, 0)))
    scan()
    a, b = asset_at("a.jpg"), asset_at("b.jpg")

    add = ops.add_to_album([a.pk, b.pk], "Coast 2024")
    album = Album.objects.get()
    assert (album.name, album.items.count(), album.cover_id) == ("Coast 2024", 2, a.pk)
    again = ops.add_to_album([a.pk], "coast 2024")
    assert Album.objects.count() == 1 and again.state == OpState.APPLIED, "same album, any case"

    ops.remove_from_album(album.pk, [a.pk])
    album.refresh_from_db()
    assert album.cover_id == b.pk

    ops.rename_album(album.pk, "Coast")
    deleted = ops.delete_album(album.pk)
    assert not Album.objects.exists()
    assert Asset.objects.count() == 2, "deleting an album never deletes photos"
    ops.undo(deleted.pk)
    restored = Album.objects.get()
    assert (restored.name, list(restored.items.values_list("asset_id", flat=True))) == ("Coast", [b.pk])

    with pytest.raises(Exception, match="already an album"):
        ops.create_album("coast")

    # The first add also created the album, so undoing it takes the album away.
    ops.undo(add.pk)
    assert not Album.objects.exists()
