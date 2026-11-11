"""The loop and the command, against real transactions.

`transaction=True` because the loop calls `close_old_connections()`, which
inside pytest's usual wrapping transaction would close the connection mid-test.
"""

from __future__ import annotations

import time
from datetime import timedelta

import pytest
from django.core.management import call_command
from django.utils import timezone

from jobs import services
from jobs.models import Job, JobState, WorkerBeat

pytestmark = pytest.mark.django_db(transaction=True)


def test_work_once_runs_until_nothing_is_runnable(monkeypatch):
    done = []
    monkeypatch.setattr(services, "import_string", lambda path: lambda job: done.append(job.kind))
    services.enqueue("thumb_small")
    services.enqueue("thumb_large")
    assert services.work(once=True, schedule=False) == 2
    assert done == ["thumb_small", "thumb_large"]
    assert not Job.objects.exists()


def test_work_runs_every_scheduler(monkeypatch):
    ticks = []
    monkeypatch.setattr(services, "import_string", lambda path: lambda *a: ticks.append(path))
    services.work(once=True, schedule=True)
    assert ticks == list(services.SCHEDULERS)


def test_a_failing_scheduler_does_not_stop_the_worker(monkeypatch):
    def boom():
        raise RuntimeError("no")

    monkeypatch.setattr(services, "import_string", lambda path: boom)
    assert services.work(once=True, schedule=True) == 0


def test_the_heartbeat_keeps_going_through_a_long_job(monkeypatch):
    monkeypatch.setattr(services, "BEAT_EVERY", timedelta(milliseconds=50))
    seen = []

    def long_job(job):
        first = WorkerBeat.objects.get().beat_at
        time.sleep(0.4)
        seen.append(WorkerBeat.objects.get().beat_at > first)

    monkeypatch.setattr(services, "import_string", lambda path: long_job)
    services.enqueue("thumb_large")
    assert services.work(once=True, schedule=False) == 1
    assert seen == [True]


def test_the_loop_recovers_an_orphan_left_by_a_dead_process(monkeypatch):
    done = []
    monkeypatch.setattr(services, "import_string", lambda path: lambda job: done.append(job.pk))
    job = services.enqueue("index_file", {"path": "a"})
    long_ago = timezone.now() - timedelta(minutes=5)
    Job.objects.filter(pk=job.pk).update(state=JobState.RUNNING, locked_at=long_ago, locked_by="gone:1")
    assert services.work(once=True, schedule=False) == 1
    assert done == [job.pk]


def test_stop_flag():
    services.enqueue("thumb_small")
    assert services.work(stop=lambda: True, schedule=False) == 0


def test_command_once(monkeypatch, capsys):
    monkeypatch.setattr(services, "import_string", lambda path: lambda job: None)
    services.enqueue("thumb_small")
    call_command("worker", "--once")
    assert "Ran 1 job(s)." in capsys.readouterr().out
