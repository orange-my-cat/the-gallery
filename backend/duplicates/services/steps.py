"""The duplicate group's own step in an operation: merged or dismissed, and
back to open when the operation is undone. Registered on app load."""

from __future__ import annotations

from datetime import datetime

from django.utils import timezone

from fileops.models import Operation
from fileops.services.engine import Step, reverter, step

from duplicates.models import DuplicateGroup


@step("dup_group", touches_files=False)
def apply_dup_group(op: Operation, s: Step) -> Step:
    group = DuplicateGroup.objects.select_for_update().filter(pk=s["group"]).first()
    if group is None:
        return {**s, "skipped": True}  # recomputed away meanwhile; nothing to mark
    before = {"state": group.state, "resolved_at": group.resolved_at.isoformat() if group.resolved_at else None}
    group.state = s["state"]
    group.resolved_at = timezone.now()
    group.operation = op
    group.save(update_fields=["state", "resolved_at", "operation", "updated_at"])
    return {**s, "before": before}


@reverter("dup_group")
def revert_dup_group(op: Operation, s: Step) -> None:
    if s.get("skipped"):
        return
    before = s["before"]
    DuplicateGroup.objects.filter(pk=s["group"]).update(
        state=before["state"],
        resolved_at=datetime.fromisoformat(before["resolved_at"]) if before["resolved_at"] else None,
    )
