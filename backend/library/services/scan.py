"""Scanning: walk the folder, index what changed, reconcile what went away.

The HLD's two phases (LIB-03):

1. **Walk** - `os.scandir` over the library. A file whose path, size and
   modification time all match the database is only marked seen; anything else
   is handed to an `index_file` job. Nothing is hashed here, so a rescan of an
   unchanged 100,000-file library is a directory listing and one query.
2. **Reconcile** - once every `index_file` job of this scan has finished, any
   file the scan did not see is marked `missing` and drops out of views. Never
   deleted: it may come back, and then its asset comes back with it.

The app moves files while a scan runs (Trash, restore, move, rename), so the
walk's view of a file can be stale by the time it is written back. A trashed
file is never marked present again, and a file the app touched after the scan
started is never marked missing by it.

Moves (LIB-04) are recognised by `index_file` itself: a new path whose SHA-256
matches a known file that is no longer at its old path takes over that file's
row, so the asset - and everything attached to it - stays the same.
"""

from __future__ import annotations

import logging
import os
from datetime import timedelta
from pathlib import Path

from django.db import IntegrityError, transaction
from django.db.models import F
from django.utils import timezone

from core.models import AppSettings
from core.services.exceptions import BusinessRuleError
from jobs.models import Job, JobState
from jobs.services import Retry, enqueue, enqueue_many
from library.models import ACTIVE_SCAN_STATES, FileState, MediaFile, ScanRun, ScanState, ScanTrigger
from library.services.paths import (
    TMP_MARK,
    LibraryUnavailable,
    file_type,
    is_excluded,
    library_root,
    scan_root,
    to_rel,
)

logger = logging.getLogger("gallery")

# How long reconcile waits before looking again at a scan still indexing.
RECONCILE_POLL_SECONDS = 2.0
_BATCH = 5000


# ---------------------------------------------------------------------------
# Starting scans
# ---------------------------------------------------------------------------


def active_scan() -> ScanRun | None:
    return ScanRun.objects.filter(state__in=ACTIVE_SCAN_STATES).first()


def start_scan(trigger: str = ScanTrigger.MANUAL) -> ScanRun:
    """Queue a scan, or return the one already under way."""
    existing = active_scan()
    if existing is not None:
        return existing
    try:
        with transaction.atomic():
            scan = ScanRun.objects.create(
                trigger=trigger,
                first_scan=not MediaFile.objects.exists(),
            )
            enqueue("scan_walk", {"scan": scan.pk}, scan_id=scan.pk)
    except IntegrityError:
        # Another worker or request won the race; the constraint guarantees
        # there is exactly one active scan to return.
        return ScanRun.objects.get(state__in=ACTIVE_SCAN_STATES)
    logger.info("scan %s queued (%s)", scan.pk, trigger)
    return scan


def schedule_due(now=None) -> ScanRun | None:
    """Start a scheduled scan if the interval has passed. The worker calls this."""
    now = now or timezone.now()
    interval = AppSettings.load().scan_interval_minutes
    if not interval or active_scan() is not None:
        return None
    try:
        scan_root()
    except BusinessRuleError:
        return None  # no library yet (or not chosen): nothing to schedule
    last = ScanRun.objects.order_by("-started_at").first()
    if last is not None and last.started_at > now - timedelta(minutes=interval):
        return None
    return start_scan(ScanTrigger.SCHEDULE)


def _finish(scan_id: int, state: str, **fields) -> None:
    ScanRun.objects.filter(pk=scan_id).update(state=state, finished_at=timezone.now(), **fields)


# ---------------------------------------------------------------------------
# Walk
# ---------------------------------------------------------------------------


def _walk(top: Path, root: Path, exclusions: list[str], unreadable: list[str]):
    """Yield (rel_path, size, mtime_ns) for every supported file under `top`.

    Paths are relative to the root (what the database stores); exclusions are
    relative to `top`, the library folder, which is how the owner names them.
    """
    stack = [top]
    while stack:
        folder = stack.pop()
        try:
            entries = list(os.scandir(folder))
        except OSError as exc:
            rel = to_rel(root, folder) if folder != root else ""
            logger.warning("scan could not read %s: %s", folder, exc)
            unreadable.append(rel)
            continue
        for entry in entries:
            try:
                if entry.is_dir(follow_symlinks=False):
                    if not is_excluded(to_rel(top, entry.path), exclusions):
                        stack.append(Path(entry.path))
                elif entry.is_file(follow_symlinks=False):
                    # macOS resource forks copied onto Windows drives, and
                    # The Gallery's own write-in-progress copies.
                    if entry.name.startswith("._") or TMP_MARK in entry.name:
                        continue
                    rel = to_rel(root, entry.path)
                    if file_type(rel) is None:
                        continue
                    stat = entry.stat(follow_symlinks=False)
                    yield rel, stat.st_size, stat.st_mtime_ns
            except OSError as exc:
                logger.warning("scan could not read %s: %s", entry.path, exc)


def walk_job(job: Job) -> None:
    scan_id = job.payload["scan"]
    if not ScanRun.objects.filter(pk=scan_id, state__in=[ScanState.QUEUED, ScanState.WALKING]).update(
        state=ScanState.WALKING
    ):
        return  # finished or failed in the meantime

    try:
        root = library_root()
        top = scan_root(root)
    except LibraryUnavailable as exc:
        logger.warning("scan %s failed: %s", scan_id, exc.message)
        _finish(scan_id, ScanState.FAILED, error=exc.message)
        return

    exclusions = list(AppSettings.load().exclusions or [])
    known = {
        rel: (pk, size, mtime_ns)
        for pk, rel, size, mtime_ns in MediaFile.objects.exclude(state=FileState.TRASHED)
        .values_list("pk", "rel_path", "size", "mtime_ns")
        .iterator(chunk_size=_BATCH)
    }

    seen = 0
    unchanged: list[int] = []
    to_index: list[dict] = []
    unreadable: list[str] = []
    for rel, size, mtime_ns in _walk(top, root, exclusions, unreadable):
        seen += 1
        match = known.get(rel)
        if match is not None and match[1] == size and match[2] == mtime_ns:
            unchanged.append(match[0])
        else:
            to_index.append({"path": rel})

    for start in range(0, len(unchanged), _BATCH):
        # `known` was read before the walk; a file sent to Trash since then
        # must stay trashed, not be marked present inside .gallery-trash.
        MediaFile.objects.filter(pk__in=unchanged[start : start + _BATCH]).exclude(
            state=FileState.TRASHED
        ).update(
            last_seen_scan_id=scan_id, state=FileState.PRESENT, missed_scans=0
        )
    # A file that failed to index has no MediaFile row, so every scan tries it
    # again - and the new attempt supersedes the old failure. Without this an
    # unreadable file was counted once more as failed after every scan.
    for start in range(0, len(to_index), _BATCH):
        paths = [item["path"] for item in to_index[start : start + _BATCH]]
        Job.objects.filter(kind="index_file", state=JobState.FAILED, payload__path__in=paths).delete()
    queued = enqueue_many("index_file", to_index, scan_id=scan_id)
    ScanRun.objects.filter(pk=scan_id).update(state=ScanState.INDEXING, seen=seen, queued=queued)
    enqueue("scan_reconcile", {"scan": scan_id, "unreadable": unreadable}, scan_id=scan_id)
    logger.info("scan %s walked: %s files, %s to index", scan_id, seen, queued)


# ---------------------------------------------------------------------------
# Reconcile
# ---------------------------------------------------------------------------


def reconcile_job(job: Job) -> None:
    scan_id = job.payload["scan"]
    if Job.objects.filter(
        scan_id=scan_id, kind="index_file", state__in=[JobState.QUEUED, JobState.RUNNING]
    ).exists():
        raise Retry(RECONCILE_POLL_SECONDS)

    with transaction.atomic():
        # A file whose index job gave up was still found by the walk; it is not
        # missing, just unreadable, and it already shows as a failure.
        failed_paths = [
            p["path"]
            for p in Job.objects.filter(scan_id=scan_id, kind="index_file", state=JobState.FAILED).values_list(
                "payload", flat=True
            )
        ]
        for start in range(0, len(failed_paths), _BATCH):
            MediaFile.objects.filter(rel_path__in=failed_paths[start : start + _BATCH]).update(
                last_seen_scan_id=scan_id
            )

        # A folder the walk could not open says nothing about its files - and
        # if that was the library root itself, nothing is marked missing.
        unreadable: list[str] = job.payload.get("unreadable", [])
        newly_missing = 0
        if "" not in unreadable:
            unseen = MediaFile.objects.filter(state__in=[FileState.PRESENT, FileState.MISSING]).exclude(
                last_seen_scan_id=scan_id
            )
            for prefix in unreadable:
                unseen = unseen.exclude(rel_path__startswith=prefix + "/")
            # Restored, moved or renamed by the app after the walk passed its
            # new folder: not seen, but not missing either.
            started = ScanRun.objects.filter(pk=scan_id).values_list("started_at", flat=True).first()
            if started is not None:
                unseen = unseen.exclude(updated_at__gte=started)
            newly_missing = unseen.filter(state=FileState.PRESENT).count()
            unseen.update(state=FileState.MISSING, missed_scans=F("missed_scans") + 1)
        _finish(scan_id, ScanState.DONE, missing=newly_missing)
    logger.info("scan %s done: %s newly missing", scan_id, newly_missing)
