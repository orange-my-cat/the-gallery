"""The operation log (FOP-03, NFR-03): every change, and how to take it back.

An Operation is one thing the owner did - "trash 12 items", "rate 3 photos
four stars". It is carried out as a list of steps, and each step is written to
`steps` the moment it is done, with what is needed to reverse it. So:

- a failure part-way reverses exactly the steps already done, newest first;
- a crash part-way is found on restart (`pending` with steps) and reversed
  the same way, so nothing is left half-moved;
- undo is that same reversal, applied to an `applied` operation.

Changes that touch only the database (favourites, albums) are applied inside
the request. Anything that touches a file is carried out by the worker - the
only process that ever writes to the library.
"""

from __future__ import annotations

from django.db import models


class OpKind(models.TextChoices):
    EDIT = "edit"  # asset fields, tags; written back to the files where they belong
    FAVOURITE = "favourite"
    ALBUM = "album"
    TRASH = "trash"
    RESTORE = "restore"
    PURGE = "purge"
    MOVE = "move"
    RENAME = "rename"


class OpState(models.TextChoices):
    PENDING = "pending"  # queued or running
    APPLIED = "applied"
    UNDOING = "undoing"
    UNDONE = "undone"
    FAILED = "failed"  # reversed after an error; nothing of it remains


class Operation(models.Model):
    kind = models.CharField(max_length=16, choices=OpKind.choices)
    state = models.CharField(max_length=16, choices=OpState.choices, default=OpState.PENDING)
    # What was asked for, validated; the worker plans its steps from it.
    request = models.JSONField(default=dict)
    # Steps done, in order, each carrying what reverses it.
    steps = models.JSONField(default=list)
    # "Moved 12 items to Trash" - for the activity list and the undo toast.
    summary = models.CharField(max_length=300)
    error = models.TextField(blank=True, default="")
    # False once a step can no longer be reversed (a purge, or trash emptied).
    undoable = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    undone_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        indexes = [models.Index(fields=["-created_at"], name="operation_recent_idx")]

    def __str__(self) -> str:
        return f"{self.kind} #{self.pk} ({self.state})"
