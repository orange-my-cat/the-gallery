"""The post-deployment smoke test - one command.

    python manage.py smoke_test

`scripts/deploy.ps1` runs it once the container reports healthy.

    1. the application responds
    2. the database is reachable
    3. it is the *right* database
    4. migrations are current
    5. the last dump is recent
    6. the worker is alive (production)

Assertion 3 is the tripwire under the 5432/5433 hazard. `config.settings.dev`
already refuses to start against the production port, but that guard reads
configuration. This one asks the server it actually connected to what its name
is.

Later checks belong here as the HLD's pieces land: the library mount is
readable, the cache volume is writable, the worker's queue is moving.
"""

from __future__ import annotations

import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import connection
from django.db.migrations.executor import MigrationExecutor

PASS, FAIL, SKIP = "PASS", "FAIL", "SKIP"


@dataclass
class Check:
    name: str
    status: str
    detail: str


def _allowed_host() -> str | None:
    """The first host name the application will admit, or None if it admits any."""
    for host in settings.ALLOWED_HOSTS:
        if host in ("*", ".localhost"):
            return None
        return host.lstrip(".")
    return None


class Command(BaseCommand):
    help = "Assert that this deployment is answering, connected, migrated and backed up."

    def add_arguments(self, parser) -> None:
        parser.add_argument(
            "--url",
            default="http://127.0.0.1:8000/api/health/",
            help="Health endpoint to poll. Default is the container's own port.",
        )
        parser.add_argument(
            "--skip-http",
            action="store_true",
            help="Skip the HTTP check, for running against a stopped application.",
        )

    def handle(self, *args, **options) -> None:
        checks: list[Check] = [
            self._check_http(options["url"], skip=options["skip_http"]),
            self._check_database(),
            self._check_database_identity(),
            self._check_migrations(),
            self._check_backup_age(),
            self._check_worker(),
        ]

        width = max(len(check.name) for check in checks)
        for check in checks:
            style = {
                PASS: self.style.SUCCESS,
                FAIL: self.style.ERROR,
                SKIP: self.style.WARNING,
            }[check.status]
            self.stdout.write(
                f"{style(check.status.ljust(4))}  {check.name.ljust(width)}  {check.detail}"
            )

        failures = [check for check in checks if check.status == FAIL]
        if failures:
            raise CommandError(
                f"{len(failures)} of {len(checks)} checks failed: "
                + ", ".join(check.name for check in failures)
            )
        self.stdout.write(self.style.SUCCESS("\nAll checks passed."))

    # -- 6 -----------------------------------------------------------------
    def _check_worker(self) -> Check:
        """gallery-worker is beating. Production only: in development the
        worker runs only when someone has started it."""
        name = "worker alive"
        if not getattr(settings, "SMOKE_EXPECT_WORKER", False):
            return Check(name, SKIP, "not expected here (development)")
        from jobs.services import worker_health

        health = worker_health()
        if health["online"]:
            return Check(name, PASS, f"{health['processes']} process(es) beating")
        last = health["lastSeenAt"] or "never"
        return Check(name, FAIL, f"no heartbeat in the last minute (last seen: {last})")

    # -- 1 -----------------------------------------------------------------
    def _check_http(self, url: str, *, skip: bool) -> Check:
        name = "application responds"
        if skip:
            return Check(name, SKIP, "--skip-http")
        # Connect to the loopback address, but present the name the application
        # answers to. Production ALLOWED_HOSTS is exactly `gallery.localhost`,
        # so a request carrying a `127.0.0.1` Host header is refused with a 400
        # before it reaches a view.
        request = urllib.request.Request(url)  # noqa: S310
        host = _allowed_host()
        if host:
            request.add_header("Host", host)
        try:
            with urllib.request.urlopen(request, timeout=10) as response:  # noqa: S310
                if response.status == 200:
                    return Check(name, PASS, f"200 from {url}")
                return Check(name, FAIL, f"{response.status} from {url}")
        except (urllib.error.URLError, OSError) as exc:
            return Check(name, FAIL, f"{url} unreachable: {exc}")

    # -- 2 -----------------------------------------------------------------
    def _check_database(self) -> Check:
        name = "database reachable"
        try:
            connection.ensure_connection()
        except Exception as exc:  # noqa: BLE001 - the message is the whole point
            return Check(name, FAIL, str(exc).strip().splitlines()[0])
        settings_dict = connection.settings_dict
        return Check(name, PASS, f"{settings_dict['HOST']}:{settings_dict['PORT']}")

    # -- 3 -----------------------------------------------------------------
    def _check_database_identity(self) -> Check:
        """Ask the server which database this actually is."""
        name = "database is the expected one"
        expected = getattr(settings, "EXPECTED_DATABASE_NAMES", None)
        if not expected:
            return Check(name, FAIL, "EXPECTED_DATABASE_NAMES is not defined by these settings")
        try:
            with connection.cursor() as cursor:
                cursor.execute("SELECT current_database()")
                actual = cursor.fetchone()[0]
        except Exception as exc:  # noqa: BLE001
            return Check(name, FAIL, str(exc).strip().splitlines()[0])

        if actual not in expected:
            return Check(
                name,
                FAIL,
                f"connected to '{actual}', expected one of {', '.join(sorted(expected))}",
            )
        return Check(name, PASS, f"'{actual}'")

    # -- 4 -----------------------------------------------------------------
    def _check_migrations(self) -> Check:
        name = "migrations current"
        try:
            executor = MigrationExecutor(connection)
            plan = executor.migration_plan(executor.loader.graph.leaf_nodes())
        except Exception as exc:  # noqa: BLE001
            return Check(name, FAIL, str(exc).strip().splitlines()[0])
        if plan:
            pending = ", ".join(f"{migration.app_label}.{migration.name}" for migration, _ in plan)
            return Check(name, FAIL, f"{len(plan)} unapplied: {pending}")
        return Check(name, PASS, "nothing unapplied")

    # -- 5 -----------------------------------------------------------------
    def _check_backup_age(self) -> Check:
        name = "recent backup"
        backup_dir = (settings.BACKUP_DIR or "").strip()
        if not backup_dir:
            # Development takes no dumps. Saying so is the honest answer;
            # passing would make the check meaningless.
            return Check(name, SKIP, "BACKUP_DIR is not configured (development)")

        directory = Path(backup_dir)
        if not directory.is_dir():
            return Check(name, FAIL, f"{directory} does not exist")

        dumps = sorted(directory.glob("*.dump"), key=lambda p: p.stat().st_mtime, reverse=True)
        if not dumps:
            return Check(name, FAIL, f"no dump files in {directory}")

        newest = dumps[0]
        age = datetime.now(UTC) - datetime.fromtimestamp(newest.stat().st_mtime, tz=UTC)
        limit = timedelta(hours=settings.BACKUP_MAX_AGE_HOURS)
        size_mb = newest.stat().st_size / (1024 * 1024)
        detail = f"{newest.name}, {size_mb:.1f} MB, {age.total_seconds() / 3600:.1f}h old"

        if age > limit:
            return Check(name, FAIL, f"{detail} - older than {settings.BACKUP_MAX_AGE_HOURS}h")
        return Check(name, PASS, f"{detail} ({len(dumps)} retained)")
