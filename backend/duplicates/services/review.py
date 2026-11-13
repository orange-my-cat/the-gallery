"""The duplicate review queue and what the owner can do there (DUP-03 to DUP-06).

- `queue()` lists open groups whose members are still in the library.
- `detail()` is one group with the merge preview: the copies, the proposed
  keeper and why, and each field's merged value with its source and any
  conflict. The owner's keeper and choices are sent back to preview again.
- `merge()` plans the merge as one logged, undoable operation the worker
  carries out: re-check the files, update the kept item, write the merged
  metadata into its files (keeping their exact bytes aside), trash the other
  copies, carry their albums over, and mark the group merged. Any failure
  reverses every step already done.
- `dismiss()` settles a group without merging ("Not duplicates").

A copy can be the keeper only if it holds an identical copy of every file of
the others: an item with a RAW or a Live Photo clip of its own is never
trashed in favour of one without, since that file would go with it.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from django.conf import settings

from core.models import AppSettings
from core.services.exceptions import BusinessRuleError, ConflictError, NotFoundError
from fileops.models import Operation, OpKind, OpState
from fileops.services import engine
from fileops.services import ops as fileops
from library.models import Asset, FileRole
from library.services.browse import display_folder, media_version, visible_assets
from library.services.paths import RAW_EXTENSIONS, TRASH_DIR, extension
from organise.models import Album

from duplicates.models import DuplicateGroup, GroupKind, GroupState
from duplicates.services import members as member_info
from duplicates.services import rules

LETTERS = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
KIND_ORDER = {GroupKind.EXACT: 0, GroupKind.PIXELS: 1}


class InvalidRequest(BusinessRuleError):
    code = "validation_failed"


# ---------------------------------------------------------------------------
# The queue
# ---------------------------------------------------------------------------


def _pending(group: DuplicateGroup) -> bool:
    return group.operation_id is not None and group.operation is not None and group.operation.state == OpState.PENDING


def open_groups(kind: str | None = None) -> list[tuple[DuplicateGroup, list[int]]]:
    """Open groups with at least two members still in the library, in review order."""
    qs = DuplicateGroup.objects.filter(state=GroupState.OPEN).select_related("operation").prefetch_related("members")
    if kind:
        qs = qs.filter(kind=kind)
    groups = list(qs)
    ids = {m.asset_id for g in groups for m in g.members.all()}
    visible = set(visible_assets().filter(pk__in=ids).values_list("pk", flat=True))
    out = []
    for g in groups:
        live = [m.asset_id for m in g.members.all() if m.asset_id in visible]
        if len(live) >= 2:
            out.append((g, live))
    out.sort(key=lambda item: (KIND_ORDER.get(item[0].kind, 9), item[0].pk))
    return out


def _thumb(asset: Asset) -> str | None:
    primary = asset.primary_file
    if primary is None:
        return None
    return f"/media/thumb/{primary.pk}/400?v={media_version(primary.sha256)}"


def queue(kind: str | None = None) -> dict[str, Any]:
    everything = open_groups()
    shown = [(g, live) for g, live in everything if not kind or g.kind == kind]
    ids = {a for _, live in shown for a in live}
    assets = {a.pk: a for a in Asset.objects.filter(pk__in=ids).select_related("primary_file")}
    counts = {k: 0 for k in KIND_ORDER}
    for g, _ in everything:
        counts[g.kind] = counts.get(g.kind, 0) + 1
    return {
        "total": len(everything),
        "counts": counts,
        "exactReady": len(ready_exact(everything)),
        "groups": [
            {
                "id": str(g.pk),
                "kind": g.kind,
                "title": g.title,
                "count": len(live),
                "thumbs": [t for t in (_thumb(assets[a]) for a in live[:4] if a in assets) if t],
                "merging": _pending(g),
            }
            for g, live in shown
        ],
    }


# ---------------------------------------------------------------------------
# One group, previewed
# ---------------------------------------------------------------------------


def _group(group_id: Any) -> DuplicateGroup:
    try:
        pk = int(group_id)
    except (TypeError, ValueError):
        raise NotFoundError("That group does not exist.") from None
    group = DuplicateGroup.objects.select_related("operation").filter(pk=pk).first()
    if group is None:
        raise NotFoundError("That group no longer exists: the library changed since it was found. Reload the queue.")
    return group


def _live(group: DuplicateGroup) -> dict[int, member_info.Member]:
    ids = [m.asset_id for m in group.members.all()]
    visible = set(visible_assets().filter(pk__in=ids).values_list("pk", flat=True))
    return member_info.load([a for a in ids if a in visible])


def _kind_of_file(f) -> str:
    if f.role == FileRole.LIVE_VIDEO:
        return "Live Photo clip"
    if extension(f.rel_path) in RAW_EXTENSIONS:
        return "RAW file"
    return "file"


def blockers(info: dict[int, member_info.Member]) -> dict[int, str | None]:
    """For each copy, why it cannot be the keeper (None if it can): another
    copy has a file it holds no identical copy of."""
    out: dict[int, str | None] = {}
    for kid, keeper in info.items():
        out[kid] = None
        for nid, other in info.items():
            if nid == kid:
                continue
            for f in other.files:
                if f.sha256 in keeper.shas or (f.pixel_hash and f.pixel_hash in keeper.pixels):
                    continue
                out[kid] = f"Keeping this would send the {_kind_of_file(f)} {f.name} to Trash with its copy."
                break
            if out[kid]:
                break
    return out


def _format(member: member_info.Member) -> str:
    d = member.display
    ext = extension(d.rel_path) if d else ""
    name = {"jpg": "JPEG", "jpeg": "JPEG", "heic": "HEIC", "heif": "HEIF", "tif": "TIFF", "tiff": "TIFF"}.get(ext, ext.upper())
    extras = []
    for f in member.extras:
        if f.role == FileRole.LIVE_VIDEO:
            extras.append("Live Photo")
        elif extension(f.rel_path) in RAW_EXTENSIONS:
            extras.append(extension(f.rel_path).upper())
    return " + ".join([name, *extras])


def _modified(mtime_ns: int) -> str:
    local = datetime.fromtimestamp(mtime_ns / 1e9, ZoneInfo(settings.TIME_ZONE))
    return f"{local.day} {local:%b %Y, %H:%M}"


def _choices(raw: Any, ids: set[int]) -> dict[str, int]:
    if raw in (None, ""):
        return {}
    if not isinstance(raw, dict):
        raise InvalidRequest("Choices are {field: item id}.", field="choices")
    out: dict[str, int] = {}
    for name, value in raw.items():
        if name not in rules.FIELD_NAMES:
            raise InvalidRequest(f"{name} is not a merged field.", field="choices")
        try:
            pk = int(value)
        except (TypeError, ValueError):
            raise InvalidRequest("Choose a copy by its id.", field="choices") from None
        if pk not in ids:
            raise InvalidRequest("That copy is not in this group.", field="choices")
        out[name] = pk
    return out


def parse_choose(text: str) -> dict[str, str]:
    """`date:12,gps:15` (the query-string form) -> {"date": "12", "gps": "15"}."""
    out: dict[str, str] = {}
    for part in (text or "").split(","):
        name, _, value = part.partition(":")
        if name.strip():
            out[name.strip()] = value.strip()
    return out


class _Plan:
    """A group worked out: who is kept, and the merge result."""

    def __init__(self, group: DuplicateGroup, keeper: Any = None, choices: Any = None) -> None:
        self.group = group
        self.info = _live(group)
        if len(self.info) < 2:
            raise NotFoundError("This group no longer has two copies in the library.")
        self.blocked = blockers(self.info)
        allowed = {k for k, why in self.blocked.items() if why is None}
        copies = [m.copy for m in self.info.values()]
        if allowed:
            self.proposed, self.reason = rules.propose_keeper(copies, allowed)
        else:
            self.proposed, self.reason = rules.ranked(copies)[0].id, ""
        self.mergeable = bool(allowed)
        if keeper in (None, ""):
            self.keeper = self.proposed
        else:
            try:
                self.keeper = int(keeper)
            except (TypeError, ValueError):
                raise InvalidRequest("Choose the copy to keep.", field="keeper") from None
            if self.keeper not in self.info:
                raise InvalidRequest("That copy is not in this group.", field="keeper")
            if self.blocked.get(self.keeper):
                raise InvalidRequest(self.blocked[self.keeper] or "", field="keeper")
        self.choices = _choices(choices, set(self.info))
        self.result = rules.merge(copies, self.keeper, self.choices)


def detail(group_id: Any, keeper: Any = None, choices: Any = None) -> dict[str, Any]:
    group = _group(group_id)
    plan = _Plan(group, keeper, choices)
    order = [c.id for c in plan.result.copies]
    letter = {pk: LETTERS[i] if i < len(LETTERS) else "?" for i, pk in enumerate(order)}
    albums = dict(Album.objects.filter(pk__in=plan.result.albums).values_list("pk", "name"))

    queue_ids = [str(g.pk) for g, _ in open_groups()]
    here = queue_ids.index(str(group.pk)) if str(group.pk) in queue_ids else None

    def copy(pk: int) -> dict[str, Any]:
        m = plan.info[pk]
        d = m.display
        assert d is not None
        return {
            "assetId": str(pk),
            "letter": letter[pk],
            "name": d.name,
            "path": display_folder(d.rel_path),
            "width": d.width or 0,
            "height": d.height or 0,
            "size": sum(f.size for f in m.files),
            "format": _format(m),
            "modified": _modified(d.mtime_ns),
            "thumbUrl": f"/media/thumb/{d.pk}/400?v={media_version(d.sha256)}",
            "previewUrl": f"/media/preview/{d.pk}?v={media_version(d.sha256)}",
            "cannotKeep": plan.blocked.get(pk),
            "favourite": m.copy.favourite,
        }

    rows = []
    for r in plan.result.rows:
        if r.field == "place" and r.mode == "none":
            continue  # shown only when some copy has a location
        rows.append(
            {
                "field": r.field,
                "label": r.label,
                "cells": [
                    {"text": t, "picked": c.id in r.picked}
                    for t, c in zip(r.texts, plan.result.copies, strict=True)
                ],
                "mode": r.mode,
                "merged": r.text,
                "sources": [letter[pk] for pk in r.picked] if r.source not in ("all copies agree",) else [],
                "sourceText": r.source,
                "conflict": r.unresolved,
                "resolved": r.resolved,
            }
        )
    op = group.operation
    return {
        "id": str(group.pk),
        "kind": group.kind,
        "title": group.title,
        "state": group.state,
        "operation": {"id": str(op.pk), "state": op.state, "error": op.error or None} if op else None,
        "position": {
            "index": here,
            "total": len(queue_ids),
            "prev": queue_ids[here - 1] if here is not None and here > 0 else None,
            "next": queue_ids[here + 1] if here is not None and here + 1 < len(queue_ids) else None,
        },
        "copies": [copy(pk) for pk in order],
        "keeper": str(plan.keeper),
        "proposedKeeper": str(plan.proposed),
        "keeperReason": plan.reason,
        "mergeable": plan.mergeable,
        "blocked": None if plan.mergeable else
        "No copy here can be kept without sending another’s RAW or Live Photo clip to Trash.",
        "rows": rows,
        "conflicts": plan.result.unresolved,
        "choices": {k: str(v) for k, v in plan.choices.items()},
        "favourite": plan.result.favourite,
        "albums": sorted(albums.values(), key=str.lower),
        "retentionDays": AppSettings.load().trash_retention_days,
    }


# ---------------------------------------------------------------------------
# Merging
# ---------------------------------------------------------------------------


def _same_text(a: str, b: str) -> bool:
    return " ".join(a.split()) == " ".join(b.split())


def changes_for(result: rules.Merge, keeper: rules.Copy) -> tuple[dict, dict, list[str]]:
    """(values for the files, asset fields for the database, tags to add) -
    only what differs from what the kept copy already has."""
    values: dict[str, Any] = {}
    fields: dict[str, Any] = {}

    def row(name: str) -> rules.Row:
        return result.row(name)

    for name in ("title", "description", "place"):
        r = row(name)
        if r.mode == "single" and not _same_text(r.value or "", getattr(keeper, name)):
            values[name] = r.value
            fields[name] = r.value
    r = row("rating")
    if r.mode == "single" and r.value != keeper.rating:
        values["rating"] = r.value
        fields["rating"] = r.value
    r = row("date")
    if r.mode == "single":
        at, local, offset = r.value
        if (at, offset) != (keeper.capture_at, keeper.capture_offset):
            values["date"] = {
                "local": local.isoformat(timespec="seconds"),
                "offset": rules._offset_text(offset).strip() or None,
            }
            fields.update(capture_at=at.isoformat(), capture_offset=offset, capture_source="metadata")
    r = row("gps")
    if r.mode == "single":
        lat, lon = r.value
        if (keeper.lat, keeper.lon) != (lat, lon):
            values["gps"] = [lat, lon]
            fields.update(lat=lat, lon=lon)
    r = row("camera")
    if r.mode == "single":
        make, model = r.value
        if (make, model) != (" ".join(keeper.camera_make.split()), " ".join(keeper.camera_model.split())):
            values.update(camera_make=make, camera_model=model)
            fields.update(camera_make=make, camera_model=model)
    r = row("lens")
    if r.mode == "single" and not _same_text(r.value or "", keeper.lens):
        values["lens"] = r.value
        fields["lens"] = r.value
    r = row("keywords")
    add: list[str] = []
    if r.mode in ("single", "union"):
        words = list(r.value or ())
        have = {w.lower() for w in keeper.keywords}
        if {w.lower() for w in words} != have:
            values["keywords"] = words
            add = [w for w in words if w.lower() not in have]
    return values, fields, add


def merge(group_id: Any, keeper: Any = None, choices: Any = None) -> Operation:
    group = _group(group_id)
    if group.state != GroupState.OPEN:
        raise ConflictError(f"This group is already {group.state}.")
    if _pending(group):
        raise ConflictError("A merge of this group is already under way.")
    plan = _Plan(group, keeper, choices)
    if not plan.mergeable:
        raise InvalidRequest("No copy here can be kept without sending another’s RAW or Live Photo clip to Trash.")
    if plan.result.unresolved:
        n = plan.result.unresolved
        raise InvalidRequest(f"Resolve {n} conflict{'s' if n != 1 else ''} first: pick a value in each highlighted row.")

    kept = plan.info[plan.keeper]
    others = [m for pk, m in plan.info.items() if pk != plan.keeper]
    values, fields, add = changes_for(plan.result, kept.copy)
    if plan.result.favourite and not kept.copy.favourite:
        fields["favourite"] = True

    n = len(plan.info)
    name = kept.display.name if kept.display else "the kept copy"
    op = Operation.objects.create(
        kind=OpKind.MERGE,
        summary=f"Merged {n} copies into {name}"[:300],
        request={"group": group.pk, "keeper": plan.keeper, "choices": plan.choices},
    )
    steps: list[dict] = [
        {
            "t": "merge_check",
            "files": [{"file": f.pk, "size": f.size, "mtime_ns": f.mtime_ns} for m in plan.info.values() for f in m.files],
        }
    ]
    if fields:
        steps.append({"t": "asset_fields", "asset": plan.keeper, "set": fields})
    if add:
        steps.append({"t": "asset_tags", "asset": plan.keeper, "add": add, "remove": []})
    if values:
        base = fileops._library_folder()
        for f in kept.files:
            backup = f"{base}/{TRASH_DIR}/{op.pk}/keeper-original/{f.pk}" if base else f"{TRASH_DIR}/{op.pk}/keeper-original/{f.pk}"
            steps.append({"t": "merge_write", "file": f.pk, "values": values, "backup_dir": backup})
    steps += fileops.trash_plan(op, [m.asset for m in others])
    for album_id in sorted(plan.result.albums - kept.copy.albums):
        steps.append({"t": "album_add", "album": album_id, "assets": [plan.keeper]})
    steps.append({"t": "dup_group", "group": group.pk, "state": GroupState.MERGED})

    DuplicateGroup.objects.filter(pk=group.pk).update(operation=op)
    return fileops._queue(op, steps)


def ready_exact(groups: list[tuple[DuplicateGroup, list[int]]] | None = None) -> list[DuplicateGroup]:
    """Exact groups that would merge as proposed with nothing to settle (DUP-06)."""
    out = []
    for group, _ in groups if groups is not None else open_groups(GroupKind.EXACT):
        if group.kind != GroupKind.EXACT or _pending(group):
            continue
        try:
            plan = _Plan(group)
        except BusinessRuleError:
            continue
        if plan.mergeable and not plan.result.unresolved:
            out.append(group)
    return out


def merge_exact(limit: int | None = None) -> list[Operation]:
    """Merge every conflict-free exact group as proposed, each its own undoable merge."""
    started = []
    for group in ready_exact()[:limit]:
        try:
            started.append(merge(group.pk))
        except BusinessRuleError:
            continue  # changed meanwhile; it will be looked at again
    return started


# ---------------------------------------------------------------------------
# Settling without a merge
# ---------------------------------------------------------------------------


def dismiss(group_id: Any) -> Operation:
    """"Not duplicates": the group is not raised again. Undoable."""
    group = _group(group_id)
    if group.state != GroupState.OPEN:
        raise ConflictError(f"This group is already {group.state}.")
    if _pending(group):
        raise ConflictError("A change to this group is already under way.")
    op = Operation.objects.create(kind=OpKind.DISMISS, summary=f"Marked {group.title} as not duplicates"[:300])
    return engine.run(op, [{"t": "dup_group", "group": group.pk, "state": GroupState.DISMISSED}])


def count_open() -> int:
    return len(open_groups())

