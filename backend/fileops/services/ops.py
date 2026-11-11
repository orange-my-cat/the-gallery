"""What the owner can do, as logged and undoable operations (ORG, FOP).

Each function validates its request, plans steps and either runs them here
(database-only: favourites, albums) or hands them to the worker (anything that
touches a file). Edits do both: the database changes at once, so the screen
updates immediately, and the worker then writes the files; if that fails the
database change is reversed too.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime, timedelta, timezone as dt_timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from django.conf import settings
from django.db.models import Q
from django.utils import timezone

from core.models import AppSettings
from core.services.exceptions import BusinessRuleError, ConflictError, NotFoundError
from fileops.models import Operation, OpKind, OpState
from fileops.services import engine, steps as _steps  # noqa: F401 - registers the step types
from fileops.services import xmp
from fileops.services.names import check_folder, check_name
from jobs.services import enqueue
from library.models import Asset, FileState, MediaFile
from library.services.browse import local_capture
from library.services.paths import TRASH_DIR, library_root, normalise_rel
from organise import services as organise
from organise.models import Album

MAX_IDS = 5000


class InvalidRequest(BusinessRuleError):
    code = "validation_failed"


# ---------------------------------------------------------------------------
# Plumbing
# ---------------------------------------------------------------------------


def _new(kind: str, summary: str, request: dict | None = None) -> Operation:
    return Operation.objects.create(kind=kind, summary=summary[:300], request=request or {})


def _queue(op: Operation, plan: list[dict]) -> Operation:
    op.request = {**op.request, "plan": plan}
    Operation.objects.filter(pk=op.pk).update(request=op.request)
    enqueue("operation", {"op": op.pk})
    return op


def _assets(ids: Iterable[Any], *, trashed: bool = False) -> list[Asset]:
    try:
        wanted = list(dict.fromkeys(int(i) for i in ids))
    except (TypeError, ValueError) as exc:
        raise InvalidRequest("Item ids are numbers.", field="ids") from exc
    if not wanted:
        raise InvalidRequest("Select at least one item.", field="ids")
    if len(wanted) > MAX_IDS:
        raise InvalidRequest(f"At most {MAX_IDS} items at once.", field="ids")
    state = Q(files__state=FileState.TRASHED) if trashed else ~Q(files__state=FileState.TRASHED)
    found = {a.pk: a for a in Asset.objects.filter(pk__in=wanted).filter(state).distinct()}
    missing = [pk for pk in wanted if pk not in found]
    if missing:
        raise NotFoundError(
            f"{len(missing)} of the selected items {'are not in the trash' if trashed else 'no longer exist'}. Reload and try again."
        )
    return [found[pk] for pk in wanted]


def _items(n: int) -> str:
    return f"{n} item" if n == 1 else f"{n} items"


def _files(asset: Asset, *, trashed: bool = False) -> list[MediaFile]:
    qs = asset.files.filter(state=FileState.TRASHED) if trashed else asset.files.exclude(state=FileState.TRASHED)
    return list(qs.order_by("pk"))


def _library_folder() -> str:
    return normalise_rel(AppSettings.load().library_subfolder)


def _sidecars(asset: Asset, files: list[MediaFile], root: Path) -> list[tuple[MediaFile, str]]:
    """Sidecars that belong to these files: each file's own `NAME.EXT.xmp`, and
    a `NAME.xmp` that no file outside this asset shares."""
    found: list[tuple[MediaFile, str]] = []
    seen: set[str] = set()
    for f in files:
        short, long = xmp.sidecar_candidates(f.rel_path)
        if (root / long).is_file() and long not in seen:
            found.append((f, long))
            seen.add(long)
        if short not in seen and (root / short).is_file() and xmp.sidecar_for(f, root) == short:
            found.append((f, short))
            seen.add(short)
    return found


# ---------------------------------------------------------------------------
# Favourites and edits (ORG-02, ORG-04, MET-02)
# ---------------------------------------------------------------------------


def set_favourite(ids: Iterable[Any], value: bool) -> Operation:
    assets = _assets(ids)
    verb = "Added" if value else "Removed"
    op = _new(OpKind.FAVOURITE, f"{verb} {_items(len(assets))} {'to' if value else 'from'} Favourites")
    return engine.run(op, [{"t": "asset_fields", "asset": a.pk, "set": {"favourite": value}} for a in assets])


def _capture(asset: Asset, local: str) -> tuple[datetime, dict]:
    """A local wall-clock time typed by the owner, in the asset's own offset."""
    try:
        naive = datetime.fromisoformat(local)
    except ValueError as exc:
        raise InvalidRequest("Use YYYY-MM-DD HH:MM:SS.", field="capturedAt") from exc
    if naive.tzinfo is not None:
        raise InvalidRequest("Give the local time, without an offset.", field="capturedAt")
    if not 1900 <= naive.year <= timezone.now().year + 1:
        raise InvalidRequest("That year is out of range.", field="capturedAt")
    if asset.capture_offset is not None:
        zone: Any = dt_timezone(timedelta(minutes=asset.capture_offset))
        offset = local_capture(asset)[1]
    else:
        zone, offset = ZoneInfo(settings.TIME_ZONE), None
    return naive.replace(tzinfo=zone), {"local": naive.isoformat(timespec="seconds"), "offset": offset}


def edit(
    ids: Iterable[Any],
    *,
    fields: dict[str, Any] | None = None,
    add_tags: Iterable[str] = (),
    remove_tags: Iterable[str] = (),
) -> Operation:
    """Change fields and tags; the database now, the files in the worker."""
    fields = dict(fields or {})
    add = [t.strip()[: organise.MAX_TAG] for t in add_tags if t and t.strip()]
    remove = [t.strip() for t in remove_tags if t and t.strip()]
    unknown = set(fields) - {"title", "description", "place", "rating", "capturedAt"}
    if unknown:
        raise InvalidRequest(f"{', '.join(sorted(unknown))} cannot be edited.")
    if not fields and not add and not remove:
        raise InvalidRequest("Nothing to change.")
    if "rating" in fields and (not isinstance(fields["rating"], int) or not 0 <= fields["rating"] <= 5):
        raise InvalidRequest("A rating is 0 to 5 stars.", field="rating")
    for text, limit in (("title", 500), ("description", 5000), ("place", 255)):
        if text in fields:
            if not isinstance(fields[text], str):
                raise InvalidRequest("Text, please.", field=text)
            fields[text] = fields[text].strip()
            if len(fields[text]) > limit:
                raise InvalidRequest(f"At most {limit} characters.", field=text)
    assets = _assets(ids)

    db_plan: list[dict] = []
    file_values: dict[int, dict] = {}
    for asset in assets:
        changes: dict[str, Any] = {k: fields[k] for k in ("title", "description", "place", "rating") if k in fields}
        values = dict(changes)
        if "capturedAt" in fields:
            when, date_value = _capture(asset, str(fields["capturedAt"]))
            changes.update(capture_at=when.isoformat(), capture_source="metadata", date_edited=True)
            values["date"] = date_value
        if changes:
            db_plan.append({"t": "asset_fields", "asset": asset.pk, "set": changes})
        if add or remove:
            db_plan.append({"t": "asset_tags", "asset": asset.pk, "add": add, "remove": remove})
            values["keywords"] = None  # filled in after the tag step, below
        file_values[asset.pk] = values

    op = _new(OpKind.EDIT, _edit_summary(fields, add, remove, assets), {"fields": fields, "add": add, "remove": remove})
    engine.run(op, db_plan, finish=False)
    if op.state == OpState.FAILED:
        return op

    plan: list[dict] = []
    for asset in assets:
        values = file_values[asset.pk]
        if "keywords" in values:
            values["keywords"] = organise.tags_of(asset)
        for f in _files(asset):
            plan.append({"t": "xmp", "file": f.pk, "values": values})
    return _queue(op, plan) if plan else engine.run(op, [])


def _edit_summary(fields: dict, add: list[str], remove: list[str], assets: list[Asset]) -> str:
    what = assets[0].primary_file.name if len(assets) == 1 and assets[0].primary_file else _items(len(assets))
    if "rating" in fields and len(fields) == 1 and not add and not remove:
        stars = fields["rating"]
        return f"Rated {what} {'★' * stars}" if stars else f"Cleared the rating on {what}"
    if add and not fields and not remove:
        return f"Tagged {what} “{', '.join(add)}”"
    if remove and not fields and not add:
        return f"Removed “{', '.join(remove)}” from {what}"
    names = {"title": "title", "description": "description", "place": "location", "capturedAt": "date"}
    changed = [names[k] for k in fields if k in names] + (["tags"] if add or remove else [])
    return f"Changed the {' and '.join(changed) or 'details'} of {what}"


# ---------------------------------------------------------------------------
# Albums (ORG-01) - database only
# ---------------------------------------------------------------------------


def _album_name(name: Any) -> str:
    if not isinstance(name, str) or not name.strip():
        raise InvalidRequest("An album needs a name.", field="name")
    if len(name.strip()) > organise.MAX_ALBUM:
        raise InvalidRequest(f"At most {organise.MAX_ALBUM} characters.", field="name")
    return name.strip()


def _album(album_id: Any) -> Album:
    album = Album.objects.filter(pk=album_id).first()
    if album is None:
        raise NotFoundError("That album no longer exists.")
    return album


def add_to_album(ids: Iterable[Any], name: Any) -> Operation:
    """Add to the album with this name, creating it if there is none."""
    name = _album_name(name)
    assets = _assets(ids)
    existing = organise.album_named(name)
    plan: list[dict] = [] if existing else [{"t": "album_create", "name": name}]
    plan.append({"t": "album_add", "album_name": name, "assets": [a.pk for a in assets]})
    op = _new(OpKind.ALBUM, f"Added {_items(len(assets))} to {existing.name if existing else name}")
    return engine.run(op, plan)


def remove_from_album(album_id: Any, ids: Iterable[Any]) -> Operation:
    album = _album(album_id)
    assets = _assets(ids)
    op = _new(OpKind.ALBUM, f"Removed {_items(len(assets))} from {album.name}")
    return engine.run(op, [{"t": "album_remove", "album": album.pk, "assets": [a.pk for a in assets]}])


def _name_free(name: str, except_id: int | None = None) -> None:
    clash = organise.album_named(name)
    if clash is not None and clash.pk != except_id:
        raise ConflictError(f"There is already an album called “{clash.name}”.", field="name")


def create_album(name: Any) -> Operation:
    name = _album_name(name)
    _name_free(name)
    return engine.run(_new(OpKind.ALBUM, f"Created the album {name}"), [{"t": "album_create", "name": name}])


def rename_album(album_id: Any, name: Any) -> Operation:
    album = _album(album_id)
    name = _album_name(name)
    _name_free(name, except_id=album.pk)
    op = _new(OpKind.ALBUM, f"Renamed the album {album.name} to {name}")
    return engine.run(op, [{"t": "album_rename", "album": album.pk, "name": name}])


def delete_album(album_id: Any) -> Operation:
    album = _album(album_id)
    op = _new(OpKind.ALBUM, f"Deleted the album {album.name} (its photos stay in the library)")
    return engine.run(op, [{"t": "album_delete", "album": album.pk}])


# ---------------------------------------------------------------------------
# Trash, restore, purge (FOP-02)
# ---------------------------------------------------------------------------


def _trash_path(op_id: int, rel: str, base: str) -> str:
    inner = rel[len(base) + 1:] if base and rel.startswith(base + "/") else rel
    return f"{base}/{TRASH_DIR}/{op_id}/{inner}" if base else f"{TRASH_DIR}/{op_id}/{inner}"


def trash(ids: Iterable[Any]) -> Operation:
    assets = _assets(ids)
    root = library_root()
    base = _library_folder()
    days = AppSettings.load().trash_retention_days
    op = _new(OpKind.TRASH, f"Moved {_items(len(assets))} to Trash", {"retention_days": days})
    plan: list[dict] = []
    for asset in assets:
        files = _files(asset)
        for f in files:
            plan.append({"t": "move_file", "file": f.pk, "mode": "trash", "to": _trash_path(op.pk, f.rel_path, base)})
        for owner, sidecar in _sidecars(asset, files, root):
            plan.append({"t": "move_path", "owner": owner.pk, "from": sidecar, "to": _trash_path(op.pk, sidecar, base)})
    return _queue(op, plan)


def restore(ids: Iterable[Any]) -> Operation:
    assets = _assets(ids, trashed=True)
    op = _new(OpKind.RESTORE, f"Restored {_items(len(assets))} from Trash")
    plan: list[dict] = []
    for asset in assets:
        for f in _files(asset, trashed=True):
            plan.append({"t": "move_file", "file": f.pk, "mode": "restore", "to": f.trashed_from})
            for done in (f.trash_operation.steps if f.trash_operation else []):
                if done["t"] == "move_path" and done.get("owner") == f.pk and not done.get("skipped"):
                    plan.append({"t": "move_path", "owner": f.pk, "from": done["to"], "to": done["from"]})
    return _queue(op, plan)


def purge(ids: Iterable[Any] | None = None, *, older_than_days: int | None = None) -> Operation | None:
    """Delete from the trash for good: some items, everything past the
    retention period, or (with neither) the whole trash."""
    if ids is not None:
        assets = _assets(ids, trashed=True)
        files = [f for a in assets for f in _files(a, trashed=True)]
        summary = f"Deleted {_items(len(assets))} for good"
    else:
        qs = MediaFile.objects.filter(state=FileState.TRASHED)
        if older_than_days is not None:
            qs = qs.filter(trashed_at__lt=timezone.now() - timedelta(days=older_than_days))
        files = list(qs.order_by("pk"))
        n = len({f.asset_id for f in files})
        summary = (
            f"Emptied {_items(n)} from Trash after {older_than_days} days"
            if older_than_days is not None
            else f"Emptied the Trash ({_items(n)})"
        )
    if not files:
        if ids is None:
            return None
        raise InvalidRequest("Nothing to delete.")
    op = _new(OpKind.PURGE, summary)
    Operation.objects.filter(pk=op.pk).update(undoable=False)
    op.undoable = False
    return _queue(op, [{"t": "purge_file", "file": f.pk} for f in files])


def purge_due(now: datetime | None = None) -> Operation | None:
    """Scheduled: empty what has been in the trash past the retention period."""
    if Operation.objects.filter(kind=OpKind.PURGE, state=OpState.PENDING).exists():
        return None
    days = AppSettings.load().trash_retention_days
    return purge(older_than_days=days)


# ---------------------------------------------------------------------------
# Move and rename (FOP-01)
# ---------------------------------------------------------------------------


def move(ids: Iterable[Any], folder: Any) -> Operation:
    """Move items' files (and sidecars) into a folder of the library."""
    if not isinstance(folder, str):
        raise InvalidRequest("Choose a folder.", field="folder")
    inside = check_folder(folder)
    base = _library_folder()
    target = "/".join(p for p in (base, inside) if p)
    assets = _assets(ids)
    root = library_root()
    shown = inside or "the library folder"
    op = _new(OpKind.MOVE, f"Moved {_items(len(assets))} to {shown}", {"folder": inside})
    plan: list[dict] = []
    for asset in assets:
        files = _files(asset)
        for f in files:
            if f.rel_path.rpartition("/")[0] != target:
                plan.append({"t": "move_file", "file": f.pk, "mode": "move", "to": f"{target}/{f.name}" if target else f.name})
        for owner, sidecar in _sidecars(asset, files, root):
            if sidecar.rpartition("/")[0] != target:
                name = sidecar.rpartition("/")[2]
                plan.append({"t": "move_path", "owner": owner.pk, "from": sidecar, "to": f"{target}/{name}" if target else name})
    if not plan:
        raise InvalidRequest(f"Everything selected is already in {shown}.", field="folder")
    return _queue(op, plan)


def rename(asset_id: Any, name: Any) -> Operation:
    """Rename an item's files - a RAW+JPEG pair together - keeping extensions."""
    if not isinstance(name, str):
        raise InvalidRequest("A name is needed.", field="name")
    (asset,) = _assets([asset_id])
    stem = check_name(name)
    files = _files(asset)
    root = library_root()
    for f in files:
        ext = f.name.rsplit(".", 1)[1] if "." in f.name else ""
        if stem.lower().endswith(f".{ext.lower()}"):
            stem = stem[: -(len(ext) + 1)]
    plan: list[dict] = []
    for f in files:
        folder, _, old = f.rel_path.rpartition("/")
        ext = old.rsplit(".", 1)[1] if "." in old else ""
        new = f"{stem}.{ext}" if ext else stem
        if new != old:
            plan.append({"t": "move_file", "file": f.pk, "mode": "move", "to": f"{folder}/{new}" if folder else new})
    for owner, sidecar in _sidecars(asset, files, root):
        folder, _, old = sidecar.rpartition("/")
        owner_ext = owner.name.rsplit(".", 1)[1] if "." in owner.name else ""
        new = f"{stem}.{owner_ext}.xmp" if old.lower() == f"{owner.name.lower()}.xmp" else f"{stem}.xmp"
        if new != old:
            plan.append({"t": "move_path", "owner": owner.pk, "from": sidecar, "to": f"{folder}/{new}" if folder else new})
    if not plan:
        raise InvalidRequest("That is already its name.", field="name")
    primary = asset.primary_file.name if asset.primary_file else "the item"
    return _queue(_new(OpKind.RENAME, f"Renamed {primary} to {stem}"), plan)


# ---------------------------------------------------------------------------
# Undo, and the worker's side
# ---------------------------------------------------------------------------


def get(op_id: Any) -> Operation:
    op = Operation.objects.filter(pk=op_id).first()
    if op is None:
        raise NotFoundError("That change is not in the log.")
    return op


def undo(op_id: Any) -> Operation:
    op = get(op_id)
    if op.state not in (OpState.APPLIED,):
        raise engine.CannotUndo(f"This change is {op.state}, so there is nothing to undo.")
    if not op.undoable:
        raise engine.CannotUndo("This can no longer be undone: the trash it relies on has been emptied.")
    if engine.needs_worker(op.steps):
        Operation.objects.filter(pk=op.pk).update(state=OpState.UNDOING)
        op.state = OpState.UNDOING
        enqueue("operation_undo", {"op": op.pk})
        return op
    return engine.undo(op)


def operation_job(job) -> None:
    op = Operation.objects.filter(pk=job.payload["op"]).first()
    if op is None or op.state != OpState.PENDING:
        return
    if op.request.get("worker_started"):
        # A previous run of this job died part-way: reverse what it did.
        engine.recover(op)
        return
    op.request = {**op.request, "worker_started": True}
    Operation.objects.filter(pk=op.pk).update(request=op.request)
    engine.run(op, op.request.get("plan", []), reversible=op.kind != OpKind.PURGE)


def operation_undo_job(job) -> None:
    op = Operation.objects.filter(pk=job.payload["op"]).first()
    if op is not None and op.state == OpState.UNDOING:
        engine.undo(op)
