"""The worker queue: a Postgres table claimed with FOR UPDATE SKIP LOCKED.

No Redis and no broker (HLD, "Key design decisions"). Successful jobs are
deleted as they finish, so the table holds only what is still to do, what is
being done, and what has given up - which is exactly what the scan status view
needs to show.
"""

from __future__ import annotations

from django.db import models


class JobState(models.TextChoices):
    QUEUED = "queued"
    RUNNING = "running"
    FAILED = "failed"


class Job(models.Model):
    kind = models.CharField(max_length=32)
    payload = models.JSONField(default=dict)
    # Lower runs first (HLD, "Jobs by priority").
    priority = models.SmallIntegerField(default=5)
    state = models.CharField(max_length=16, choices=JobState.choices, default=JobState.QUEUED)
    attempts = models.PositiveSmallIntegerField(default=0)
    run_after = models.DateTimeField()
    locked_at = models.DateTimeField(null=True, blank=True)
    locked_by = models.CharField(max_length=64, blank=True, default="")
    error = models.TextField(blank=True, default="")
    # Which scan queued it, so a scan can tell when its indexing is finished
    # and the status view can count its failures. Null for jobs no scan owns.
    scan = models.ForeignKey(
        "library.ScanRun", null=True, blank=True, on_delete=models.SET_NULL, related_name="jobs"
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [
            # The claim query's access path.
            models.Index(
                fields=["priority", "id"],
                name="job_claim_idx",
                condition=models.Q(state="queued"),
            ),
            models.Index(fields=["scan", "kind", "state"], name="job_scan_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.kind} #{self.pk} ({self.state})"


class WorkerBeat(models.Model):
    """Each worker process says it is alive every few seconds.

    Without this a stopped worker is invisible: a scan just sits at "0 of 0"
    forever. The status view reads the newest beat to say so plainly.
    """

    worker_id = models.CharField(max_length=64, primary_key=True)
    beat_at = models.DateTimeField()
    started_at = models.DateTimeField()

    def __str__(self) -> str:
        return f"{self.worker_id} @ {self.beat_at:%H:%M:%S}"
