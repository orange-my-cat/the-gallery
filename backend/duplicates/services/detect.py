"""Finding duplicate groups (DUP-01).

Only pixel-identical copies are duplicates (the owner's rule, 2026-11-14;
it replaces the BRD's visual matching, DUP-02). Two kinds of group:

- `exact` - byte-identical files of different items, any media type.
- `pixels` - photos whose decoded pixels are identical though their bytes are
  not: the same picture with different metadata (a title written into one, a
  tool that rewrote the tags). See `fingerprint`.

Photos that merely look alike - resized, re-compressed, a burst - are never
grouped. A RAW and the JPEG it was paired with, or a Live Photo's still and
clip, are one item, and so never a group.

`recompute()` rebuilds every open group from the files as they are now: two
passes over the stored hashes, cheap enough to run after every scan. A group
keeps its id while its members stay the same, so the screen does not jump
while the owner reviews it; a dismissed group is not raised again unless
something new joins it.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass, field

from django.db import connection, transaction
from django.utils import timezone

from core.models import AppSettings
from jobs.models import Job, JobState
from jobs.services import Retry, enqueue
from library.models import FileState, MediaFile

from duplicates.models import DuplicateGroup, DuplicateMember, GroupKind, GroupState
from duplicates.services import members as member_info
from duplicates.services.fingerprint import decodes
from duplicates.services.rules import ranked

logger = logging.getLogger("gallery")

# Fingerprinting files indexed before it existed, a batch per scheduler turn.
BACKFILL_BATCH = 2000
# Merges DUP-06 starts by itself in one pass; the next pass takes the rest.
AUTO_MERGE_BATCH = 100
_LOCK = 0x6475_7065  # "dupe": one recompute at a time


# ---------------------------------------------------------------------------
# Scheduling
# ---------------------------------------------------------------------------


def request_dedupe() -> None:
    """Queue a recompute unless one is already waiting to run."""
    if not Job.objects.filter(kind="dedupe", state=JobState.QUEUED).exists():
        enqueue("dedupe", {})


def _fingerprinting() -> bool:
    return Job.objects.filter(kind="fingerprint", state__in=[JobState.QUEUED, JobState.RUNNING]).exists()


def schedule_due(now=None) -> int:
    """Worker scheduler: fingerprint files indexed before fingerprints existed
    (or whose job was lost). Returns how many were queued or filled in."""
    if _fingerprinting():
        return 0
    batch = list(MediaFile.objects.filter(state=FileState.PRESENT, pixel_hash="").order_by("pk")[:BACKFILL_BATCH])
    for media in batch:
        queue_fingerprint(media)
    return len(batch)


def queue_fingerprint(media: MediaFile, *, scan_id: int | None = None) -> None:
    """After indexing: RAW and video match by bytes at once; a photo reuses a
    byte-identical copy's fingerprint, or is queued to be decoded."""
    if media.pixel_hash:
        return
    if not decodes(media):
        known = media.sha256
    else:
        known = (
            MediaFile.objects.filter(sha256=media.sha256).exclude(pixel_hash="").values_list("pixel_hash", flat=True).first()
        )
    if known:
        MediaFile.objects.filter(pk=media.pk).update(pixel_hash=known)
        media.pixel_hash = known
    else:
        enqueue("fingerprint", {"file": media.pk}, scan_id=scan_id)


# ---------------------------------------------------------------------------
# Matching
# ---------------------------------------------------------------------------


class _Union:
    def __init__(self) -> None:
        self.parent: dict[int, int] = {}

    def find(self, x: int) -> int:
        self.parent.setdefault(x, x)
        root = x
        while self.parent[root] != root:
            root = self.parent[root]
        while self.parent[x] != root:
            self.parent[x], x = root, self.parent[x]
        return root

    def join(self, a: int, b: int) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.parent[max(ra, rb)] = min(ra, rb)

    def groups(self) -> list[list[int]]:
        out: dict[int, list[int]] = defaultdict(list)
        for x in self.parent:
            out[self.find(x)].append(x)
        return [sorted(g) for g in out.values() if len(g) > 1]


@dataclass
class Found:
    kind: str
    assets: list[int]
    # asset id -> the file that matched
    files: dict[int, int] = field(default_factory=dict)

    @property
    def key(self) -> str:
        return ",".join(map(str, sorted(self.assets)))


def find() -> list[Found]:
    """Every group of identical copies the library holds now."""
    present = list(MediaFile.objects.filter(state=FileState.PRESENT).values_list("pk", "asset_id", "sha256", "pixel_hash"))
    joined = _Union()
    matched: dict[int, int] = {}
    pixel_only: set[int] = set()

    by_bytes: dict[str, list[tuple[int, int]]] = defaultdict(list)
    by_pixels: dict[str, list[tuple[int, int, str]]] = defaultdict(list)
    for pk, asset_id, sha, pixels in present:
        by_bytes[sha].append((pk, asset_id))
        if pixels:
            by_pixels[pixels].append((pk, asset_id, sha))

    for copies in by_bytes.values():
        assets = sorted({a for _, a in copies})
        if len(assets) < 2:
            continue
        for pk, asset_id in copies:
            matched.setdefault(asset_id, pk)
        for other in assets[1:]:
            joined.join(assets[0], other)

    for copies in by_pixels.values():
        assets = sorted({a for _, a, _ in copies})
        if len(assets) < 2 or len({sha for _, _, sha in copies}) < 2:
            continue  # one item, or byte-identical: the pass above has them
        for pk, asset_id, _ in copies:
            matched.setdefault(asset_id, pk)
            pixel_only.add(asset_id)
        for other in assets[1:]:
            joined.join(assets[0], other)

    return [
        Found(
            kind=GroupKind.PIXELS if any(a in pixel_only for a in group) else GroupKind.EXACT,
            assets=group,
            files={a: matched[a] for a in group if a in matched},
        )
        for group in joined.groups()
    ]


# ---------------------------------------------------------------------------
# Storing
# ---------------------------------------------------------------------------


def _title(assets: list[int], info: dict[int, member_info.Member]) -> str:
    best = ranked([info[a].copy for a in assets if a in info])
    name = info[best[0].id].display.name if best and info[best[0].id].display else "this picture"
    stem = name.rsplit(".", 1)[0] if "." in name else name
    return f"{len(assets)} copies of {stem}"


def recompute() -> dict[str, int]:
    """Bring the open groups up to date. Returns counts for the log."""
    found = find()
    info = member_info.load({a for f in found for a in f.assets})

    dismissed = [set(g.asset_ids) for g in DuplicateGroup.objects.filter(state=GroupState.DISMISSED)]
    opened = {g.key: g for g in DuplicateGroup.objects.filter(state=GroupState.OPEN)}
    keep: set[int] = set()
    created = updated = 0
    with transaction.atomic():
        for f in found:
            live = [a for a in f.assets if a in info]
            if len(live) < 2:
                continue
            if any(set(f.assets) <= d for d in dismissed):
                continue  # the owner said these are not duplicates
            title = _title(f.assets, info)
            group = opened.get(f.key)
            if group is None:
                group = DuplicateGroup.objects.create(kind=f.kind, key=f.key, title=title)
                DuplicateMember.objects.bulk_create(
                    [DuplicateMember(group=group, asset_id=a, file_id=f.files.get(a)) for a in f.assets]
                )
                created += 1
            elif group.kind != f.kind or group.title != title:
                DuplicateGroup.objects.filter(pk=group.pk).update(kind=f.kind, title=title, updated_at=timezone.now())
                updated += 1
            keep.add(group.pk)
        # Open groups that no longer exist - their copies were trashed, moved
        # apart or edited - go, unless a merge is under way on one of them.
        stale = DuplicateGroup.objects.filter(state=GroupState.OPEN).exclude(pk__in=keep).exclude(
            operation__state="pending"
        )
        removed = stale.count()
        stale.delete()
    return {"groups": len(keep), "created": created, "updated": updated, "removed": removed}


def dedupe_job(job: Job) -> None:
    # Fingerprints still being made would leave their files out: a group would
    # lose a copy (and its id) only to gain it back a moment later. Wait for
    # them; the last fingerprint job to finish asks for this run again anyway.
    if _fingerprinting():
        raise Retry(10)
    with connection.cursor() as cursor:
        cursor.execute("SELECT pg_try_advisory_lock(%s)", [_LOCK])
        (locked,) = cursor.fetchone()
    if not locked:
        raise Retry(30)  # another process is recomputing; look again shortly
    try:
        counts = recompute()
        logger.info("duplicates: %s", counts)
        if AppSettings.load().auto_merge_exact:
            from duplicates.services.review import merge_exact

            merge_exact(limit=AUTO_MERGE_BATCH)
    finally:
        with connection.cursor() as cursor:
            cursor.execute("SELECT pg_advisory_unlock(%s)", [_LOCK])

