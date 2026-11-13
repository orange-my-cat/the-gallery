"""Queueing, claiming and finishing jobs, and the worker loop that ties them.

A handler is a function taking the claimed `Job`. It returns normally on
success (the row is deleted), raises `Retry` to be run again later without
using up an attempt, or raises anything else to fail the attempt. A job gets
three attempts with backoff, then stays `failed` for the status view (LIB-06).

Several worker processes run side by side safely: claiming is a single
`SELECT ... FOR UPDATE SKIP LOCKED` in its own transaction, so no two processes
ever hold the same row.
"""

from __future__ import annotations

import logging
import os
import socket
import threading
import time
import traceback
from collections.abc import Callable, Iterable
from datetime import datetime, timedelta

from django.db import close_old_connections, connection, transaction
from django.db.models import F, Q
from django.utils import timezone
from django.utils.module_loading import import_string

from jobs.models import Job, JobState, WorkerBeat

logger = logging.getLogger("gallery")

# Dotted paths rather than imports: the queue knows job kinds by name only, so
# `jobs` never imports the apps whose work it runs.
HANDLERS: dict[str, str] = {
    "scan_walk": "library.services.scan.walk_job",
    "scan_reconcile": "library.services.scan.reconcile_job",
    "index_file": "library.services.indexer.index_file_job",
    "thumb_small": "library.services.thumbnails.thumb_small_job",
    "thumb_large": "library.services.thumbnails.thumb_large_job",
    "operation": "fileops.services.ops.operation_job",
    "operation_undo": "fileops.services.ops.operation_undo_job",
    "fingerprint": "duplicates.services.fingerprint.fingerprint_job",
    "dedupe": "duplicates.services.detect.dedupe_job",
    "load_places": "places.services.geocode.load_job",
    "geocode": "places.services.geocode.geocode_job",
}

# HLD, "Jobs by priority". The walk goes first so the queue fills; reconcile
# shares index_file's priority but waits for the scan's index jobs itself.
PRIORITY: dict[str, int] = {
    # The owner is waiting on these: ahead of everything a scan queued.
    "operation": 0,
    "operation_undo": 0,
    "scan_walk": 0,
    "index_file": 1,
    "scan_reconcile": 1,
    "thumb_small": 2,
    "thumb_large": 3,
    "fingerprint": 4,
    "load_places": 5,
    "geocode": 5,
    # Last: it should see every hash the scan's files are getting.
    "dedupe": 6,
}

# Called on every turn of the loop (rate-limited): the worker is also the
# scheduler (HLD, "Worker loop") - rescans, and emptying old trash (FOP-02).
SCHEDULERS = (
    "library.services.scan.schedule_due",
    "fileops.services.ops.purge_due",
    # Fingerprints for files indexed before duplicate detection existed.
    "duplicates.services.detect.schedule_due",
    # Place names: load GeoNames once, then name what has a position.
    "places.services.geocode.schedule_due",
)
SCHEDULE_EVERY = timedelta(seconds=30)

MAX_ATTEMPTS = 3
# A backstop for a job whose process is still beating but has not finished it
# in this long - a hung tool, say. A dead process's jobs are recovered much
# sooner, by its missing heartbeat (`recover_stale`).
STALE_AFTER = timedelta(minutes=30)


class Retry(Exception):  # noqa: N818 - a signal, not an error
    """Run this job again after `delay` seconds without counting an attempt."""

    def __init__(self, delay: float = 2.0) -> None:
        super().__init__(f"retry in {delay}s")
        self.delay = delay


def _backoff(attempts: int) -> timedelta:
    # 30 s after the first failure, 2 min after the second.
    return timedelta(seconds=30 * 4 ** max(attempts - 1, 0))


# ---------------------------------------------------------------------------
# Queueing
# ---------------------------------------------------------------------------


def enqueue(kind: str, payload: dict | None = None, *, scan_id: int | None = None, delay: float = 0) -> Job:
    if kind not in HANDLERS:
        raise ValueError(f"Unknown job kind {kind!r}")
    return Job.objects.create(
        kind=kind,
        payload=payload or {},
        priority=PRIORITY.get(kind, 5),
        run_after=timezone.now() + timedelta(seconds=delay),
        scan_id=scan_id,
    )


def enqueue_many(kind: str, payloads: Iterable[dict], *, scan_id: int | None = None, batch: int = 1000) -> int:
    if kind not in HANDLERS:
        raise ValueError(f"Unknown job kind {kind!r}")
    now = timezone.now()
    priority = PRIORITY.get(kind, 5)
    rows = (
        Job(kind=kind, payload=p, priority=priority, run_after=now, scan_id=scan_id)
        for p in payloads
    )
    count = 0
    chunk: list[Job] = []
    for row in rows:
        chunk.append(row)
        if len(chunk) >= batch:
            Job.objects.bulk_create(chunk)
            count += len(chunk)
            chunk = []
    if chunk:
        Job.objects.bulk_create(chunk)
        count += len(chunk)
    return count


# ---------------------------------------------------------------------------
# Claiming and finishing
# ---------------------------------------------------------------------------


def claim(worker_id: str, limit: int = 1) -> list[Job]:
    now = timezone.now()
    with transaction.atomic():
        jobs = list(
            Job.objects.select_for_update(skip_locked=True)
            .filter(state=JobState.QUEUED, run_after__lte=now)
            .order_by("priority", "id")[:limit]
        )
        if not jobs:
            return []
        Job.objects.filter(pk__in=[j.pk for j in jobs]).update(
            state=JobState.RUNNING,
            locked_at=now,
            locked_by=worker_id,
            attempts=F("attempts") + 1,
        )
    for job in jobs:
        job.state = JobState.RUNNING
        job.locked_at = now
        job.locked_by = worker_id
        job.attempts += 1
    return jobs


def run(job: Job) -> str:
    """Run one claimed job to an outcome: 'done', 'retry', 'requeued' or 'failed'."""
    try:
        handler: Callable[[Job], object] = import_string(HANDLERS[job.kind])
        handler(job)
    except Retry as signal:
        Job.objects.filter(pk=job.pk).update(
            state=JobState.QUEUED,
            run_after=timezone.now() + timedelta(seconds=signal.delay),
            attempts=F("attempts") - 1,
            locked_at=None,
            locked_by="",
        )
        return "retry"
    except Exception as exc:  # noqa: BLE001 - any failure is recorded, never fatal to the worker
        return _fail(job, exc)
    Job.objects.filter(pk=job.pk).delete()
    return "done"


def _fail(job: Job, exc: BaseException) -> str:
    detail = "".join(traceback.format_exception_only(type(exc), exc)).strip()
    if job.attempts >= MAX_ATTEMPTS:
        logger.warning("job %s %s failed for good: %s", job.kind, job.pk, detail)
        Job.objects.filter(pk=job.pk).update(
            state=JobState.FAILED, error=detail, locked_at=None, locked_by=""
        )
        return "failed"
    logger.info("job %s %s attempt %s failed: %s", job.kind, job.pk, job.attempts, detail)
    Job.objects.filter(pk=job.pk).update(
        state=JobState.QUEUED,
        run_after=timezone.now() + _backoff(job.attempts),
        error=detail,
        locked_at=None,
        locked_by="",
    )
    return "requeued"


def recover_stale(now=None) -> int:
    """Queue again any job left `running` by a process that is gone.

    Every process beats from its own thread, through long jobs too, so one
    that has not beaten for BEAT_STALE is dead - killed, or its container
    removed by a deploy - and whatever it held is an orphan. A freshly claimed
    job gets the same grace, so a process that has only just started is never
    mistaken for a dead one. Runs on every scheduling turn, not only at
    startup: the processes that notice an orphan are usually the ones that
    started seconds after its owner died, before it looked dead.
    """
    now = now or timezone.now()
    alive = WorkerBeat.objects.filter(beat_at__gte=now - BEAT_STALE).values("worker_id")
    orphaned = Q(locked_at__lt=now - BEAT_STALE) & ~Q(locked_by__in=alive)
    hung = Q(locked_at__lt=now - STALE_AFTER)
    return (
        Job.objects.filter(state=JobState.RUNNING)
        .filter(orphaned | hung)
        .update(state=JobState.QUEUED, locked_at=None, locked_by="")
    )


def retry_failed(scan_id: int | None = None) -> int:
    """Give failed jobs a fresh set of attempts."""
    qs = Job.objects.filter(state=JobState.FAILED)
    if scan_id is not None:
        qs = qs.filter(scan_id=scan_id)
    return qs.update(state=JobState.QUEUED, attempts=0, run_after=timezone.now(), error="")


# ---------------------------------------------------------------------------
# The loop
# ---------------------------------------------------------------------------


def worker_id() -> str:
    return f"{socket.gethostname()}:{os.getpid()}"[:64]


# A worker beats this often, and counts as gone after missing a few.
BEAT_EVERY = timedelta(seconds=10)
BEAT_STALE = timedelta(seconds=45)


def beat(me: str, started: datetime, now: datetime | None = None) -> None:
    now = now or timezone.now()
    WorkerBeat.objects.update_or_create(worker_id=me, defaults={"beat_at": now, "started_at": started})
    # Old processes' rows: keep a day, for the record, then let them go.
    WorkerBeat.objects.filter(beat_at__lt=now - timedelta(days=1)).delete()


class Heartbeat:
    """Beats from a background thread for as long as the process works.

    Beating between jobs would leave a process silent for as long as its
    current job takes - minutes, for a large video - and it would look dead
    both to the status view and to `recover_stale`, which would hand its job
    to another process. The thread has its own database connection, closed
    when it stops.
    """

    def __init__(self, me: str, started: datetime) -> None:
        self._me = me
        self._started = started
        self._stopping = threading.Event()
        self._thread = threading.Thread(target=self._run, name="heartbeat", daemon=True)

    def __enter__(self) -> Heartbeat:
        # The first beat lands before anything is claimed.
        self._beat()
        self._thread.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self._stopping.set()
        self._thread.join(timeout=10)

    def _beat(self) -> None:
        try:
            beat(self._me, self._started)
        except Exception:  # noqa: BLE001 - a missed beat is not worth stopping for
            logger.exception("heartbeat failed")

    def _run(self) -> None:
        try:
            while not self._stopping.wait(BEAT_EVERY.total_seconds()):
                self._beat()
                close_old_connections()  # drops this thread's connection if it broke
        finally:
            connection.close()


def worker_health(now: datetime | None = None) -> dict:
    """{'online': bool, 'processes': n, 'lastSeenAt': iso or None}."""
    now = now or timezone.now()
    latest = WorkerBeat.objects.order_by("-beat_at").first()
    alive = WorkerBeat.objects.filter(beat_at__gte=now - BEAT_STALE).count()
    return {
        "online": alive > 0,
        "processes": alive,
        "lastSeenAt": latest.beat_at.isoformat() if latest else None,
    }


def work(
    *,
    stop: Callable[[], bool] = lambda: False,
    once: bool = False,
    idle_sleep: float = 1.0,
    schedule: bool = True,
) -> int:
    """Claim and run jobs until `stop()` is true. Returns the number run.

    With `once`, return as soon as the queue has nothing runnable - for tests
    and for draining a queue by hand.
    """
    me = worker_id()
    ran = 0
    schedulers = [import_string(path) for path in SCHEDULERS] if schedule else []
    started = timezone.now()
    next_schedule = started

    with Heartbeat(me, started):
        while not stop():
            close_old_connections()
            if timezone.now() >= next_schedule:
                # Every process recovers orphans, scheduling or not.
                try:
                    recover_stale()
                except Exception:  # noqa: BLE001 - try again next turn
                    logger.exception("recovering stale jobs failed")
                for scheduler in schedulers:
                    try:
                        scheduler()
                    except Exception:  # noqa: BLE001 - one failing schedule must not stop the others
                        logger.exception("scheduler failed")
                next_schedule = timezone.now() + SCHEDULE_EVERY

            jobs = claim(me)
            if not jobs:
                if once:
                    break
                time.sleep(idle_sleep)
                continue
            for job in jobs:
                run(job)
                ran += 1
    return ran
