"""Claiming, retrying, failing and recovering jobs."""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.utils import timezone

from jobs import services
from jobs.models import Job, JobState
from jobs.services import Retry, beat, claim, enqueue, enqueue_many, recover_stale, retry_failed, run

pytestmark = pytest.mark.django_db


@pytest.fixture
def handler(monkeypatch):
    """Route every job kind to one controllable function."""
    calls: list[Job] = []
    behaviour: dict[str, object] = {"raise": None}

    def fake(job):
        calls.append(job)
        if behaviour["raise"] is not None:
            raise behaviour["raise"]  # type: ignore[misc]

    monkeypatch.setattr(services, "import_string", lambda path: fake)
    return calls, behaviour


def test_unknown_kind_is_refused():
    with pytest.raises(ValueError):
        enqueue("nonsense")
    with pytest.raises(ValueError):
        enqueue_many("nonsense", [{}])


def test_claim_order_is_priority_then_age():
    late_thumb = enqueue("thumb_large")
    index = enqueue("index_file", {"path": "a"})
    walk = enqueue("scan_walk", {"scan": 1})
    order = [claim("w")[0].pk for _ in range(3)]
    assert order == [walk.pk, index.pk, late_thumb.pk]
    assert claim("w") == []


def test_claim_respects_run_after():
    enqueue("thumb_small", delay=60)
    assert claim("w") == []


def test_claimed_job_is_marked():
    enqueue("thumb_small")
    job = claim("worker-1")[0]
    stored = Job.objects.get(pk=job.pk)
    assert (stored.state, stored.locked_by, stored.attempts) == (JobState.RUNNING, "worker-1", 1)


def test_success_deletes(handler):
    enqueue("thumb_small")
    assert run(claim("w")[0]) == "done"
    assert not Job.objects.exists()


def test_failure_backs_off_then_gives_up(handler):
    calls, behaviour = handler
    behaviour["raise"] = OSError("disk said no")
    enqueue("thumb_small")

    outcomes = []
    for _ in range(3):
        Job.objects.update(run_after=timezone.now())
        outcomes.append(run(claim("w")[0]))

    job = Job.objects.get()
    assert outcomes == ["requeued", "requeued", "failed"]
    assert job.state == JobState.FAILED
    assert "disk said no" in job.error
    assert len(calls) == 3


def test_backoff_grows():
    assert services._backoff(1) == timedelta(seconds=30)
    assert services._backoff(2) == timedelta(minutes=2)


def test_retry_does_not_use_an_attempt(handler):
    _, behaviour = handler
    behaviour["raise"] = Retry(5)
    enqueue("scan_reconcile", {"scan": 1})
    assert run(claim("w")[0]) == "retry"
    job = Job.objects.get()
    assert (job.state, job.attempts) == (JobState.QUEUED, 0)
    assert job.run_after > timezone.now() + timedelta(seconds=3)


def test_stale_running_jobs_are_recovered():
    enqueue("thumb_small")
    claim("dead-worker")
    assert recover_stale() == 0
    assert recover_stale(now=timezone.now() + timedelta(hours=1)) == 1
    assert Job.objects.get().state == JobState.QUEUED


def test_a_dead_workers_jobs_are_recovered_within_a_minute():
    # The deploy case: the old container is removed mid-job, and its processes
    # stop beating. Nobody should wait 30 minutes for that.
    enqueue("index_file", {"path": "a"})
    claim("old-container:34")
    beat("old-container:34", timezone.now())
    later = timezone.now() + services.BEAT_STALE + timedelta(seconds=5)
    assert recover_stale(now=later) == 1
    job = Job.objects.get()
    assert (job.state, job.locked_by, job.locked_at) == (JobState.QUEUED, "", None)


def test_a_live_workers_long_job_is_left_alone():
    enqueue("thumb_large")
    claim("busy:1")
    later = timezone.now() + timedelta(minutes=10)
    beat("busy:1", timezone.now(), now=later)  # still beating, from its heartbeat thread
    assert recover_stale(now=later) == 0
    assert Job.objects.get().state == JobState.RUNNING


def test_a_freshly_claimed_job_gets_grace_before_its_worker_has_beaten():
    enqueue("thumb_small")
    claim("just-started:1")
    assert recover_stale(now=timezone.now() + timedelta(seconds=5)) == 0


def test_retry_failed_gives_fresh_attempts():
    job = enqueue("thumb_small")
    Job.objects.filter(pk=job.pk).update(state=JobState.FAILED, attempts=3, error="x")
    assert retry_failed() == 1
    job.refresh_from_db()
    assert (job.state, job.attempts, job.error) == (JobState.QUEUED, 0, "")


def test_enqueue_many_batches():
    assert enqueue_many("index_file", ({"path": str(i)} for i in range(25)), batch=10) == 25
    assert Job.objects.filter(kind="index_file", priority=1).count() == 25
