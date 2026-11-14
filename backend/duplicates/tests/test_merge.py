"""Merging (DUP-04, DUP-05): the preview, the merge, undo, and fault injection.

ExifTool is the JSON-trailer stand-in from fileops' tests, so writes really
change the files' bytes.
"""

from __future__ import annotations

import hashlib
from types import SimpleNamespace

import pytest

from core.models import AppSettings
from core.services.exceptions import BusinessRuleError
from duplicates.models import DuplicateGroup, GroupKind, GroupState
from duplicates.services import review
from fileops.models import Operation, OpState
from fileops.services import engine, ops
from library.models import Asset, FileState, MediaFile
from library.services.paths import TRASH_DIR
from library.services.scan import start_scan
from organise import services as organise
from organise.models import AlbumItem

from .conftest import camera, drain, picture, stripped, trailer, write

pytestmark = pytest.mark.django_db


def scan() -> None:
    start_scan()
    drain()


@pytest.fixture
def pair(library, exif):
    """A camera original and a copy of the same picture - other bytes, the
    same pixels - that knows more about the scene."""
    data = picture(3, (1600, 1200), quality=95)
    write(library / "2024" / "DSCF4821.JPG", data)
    write(library / "Phone" / "IMG_20240614_184207.jpg", data + b"\x00phone tags")
    exif["DSCF4821.JPG"] = camera(size=(1600, 1200), **{"XMP:Subject": ["coast"], "XMP:Rating": 3})
    exif["IMG_20240614_184207.jpg"] = stripped(
        size=(1600, 1200),
        **{
            "Composite:GPSLatitude": -38.392,
            "Composite:GPSLongitude": 144.252,
            "XMP:Title": "Last light, Point Addis",
            "XMP:Subject": ["coast", "sunset"],
        },
    )
    scan()
    keeper = MediaFile.objects.get(rel_path="2024/DSCF4821.JPG")
    copy = MediaFile.objects.get(rel_path="Phone/IMG_20240614_184207.jpg")
    ops.add_to_album([copy.asset_id], "Coast 2024")
    ops.set_favourite([copy.asset_id], True)
    (group,) = DuplicateGroup.objects.filter(state=GroupState.OPEN)
    return SimpleNamespace(group=group, keeper=keeper, copy=copy, library=library)


def tree(root) -> dict[str, str]:
    return {
        p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(root.rglob("*"))
        if p.is_file()
    }


def database() -> dict:
    return {
        "assets": list(
            Asset.objects.order_by("pk").values_list(
                "pk", "title", "description", "rating", "favourite", "lat", "lon", "camera_make",
                "camera_model", "lens", "capture_at", "capture_offset", "capture_source", "place",
            )
        ),
        "files": list(MediaFile.objects.order_by("pk").values_list("pk", "asset_id", "rel_path", "state", "sha256")),
        "tags": {a.pk: organise.tags_of(a) for a in Asset.objects.all()},
        "albums": sorted(AlbumItem.objects.values_list("album_id", "asset_id")),
        "groups": list(DuplicateGroup.objects.order_by("pk").values_list("pk", "state")),
    }


# ---------------------------------------------------------------------------
# Preview
# ---------------------------------------------------------------------------


def test_the_preview_proposes_the_best_copy_and_fills_its_gaps(pair):
    d = review.detail(pair.group.pk)
    assert d["kind"] == GroupKind.PIXELS
    assert d["keeper"] == d["proposedKeeper"] == str(pair.keeper.asset_id)
    assert d["keeperReason"] == "Still has the camera’s original details"
    assert [c["letter"] for c in d["copies"]] == ["A", "B"]
    rows = {r["field"]: r for r in d["rows"]}
    assert rows["title"]["merged"] == "Last light, Point Addis" and rows["title"]["sources"] == ["B"]
    assert rows["gps"]["merged"] == "−38.3920°, 144.2520°"
    assert rows["keywords"]["merged"] == "coast, sunset" and rows["keywords"]["mode"] == "union"
    assert rows["camera"]["merged"] == "FUJIFILM X-T4"
    assert d["conflicts"] == 0 and d["mergeable"]
    assert d["favourite"] is True and d["albums"] == ["Coast 2024"]


def test_a_conflict_must_be_settled_and_a_choice_settles_it(pair, exif):
    Asset.objects.filter(pk=pair.keeper.asset_id).update(title="Cliffs")
    d = review.detail(pair.group.pk)
    assert d["conflicts"] == 1
    with pytest.raises(BusinessRuleError, match="Resolve 1 conflict"):
        review.merge(pair.group.pk)
    chosen = review.detail(pair.group.pk, choices={"title": str(pair.copy.asset_id)})
    assert chosen["conflicts"] == 0
    title = next(r for r in chosen["rows"] if r["field"] == "title")
    assert title["resolved"] and title["sourceText"] == "your choice"


def test_the_owner_can_keep_another_copy(pair):
    d = review.detail(pair.group.pk, keeper=str(pair.copy.asset_id))
    assert d["keeper"] == str(pair.copy.asset_id)
    assert d["copies"][0]["letter"] == "A", "letters do not move with the keeper"


# ---------------------------------------------------------------------------
# The merge, and undo
# ---------------------------------------------------------------------------


def test_a_merge_fills_the_keeper_trashes_the_copy_and_undo_puts_it_all_back(pair, exif):
    before_tree, before_db = tree(pair.library), database()
    original = (pair.library / "2024" / "DSCF4821.JPG").read_bytes()

    op = review.merge(pair.group.pk)
    assert op.state == OpState.PENDING
    drain()
    op.refresh_from_db()
    assert op.state == OpState.APPLIED, op.error
    assert op.summary == "Merged 2 copies into DSCF4821.JPG"

    kept = trailer(pair.library / "2024" / "DSCF4821.JPG")
    assert kept["XMP-dc:Title"] == "Last light, Point Addis"
    assert kept["XMP-dc:Subject"] == ["coast", "sunset"]
    assert float(kept["XMP-exif:GPSLatitude"]) == -38.392
    assert kept["EXIF:GPSLatitudeRef"] == "S"

    asset = Asset.objects.get(pk=pair.keeper.asset_id)
    assert (asset.title, asset.lat, asset.lon, asset.favourite) == ("Last light, Point Addis", -38.392, 144.252, True)
    assert asset.geocoded is False, "a new position is named again (BRD: place from the merged GPS)"
    assert organise.tags_of(asset) == ["coast", "sunset"]
    assert set(AlbumItem.objects.filter(asset=asset).values_list("album__name", flat=True)) == {"Coast 2024"}

    copy = MediaFile.objects.get(pk=pair.copy.pk)
    assert copy.state == FileState.TRASHED
    assert copy.rel_path == f"{TRASH_DIR}/{op.pk}/Phone/IMG_20240614_184207.jpg"
    backup = pair.library / TRASH_DIR / str(op.pk) / "keeper-original" / str(pair.keeper.pk) / "DSCF4821.JPG"
    assert backup.read_bytes() == original
    assert DuplicateGroup.objects.get(pk=pair.group.pk).state == GroupState.MERGED
    assert review.queue()["total"] == 0

    ops.undo(op.pk)
    drain()
    op.refresh_from_db()
    assert op.state == OpState.UNDONE, op.error
    assert tree(pair.library) == before_tree
    after = database()
    assert after == before_db


def test_purging_the_merged_copy_drops_the_keepers_backup(pair, exif):
    op = review.merge(pair.group.pk)
    drain()
    ops.purge()
    drain()
    assert not (pair.library / TRASH_DIR).exists() or not any((pair.library / TRASH_DIR).rglob("*.JPG"))
    op.refresh_from_db()
    assert op.undoable is False


def test_identical_copies_merge_without_writing_anything(library, exif):
    data = picture(1)
    write(library / "2024" / "IMG_0412.jpg", data)
    write(library / "Downloads" / "IMG_0412 (1).jpg", data)
    exif["IMG_0412.jpg"] = exif["IMG_0412 (1).jpg"] = camera()
    scan()
    (group,) = DuplicateGroup.objects.all()
    op = review.merge(group.pk)
    assert [s["t"] for s in op.request["plan"]] == ["merge_check", "move_file", "dup_group"]
    drain()
    op.refresh_from_db()
    assert op.state == OpState.APPLIED
    assert (library / "2024" / "IMG_0412.jpg").read_bytes() == data
    assert MediaFile.objects.get(rel_path__endswith="IMG_0412 (1).jpg").state == FileState.TRASHED


def test_a_copy_with_a_raw_is_never_trashed_for_one_without(library, exif):
    write(library / "trip" / "IMG_1.CR3", b"raw bytes")
    write(library / "trip" / "IMG_1.JPG", picture(7))
    write(library / "Export" / "IMG_1.jpg", picture(7))  # the JPEG again, alone
    exif["IMG_1.CR3"] = {"File:MIMEType": "image/x-canon-cr3", "Composite:ImageSize": "640 480"}
    exif["IMG_1.JPG"] = stripped()
    exif["IMG_1.jpg"] = stripped()
    scan()
    (group,) = DuplicateGroup.objects.all()
    d = review.detail(group.pk)
    alone = MediaFile.objects.get(rel_path="Export/IMG_1.jpg").asset_id
    paired = MediaFile.objects.get(rel_path="trip/IMG_1.JPG").asset_id
    by_id = {c["assetId"]: c for c in d["copies"]}
    assert "RAW file IMG_1.CR3" in by_id[str(alone)]["cannotKeep"]
    assert by_id[str(paired)]["cannotKeep"] is None
    assert d["keeper"] == str(paired)
    with pytest.raises(BusinessRuleError):
        review.merge(group.pk, keeper=str(alone))


def test_a_file_changed_since_the_scan_stops_the_merge(pair, exif):
    path = pair.library / "Phone" / "IMG_20240614_184207.jpg"
    path.write_bytes(path.read_bytes() + b"edited elsewhere")
    op = review.merge(pair.group.pk)
    drain()
    op.refresh_from_db()
    assert op.state == OpState.FAILED and "has changed since" in op.error
    assert DuplicateGroup.objects.get(pk=pair.group.pk).state == GroupState.OPEN


# ---------------------------------------------------------------------------
# Fault injection (HLD): fail - or crash - before each step; nothing changes.
# ---------------------------------------------------------------------------


class Crash(BaseException):
    """The worker process dying: not an error anything catches."""


def _fail_at(monkeypatch, k: int, exc: BaseException):
    """Raise `exc` before the k-th step of a merge. Returns a function that
    takes the fault away again (the worker restarting healthy)."""
    originals = dict(engine._APPLY)
    seen = {"n": 0}
    for name, fn in list(engine._APPLY.items()):
        def wrapped(op, s, fn=fn):
            if op.kind == "merge":
                if seen["n"] == k:
                    raise exc
                seen["n"] += 1
            return fn(op, s)

        monkeypatch.setitem(engine._APPLY, name, wrapped)
    return lambda: engine._APPLY.update(originals)


STEPS = 7  # check, fields, tags, write, trash, album, group


@pytest.mark.parametrize("k", range(STEPS))
def test_a_failure_at_any_step_leaves_files_and_database_as_they_were(pair, exif, monkeypatch, k):
    before_tree, before_db = tree(pair.library), database()
    op = review.merge(pair.group.pk)
    assert len(op.request["plan"]) == STEPS
    _fail_at(monkeypatch, k, RuntimeError("disk full"))
    drain()
    op.refresh_from_db()
    assert op.state == OpState.FAILED and "disk full" in op.error
    assert tree(pair.library) == before_tree
    assert database() == before_db


@pytest.mark.parametrize("k", range(STEPS))
def test_a_crash_at_any_step_is_rolled_back_when_the_worker_restarts(pair, exif, monkeypatch, k):
    before_tree, before_db = tree(pair.library), database()
    op = review.merge(pair.group.pk)
    healthy = _fail_at(monkeypatch, k, Crash())
    with pytest.raises(Crash):
        drain()
    healthy()
    # The restarted worker finds the merge pending and started: it reverses it.
    ops.operation_job(SimpleNamespace(payload={"op": op.pk}))
    op.refresh_from_db()
    assert op.state == OpState.FAILED and "Interrupted" in op.error
    assert tree(pair.library) == before_tree
    assert database() == before_db


# ---------------------------------------------------------------------------
# Settling without merging, and DUP-06
# ---------------------------------------------------------------------------


def test_not_duplicates_can_be_undone(pair):
    op = review.dismiss(pair.group.pk)
    assert op.state == OpState.APPLIED
    assert review.queue()["total"] == 0
    ops.undo(op.pk)
    assert review.queue()["total"] == 1


def test_exact_copies_merge_in_bulk_and_by_themselves_when_switched_on(library, exif):
    for i in range(3):
        data = picture(20 + i)
        write(library / "a" / f"x{i}.jpg", data)
        write(library / "b" / f"x{i}.jpg", data)
    scan()
    assert review.queue()["exactReady"] == 3
    started = review.merge_exact()
    assert len(started) == 3
    drain()
    assert all(o.state == OpState.APPLIED for o in Operation.objects.filter(kind="merge"))
    assert review.queue()["total"] == 0

    AppSettings.objects.update_or_create(pk=1, defaults={"auto_merge_exact": True})
    write(library / "c" / "y.jpg", picture(30))
    write(library / "d" / "y.jpg", picture(30))
    scan()
    assert Operation.objects.filter(kind="merge", state=OpState.APPLIED).count() == 4


def test_settled_same_picture_groups_merge_in_bulk_and_conflicts_wait(pair, library, exif):
    # A second same-picture pair whose titles disagree: it needs the owner.
    data = picture(40, (1600, 1200), quality=95)
    write(library / "2025" / "DSCF0001.JPG", data)
    write(library / "Phone" / "IMG_0001.jpg", data + b"\x00phone tags")
    exif["DSCF0001.JPG"] = camera(size=(1600, 1200), **{"XMP:Title": "Cliffs"})
    exif["IMG_0001.jpg"] = stripped(size=(1600, 1200), **{"XMP:Title": "Beach"})
    scan()
    queue = review.queue()
    assert queue["counts"]["pixels"] == 2 and queue["ready"] == {"exact": 0, "pixels": 1}
    assert {g["id"]: g["ready"] for g in queue["groups"]}[str(pair.group.pk)] is True

    assert review.merge_exact() == []  # the auto-merge setting's scope stays exact copies
    (op,) = review.merge_ready("pixels")
    drain()
    op.refresh_from_db()
    assert op.state == OpState.APPLIED
    kept = Asset.objects.get(pk=pair.keeper.asset_id)
    assert kept.title == "Last light, Point Addis" and kept.favourite
    after = review.queue()
    assert after["total"] == 1 and after["ready"]["pixels"] == 0
