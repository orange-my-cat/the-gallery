"""The step types operations are made of. See engine.py for the contract.

Database steps change only rows. File steps change the library and the rows
that describe it; each puts the file back itself if its row update fails.
"""

from __future__ import annotations

import os
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any

from django.utils import timezone

from core.services.exceptions import BusinessRuleError
from fileops.models import Operation
from fileops.services import xmp
from fileops.services.engine import CannotUndo, Step, reverter, step
from fileops.services.names import free_target
from library.models import Asset, FileState, MediaFile
from library.services.indexer import index_path
from library.services.paths import TRASH_DIR, library_root, pair_key, to_abs, to_rel
from organise import services as organise
from organise.models import Album

# ---------------------------------------------------------------------------
# Database: asset fields
# ---------------------------------------------------------------------------

EDITABLE_FIELDS = (
    "favourite", "rating", "title", "description", "place",
    "capture_at", "capture_offset", "capture_source", "date_edited",
    # Filled in by a duplicate merge from another copy (DUP-05).
    "lat", "lon", "camera_make", "camera_model", "lens",
)


def _dump(value: Any) -> Any:
    return value.isoformat() if isinstance(value, datetime) else value


def _load(field: str, value: Any) -> Any:
    return datetime.fromisoformat(value) if field == "capture_at" and isinstance(value, str) else value


@step("asset_fields", touches_files=False)
def apply_asset_fields(op: Operation, s: Step) -> Step:
    asset = Asset.objects.select_for_update().get(pk=s["asset"])
    before = {f: _dump(getattr(asset, f)) for f in s["set"]}
    for field, value in s["set"].items():
        if field not in EDITABLE_FIELDS:
            raise BusinessRuleError(f"{field} cannot be edited.")
        setattr(asset, field, _load(field, value))
    _position_changed(asset, s["set"])
    asset.save()
    return {**s, "before": before}


def _position_changed(asset: Asset, fields) -> None:
    """A new position (a merge's) means a new place name, worked out again."""
    if "lat" in fields or "lon" in fields:
        asset.geo_place = None
        asset.geocoded = False


@reverter("asset_fields")
def revert_asset_fields(op: Operation, s: Step) -> None:
    asset = Asset.objects.select_for_update().filter(pk=s["asset"]).first()
    if asset is None:
        return  # purged since; nothing left to put back
    for field, value in s["before"].items():
        setattr(asset, field, _load(field, value))
    _position_changed(asset, s["before"])
    asset.save()


# ---------------------------------------------------------------------------
# Database: tags and albums
# ---------------------------------------------------------------------------


@step("asset_tags", touches_files=False)
def apply_asset_tags(op: Operation, s: Step) -> Step:
    asset = Asset.objects.get(pk=s["asset"])
    before = organise.tags_of(asset)
    remove = {t.lower() for t in s.get("remove", [])}
    after = [t for t in before if t.lower() not in remove] + list(s.get("add", []))
    organise.set_tags(asset, after)
    return {**s, "before": before, "after": organise.tags_of(asset)}


@reverter("asset_tags")
def revert_asset_tags(op: Operation, s: Step) -> None:
    asset = Asset.objects.filter(pk=s["asset"]).first()
    if asset is not None:
        organise.set_tags(asset, s["before"])


@step("album_create", touches_files=False)
def apply_album_create(op: Operation, s: Step) -> Step:
    if organise.album_named(s["name"]) is not None:
        raise BusinessRuleError(f"There is already an album called “{s['name']}”.", field="name")
    album = Album.objects.create(name=s["name"])
    return {**s, "album": album.pk}


@reverter("album_create")
def revert_album_create(op: Operation, s: Step) -> None:
    Album.objects.filter(pk=s["album"]).delete()


@step("album_add", touches_files=False)
def apply_album_add(op: Operation, s: Step) -> Step:
    """By id, or by `album_name` when an earlier step of the plan created it."""
    album = (
        Album.objects.select_for_update().get(pk=s["album"])
        if "album" in s
        else organise.album_named(s["album_name"])
    )
    if album is None:
        raise BusinessRuleError("That album no longer exists.")
    return {**s, "album": album.pk, "added": organise.add_to_album(album, s["assets"])}


@reverter("album_add")
def revert_album_add(op: Operation, s: Step) -> None:
    album = Album.objects.filter(pk=s["album"]).first()
    if album is not None:
        organise.remove_from_album(album, s["added"])


@step("album_remove", touches_files=False)
def apply_album_remove(op: Operation, s: Step) -> Step:
    album = Album.objects.select_for_update().get(pk=s["album"])
    return {**s, "removed": organise.remove_from_album(album, s["assets"])}


@reverter("album_remove")
def revert_album_remove(op: Operation, s: Step) -> None:
    album = Album.objects.filter(pk=s["album"]).first()
    if album is not None:
        organise.add_to_album(album, [pk for pk in s["removed"] if Asset.objects.filter(pk=pk).exists()])


@step("album_rename", touches_files=False)
def apply_album_rename(op: Operation, s: Step) -> Step:
    album = Album.objects.select_for_update().get(pk=s["album"])
    clash = organise.album_named(s["name"])
    if clash is not None and clash.pk != album.pk:
        raise BusinessRuleError(f"There is already an album called “{s['name']}”.", field="name")
    before = album.name
    album.name = s["name"]
    album.save(update_fields=["name"])
    return {**s, "before": before}


@reverter("album_rename")
def revert_album_rename(op: Operation, s: Step) -> None:
    Album.objects.filter(pk=s["album"]).update(name=s["before"])


@step("album_delete", touches_files=False)
def apply_album_delete(op: Operation, s: Step) -> Step:
    album = Album.objects.select_for_update().get(pk=s["album"])
    snapshot = {"name": album.name, "cover": album.cover_id, "members": organise.album_members(album)}
    album.delete()
    return {**s, **snapshot}


@reverter("album_delete")
def revert_album_delete(op: Operation, s: Step) -> None:
    # Under its old id, so earlier changes that name it can still be undone.
    album = Album.objects.create(pk=s["album"], name=s["name"])
    organise.add_to_album(album, [pk for pk in s["members"] if Asset.objects.filter(pk=pk).exists()])
    if s.get("cover") and Asset.objects.filter(pk=s["cover"]).exists():
        album.cover_id = s["cover"]
        album.save(update_fields=["cover"])


# ---------------------------------------------------------------------------
# Files: metadata write-back (MET-02)
# ---------------------------------------------------------------------------


@step("xmp", touches_files=True)
def apply_xmp(op: Operation, s: Step) -> Step:
    media = MediaFile.objects.get(pk=s["file"])
    if media.state == FileState.TRASHED:
        return {**s, "skipped": True}
    root = library_root()
    target, sidecar = xmp.target_for(media, root)
    path = to_abs(target, root)
    assign = xmp.assignments(s["values"], embedded=not sidecar)
    created = sidecar and not path.exists()
    before = {tag: None for tag in assign} if created else xmp.current(path, list(assign))
    xmp.write(path, assign, sidecar=sidecar)
    index_path(media.rel_path)
    return {**s, "target": target, "sidecar": sidecar, "created": created, "before": before}


@reverter("xmp")
def revert_xmp(op: Operation, s: Step) -> None:
    if s.get("skipped"):
        return
    media = MediaFile.objects.filter(pk=s["file"]).first()
    root = library_root()
    path = to_abs(s["target"], root)
    if s["created"]:
        path.unlink(missing_ok=True)  # The Gallery made this sidecar; nothing else is in it
    elif path.exists():
        xmp.write(path, s["before"], sidecar=s["sidecar"])
    if media is not None and media.state != FileState.TRASHED:
        index_path(media.rel_path)


# ---------------------------------------------------------------------------
# Files: a duplicate merge (DUP-05, HLD "Merge")
# ---------------------------------------------------------------------------


@step("merge_check", touches_files=True)
def apply_merge_check(op: Operation, s: Step) -> Step:
    """Lock and re-check: every copy is still the file the review showed."""
    root = library_root()
    for want in s["files"]:
        media = MediaFile.objects.select_for_update().filter(pk=want["file"]).first()
        if media is None or media.state != FileState.PRESENT:
            raise BusinessRuleError("One of the copies is no longer in the library. Rescan, then review this group again.")
        path = to_abs(media.rel_path, root)
        try:
            stat = path.stat()
        except OSError:
            raise BusinessRuleError(
                f"{media.name} is no longer where the library last saw it. Rescan, then review this group again."
            ) from None
        if stat.st_size != want["size"] or stat.st_mtime_ns != want["mtime_ns"]:
            raise BusinessRuleError(
                f"{media.name} has changed since the library last read it. Rescan, then review this group again."
            )
    return s


@reverter("merge_check")
def revert_merge_check(op: Operation, s: Step) -> None:
    return None  # it changed nothing


@step("merge_write", touches_files=True)
def apply_merge_write(op: Operation, s: Step) -> Step:
    """Write the merged metadata into the kept file, after copying its exact
    bytes aside (`backup`), so undo puts back precisely what was there."""
    media = MediaFile.objects.get(pk=s["file"])
    root = library_root()
    target, sidecar = xmp.target_for(media, root)
    path = to_abs(target, root)
    assign = xmp.assignments(s["values"], embedded=not sidecar)
    created = sidecar and not path.exists()
    made: list[str] = []
    backup = None
    if not created:
        dst = to_abs(s["backup_dir"] + "/" + target.rpartition("/")[2], root)
        made = _make_dirs(dst.parent, root)
        shutil.copy2(path, dst)
        backup = to_rel(root, dst)
    try:
        xmp.write(path, assign, sidecar=sidecar)
    except Exception:
        if backup:
            to_abs(backup, root).unlink(missing_ok=True)
            _remove_dirs(made, root)
        if created:
            path.unlink(missing_ok=True)
        raise
    index_path(media.rel_path)
    return {**s, "target": target, "sidecar": sidecar, "created": created, "backup": backup, "made": made}


@reverter("merge_write")
def revert_merge_write(op: Operation, s: Step) -> None:
    root = library_root()
    path = to_abs(s["target"], root)
    if s["created"]:
        path.unlink(missing_ok=True)
    else:
        backup = to_abs(s["backup"], root)
        if not backup.is_file():
            raise CannotUndo(f"The copy of {path.name} taken before the merge is gone, so it cannot be put back.")
        os.replace(backup, path)
        _remove_dirs(s["made"], root)
        _prune_trash_dirs(backup.parent, root)
    media = MediaFile.objects.filter(pk=s["file"]).first()
    if media is not None and media.state != FileState.TRASHED:
        index_path(media.rel_path)


def drop_merge_backups(op: Operation) -> None:
    """The kept files' pre-merge copies, once the merge can no longer be undone."""
    root = library_root()
    for done in op.steps:
        if done.get("t") == "merge_write" and done.get("backup"):
            backup = to_abs(done["backup"], root)
            backup.unlink(missing_ok=True)
            _prune_trash_dirs(backup.parent, root)


# ---------------------------------------------------------------------------
# Files: moving (move, rename, trash, restore)
# ---------------------------------------------------------------------------


def _make_dirs(folder: Path, root: Path) -> list[str]:
    """Create `folder` and any missing parents; return those created, deepest first."""
    made: list[Path] = []
    current = folder
    while not current.exists():
        made.append(current)
        current = current.parent
    for d in reversed(made):
        d.mkdir()
    return [to_rel(root, d) for d in made]


def _remove_dirs(made: list[str], root: Path) -> None:
    for rel in made:  # deepest first
        try:
            (root / rel).rmdir()
        except OSError:
            pass  # not empty any more (something else arrived): leave it


def _move(src: Path, dst: Path) -> None:
    if dst.exists():
        raise BusinessRuleError(f"{dst.name} already exists; nothing is ever overwritten.")
    os.replace(src, dst)


TRASH_FIELDS = ("state", "trashed_from", "trashed_at", "trash_operation_id")


@step("move_file", touches_files=True)
def apply_move_file(op: Operation, s: Step) -> Step:
    """Move one indexed file. `mode` is move (also rename), trash or restore."""
    media = MediaFile.objects.select_for_update().get(pk=s["file"])
    root = library_root()
    src = to_abs(media.rel_path, root)
    if not src.is_file():
        raise BusinessRuleError(f"{media.name} is no longer where the library last saw it. Rescan, then try again.")
    dst = free_target(to_abs(s["to"], root))
    made = _make_dirs(dst.parent, root)
    old_rel = media.rel_path
    prev = {f: _dump(getattr(media, f)) for f in TRASH_FIELDS}
    _move(src, dst)
    try:
        media.rel_path = to_rel(root, dst)
        media.pair_key = pair_key(media.rel_path)
        if s["mode"] == "trash":
            media.state = FileState.TRASHED
            media.trashed_from = old_rel
            media.trashed_at = timezone.now()
            media.trash_operation_id = op.pk
        elif s["mode"] == "restore":
            media.state = FileState.PRESENT
            media.trashed_from = ""
            media.trashed_at = None
            media.trash_operation_id = None
        media.save()
    except Exception:
        _move(dst, src)
        _remove_dirs(made, root)
        raise
    if s["mode"] == "restore":
        _prune_trash_dirs(src.parent, root)
    return {**s, "from": old_rel, "to": media.rel_path, "made": made, "prev": prev}


@reverter("move_file")
def revert_move_file(op: Operation, s: Step) -> None:
    media = MediaFile.objects.select_for_update().filter(pk=s["file"]).first()
    if media is None:
        raise CannotUndo("A file this change moved has since been removed for good.")
    root = library_root()
    here = to_abs(media.rel_path, root)
    if not here.is_file():
        raise BusinessRuleError(f"{media.name} is not where this change left it, so it cannot be put back.")
    back = free_target(to_abs(s["from"], root))
    made = _make_dirs(back.parent, root)
    _move(here, back)
    if TRASH_DIR in media.rel_path.split("/"):
        _prune_trash_dirs(here.parent, root)
    try:
        media.rel_path = to_rel(root, back)
        media.pair_key = pair_key(media.rel_path)
        for field, value in s["prev"].items():
            setattr(media, field, datetime.fromisoformat(value) if field == "trashed_at" and value else value)
        media.save()
    except Exception:
        _move(back, here)
        _remove_dirs(made, root)
        raise
    _remove_dirs(s["made"], root)


@step("move_path", touches_files=True)
def apply_move_path(op: Operation, s: Step) -> Step:
    """Move a file the library does not index - a sidecar - alongside its owner."""
    root = library_root()
    src = to_abs(s["from"], root)
    if not src.is_file():
        return {**s, "skipped": True}
    dst = free_target(to_abs(s["to"], root))
    made = _make_dirs(dst.parent, root)
    _move(src, dst)
    return {**s, "to": to_rel(root, dst), "made": made}


@reverter("move_path")
def revert_move_path(op: Operation, s: Step) -> None:
    if s.get("skipped"):
        return
    root = library_root()
    here = to_abs(s["to"], root)
    if here.is_file():
        back = free_target(to_abs(s["from"], root))
        _make_dirs(back.parent, root)
        _move(here, back)
    _remove_dirs(s["made"], root)


# ---------------------------------------------------------------------------
# Files: purge - the only real deletion (FOP-02). Not reversible.
# ---------------------------------------------------------------------------


@step("purge_file", touches_files=True)
def apply_purge_file(op: Operation, s: Step) -> Step:
    media = MediaFile.objects.select_for_update().filter(pk=s["file"]).first()
    if media is None or media.state != FileState.TRASHED:
        return {**s, "skipped": True}
    root = library_root()
    path = to_abs(media.rel_path, root)
    if TRASH_DIR not in media.rel_path.split("/"):
        raise BusinessRuleError(f"{media.name} is not in the trash; refusing to delete it.")
    removed = [media.rel_path]
    path.unlink(missing_ok=True)
    # Sidecars that went to the trash with it go with it now.
    trash_op = media.trash_operation
    if trash_op is not None:
        for done in trash_op.steps:
            if done["t"] == "move_path" and done.get("owner") == media.pk and not done.get("skipped"):
                (root / done["to"]).unlink(missing_ok=True)
                removed.append(done["to"])
    asset = media.asset
    media.delete()
    if not asset.files.exists():
        asset.delete()
    if trash_op is not None and not MediaFile.objects.filter(trash_operation=trash_op).exists():
        Operation.objects.filter(pk=trash_op.pk).update(undoable=False)
        drop_merge_backups(trash_op)
    _prune_trash_dirs(path.parent, root)
    return {**s, "removed": removed}


@reverter("purge_file")
def revert_purge_file(op: Operation, s: Step) -> None:
    raise CannotUndo("Files emptied from the trash are gone for good.")


def _prune_trash_dirs(folder: Path, root: Path) -> None:
    """Remove now-empty folders inside the trash, never the trash itself."""
    current = folder
    while current != root and TRASH_DIR in to_rel(root, current).split("/"):
        if current.name == TRASH_DIR:
            break
        try:
            current.rmdir()
        except OSError:
            break
        current = current.parent
