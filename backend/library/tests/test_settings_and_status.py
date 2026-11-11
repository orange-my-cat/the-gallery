"""Settings (LIB-01), the scan details view (LIB-06), and the worker heartbeat."""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.contrib.auth import get_user_model
from django.core.management import CommandError, call_command
from django.test import Client
from django.utils import timezone

from core.models import AppSettings
from jobs.models import Job, JobState, WorkerBeat
from jobs.services import beat, worker_health
from library.models import ScanRun

from .conftest import drain, jpeg_bytes, write

pytestmark = pytest.mark.django_db


@pytest.fixture
def signed_in(settings) -> Client:
    client = Client()
    client.force_login(get_user_model().objects.create_user(username=settings.GALLERY_USERNAME, password="x" * 20))
    return client


def put(client: Client, body) -> tuple[int, dict]:
    response = client.put("/api/settings", body, content_type="application/json")
    return response.status_code, response.json()


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------


def test_defaults(signed_in, library):
    body = signed_in.get("/api/settings").json()
    assert body["libraryRoot"] == body["libraryPath"]
    assert body["librarySubfolder"] == ""
    assert (body["scanIntervalMinutes"], body["trashRetentionDays"], body["phashThreshold"]) == (60, 30, 4)
    assert (body["writeBackMode"], body["autoMergeExact"], body["exclusions"]) == ("embedded", False, [])


def test_saving_plain_settings_does_not_scan(signed_in, library):
    status, body = put(signed_in, {"scanIntervalMinutes": 0, "trashRetentionDays": 14, "phashThreshold": 6,
                                   "writeBackMode": "sidecar", "autoMergeExact": True})
    assert status == 200
    assert body["scanStarted"] is False
    row = AppSettings.load()
    assert (row.scan_interval_minutes, row.trash_retention_days, row.phash_threshold) == (0, 14, 6)
    assert (row.write_back_mode, row.auto_merge_exact) == ("sidecar", True)
    assert not ScanRun.objects.exists()


def test_changing_the_folder_or_exclusions_rescans(signed_in, library):
    (library / "Photos").mkdir()
    status, body = put(signed_in, {"librarySubfolder": "\\Photos\\"})
    assert status == 200
    assert body["scanStarted"] is True
    assert body["settings"]["librarySubfolder"] == "Photos"
    assert body["settings"]["libraryPath"].endswith("Photos")
    drain()

    status, body = put(signed_in, {"exclusions": ["Exports/", "exports", " ", "Old/Junk"]})
    assert body["settings"]["exclusions"] == ["Exports", "Old/Junk"], "normalised, de-duplicated, blanks dropped"
    assert body["scanStarted"] is True
    drain()

    status, body = put(signed_in, {"exclusions": ["Exports", "Old/Junk"]})
    assert body["scanStarted"] is False, "nothing changed"


@pytest.mark.parametrize(
    "body,field",
    [
        ({"librarySubfolder": "Nope"}, "librarySubfolder"),
        ({"librarySubfolder": "../outside"}, "librarySubfolder"),
        ({"exclusions": [".gallery-trash"]}, "exclusions"),
        ({"exclusions": "Exports"}, "exclusions"),
        ({"scanIntervalMinutes": 2}, "scanIntervalMinutes"),
        ({"scanIntervalMinutes": "60"}, "scanIntervalMinutes"),
        ({"trashRetentionDays": 0}, "trashRetentionDays"),
        ({"phashThreshold": 8}, "phashThreshold"),
        ({"writeBackMode": "yolo"}, "writeBackMode"),
        ({"autoMergeExact": "yes"}, "autoMergeExact"),
    ],
)
def test_refusals_name_the_field_and_change_nothing(signed_in, library, body, field):
    status, payload = put(signed_in, {"trashRetentionDays": 9, **body})
    assert status == 400
    assert field in payload["error"]["field_errors"]
    assert AppSettings.load().trash_retention_days == 30


def test_no_library_means_no_folder_choice(signed_in, settings):
    settings.LIBRARY_ROOT = None
    assert signed_in.get("/api/settings").json()["libraryPath"] is None
    status, payload = put(signed_in, {"librarySubfolder": "x"})
    assert status == 400 and "librarySubfolder" in payload["error"]["field_errors"]
    status, _ = put(signed_in, {"scanIntervalMinutes": 30})
    assert status == 200, "the rest can still be saved"


def test_settings_need_a_session(client):
    assert client.get("/api/settings").status_code == 403


# ---------------------------------------------------------------------------
# Scan details and the heartbeat
# ---------------------------------------------------------------------------


def test_heartbeat():
    assert worker_health() == {"online": False, "processes": 0, "lastSeenAt": None}
    now = timezone.now()
    beat("host:1", now, now=now)
    beat("host:2", now, now=now - timedelta(minutes=5))
    health = worker_health(now=now)
    assert (health["online"], health["processes"]) == (True, 1)
    assert worker_health(now=now + timedelta(minutes=2))["online"] is False
    beat("host:1", now, now=now + timedelta(days=2))
    assert list(WorkerBeat.objects.values_list("worker_id", flat=True)) == ["host:1"], "a day-old process forgotten"


def test_status_says_when_the_worker_is_down_and_what_is_pending(signed_in, library, tags):
    write(library / "a.jpg", jpeg_bytes())
    status = signed_in.post("/api/scan").json()
    assert status["workerOnline"] is False
    assert status["pending"] == 1  # the walk, waiting for a worker
    beat("host:1", timezone.now())
    assert signed_in.get("/api/scan/status").json()["workerOnline"] is True


def test_details_and_retry(signed_in, library, tags):
    write(library / "a.jpg", jpeg_bytes())
    signed_in.post("/api/scan")
    drain()
    Job.objects.create(kind="index_file", payload={"path": "bad.jpg"}, state=JobState.FAILED, attempts=3,
                       error="OSError: unreadable", run_after=timezone.now())
    Job.objects.create(kind="thumb_small", payload={"file": 999}, run_after=timezone.now())

    body = signed_in.get("/api/scan/details").json()
    assert body["queue"] == {"thumb_small": 1}
    assert body["failedTotal"] == 1
    assert body["failed"][0] == {"id": body["failed"][0]["id"], "kind": "index_file", "path": "bad.jpg",
                                 "error": "OSError: unreadable", "attempts": 3}
    assert body["recent"][0]["new"] == 1 and body["recent"][0]["state"] == "done"
    assert body["status"]["state"] == "errors"

    retried = signed_in.post("/api/scan/retry").json()
    assert retried["retried"] == 1
    assert Job.objects.get(kind="index_file").state == JobState.QUEUED


# ---------------------------------------------------------------------------
# The smoke test's worker check
# ---------------------------------------------------------------------------


def test_smoke_test_checks_the_worker_only_where_expected(settings, capsys):
    call_command("smoke_test", "--skip-http")
    assert "not expected here" in capsys.readouterr().out

    settings.SMOKE_EXPECT_WORKER = True
    with pytest.raises(CommandError, match="worker alive"):
        call_command("smoke_test", "--skip-http")
    beat("host:1", timezone.now())
    call_command("smoke_test", "--skip-http")
    assert "1 process(es) beating" in capsys.readouterr().out
