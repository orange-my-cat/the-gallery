"""The smoke test command that scripts/deploy.ps1 runs after a deploy."""

from __future__ import annotations

import io
import os
import time
from unittest import mock

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import connection
from django.test import override_settings

pytestmark = pytest.mark.django_db


def _run(**options) -> str:
    out = io.StringIO()
    options.setdefault("skip_http", True)
    call_command("smoke_test", stdout=out, stderr=out, **options)
    return out.getvalue()


def test_it_passes_against_a_healthy_test_database():
    output = _run()

    assert "FAIL" not in output
    assert "All checks passed." in output


def test_it_reports_the_database_it_actually_connected_to():
    output = _run()

    assert "database is the expected one" in output
    assert connection.settings_dict["NAME"] in output


def test_it_fails_when_connected_to_an_unexpected_database():
    with override_settings(EXPECTED_DATABASE_NAMES={"some_other_database"}):
        with pytest.raises(CommandError, match="database is the expected one"):
            _run()


def test_it_fails_when_the_settings_declare_no_expected_database():
    with override_settings(EXPECTED_DATABASE_NAMES=set()):
        with pytest.raises(CommandError):
            _run()


def test_backup_check_is_skipped_when_no_directory_is_configured():
    output = _run()

    assert "SKIP" in output
    assert "BACKUP_DIR is not configured" in output


def test_backup_check_passes_on_a_recent_dump(tmp_path):
    (tmp_path / "gallery-20261108T090000Z.dump").write_bytes(b"x" * 2048)

    with override_settings(BACKUP_DIR=str(tmp_path), BACKUP_MAX_AGE_HOURS=24):
        output = _run()

    assert "1 retained" in output
    assert "FAIL" not in output


def test_backup_check_fails_on_a_stale_dump(tmp_path):
    dump = tmp_path / "gallery-20260101T090000Z.dump"
    dump.write_bytes(b"x")
    two_days_ago = time.time() - (48 * 3600)
    os.utime(dump, (two_days_ago, two_days_ago))

    with override_settings(BACKUP_DIR=str(tmp_path), BACKUP_MAX_AGE_HOURS=24):
        with pytest.raises(CommandError, match="recent backup"):
            _run()


def test_backup_check_fails_when_the_folder_holds_no_dumps(tmp_path):
    with override_settings(BACKUP_DIR=str(tmp_path)):
        with pytest.raises(CommandError, match="recent backup"):
            _run()


def test_backup_check_fails_when_the_folder_is_missing(tmp_path):
    with override_settings(BACKUP_DIR=str(tmp_path / "not-mounted")):
        with pytest.raises(CommandError, match="recent backup"):
            _run()


def test_http_check_fails_when_nothing_is_listening():
    with pytest.raises(CommandError, match="application responds"):
        _run(skip_http=False, url="http://127.0.0.1:9/api/health/")


def test_http_check_presents_the_production_host_name():
    response = mock.MagicMock(status=200)
    response.__enter__.return_value = response

    with (
        override_settings(ALLOWED_HOSTS=["gallery.localhost"]),
        mock.patch("urllib.request.urlopen", return_value=response) as urlopen,
    ):
        output = _run(skip_http=False)

    assert "200 from" in output
    assert urlopen.call_args.args[0].get_header("Host") == "gallery.localhost"
