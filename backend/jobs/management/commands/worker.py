"""`manage.py worker` - run the job queue.

    python manage.py worker                 # CPU cores minus one processes
    python manage.py worker --processes 1   # one process, the friendliest for debugging
    python manage.py worker --once          # drain what is runnable now, then exit

Development runs this in a second terminal next to `runserver`. Production runs
it as `gallery-worker`, from the same image as `gallery-web`.

Each process stops after its current job on Ctrl+C or SIGTERM (`docker stop`).
SIGTERM reaches only this process - the entrypoint signals it alone, and there
is no init to signal the group - so it passes the stop on to its children.
A process killed outright leaves its job `running`; any worker still alive
queues it again within a minute of the process's heartbeat stopping.
"""

from __future__ import annotations

import multiprocessing
import os
import signal

from django.core.management.base import BaseCommand


def _install_stop_handlers(children: list | None = None) -> list[bool]:
    flag = [False]

    def handler(signum, frame):  # noqa: ARG001
        flag[0] = True
        # Passed on at once, so the children stop claiming new work while this
        # process finishes its own job. Not on Windows: Ctrl+C has already
        # reached every process in the console, and Process.terminate() there
        # is a hard kill.
        if os.name != "nt":
            for child in children or ():
                if child.pid is not None:
                    try:
                        os.kill(child.pid, signal.SIGTERM)
                    except ProcessLookupError:
                        pass

    signal.signal(signal.SIGINT, handler)
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, handler)
    return flag


def _child(schedule: bool) -> None:
    # Spawned, not forked (Windows has no fork): Django must be set up again.
    import django

    django.setup()
    from jobs.services import work

    flag = _install_stop_handlers()
    work(stop=lambda: flag[0], schedule=schedule)


class Command(BaseCommand):
    help = "Run background jobs: scans, indexing, thumbnails."

    def add_arguments(self, parser) -> None:
        parser.add_argument(
            "--processes",
            type=int,
            default=max((os.cpu_count() or 2) - 1, 1),
            help="Worker processes. Default: CPU cores minus one.",
        )
        parser.add_argument(
            "--once",
            action="store_true",
            help="Run what is runnable now in this process, then exit.",
        )

    def handle(self, *args, **options) -> None:
        from django.db import connections

        from jobs.services import work

        if options["once"]:
            ran = work(once=True, schedule=False)
            self.stdout.write(f"Ran {ran} job(s).")
            return

        processes = max(options["processes"], 1)
        self.stdout.write(f"Worker running with {processes} process(es). Ctrl+C to stop.")

        # Children open their own connections; none may inherit this one.
        connections.close_all()
        ctx = multiprocessing.get_context("spawn")
        # Only this process schedules scans; the others just work.
        children = [ctx.Process(target=_child, args=(False,), daemon=False) for _ in range(processes - 1)]
        for child in children:
            child.start()

        flag = _install_stop_handlers(children)
        try:
            work(stop=lambda: flag[0], schedule=True)
        finally:
            for child in children:
                child.join(timeout=120)
            self.stdout.write("Worker stopped.")
