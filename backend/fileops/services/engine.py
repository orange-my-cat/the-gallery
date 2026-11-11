"""Carrying out operations as reversible steps (FOP-03, NFR-03).

A step is a dict with a type `t`. Its apply function does the work and returns
the step as done - enriched with whatever undoing it needs (the path a file
actually landed on, the values it replaced). Its revert function takes that
returned dict and puts things back.

`run` applies steps one at a time and saves each to `Operation.steps` as soon
as it is done. If one fails, the steps already done are reverted newest first
and the operation ends `failed`: all or nothing. `undo` reverts an applied
operation the same way, removing each step from the list as it is reverted,
so an undo that stops part-way can simply be run again.
"""

from __future__ import annotations

import logging
import traceback
from collections.abc import Callable, Iterable
from typing import Any

from django.db import transaction
from django.utils import timezone

from core.services.exceptions import BusinessRuleError, ConflictError
from fileops.models import Operation, OpState

logger = logging.getLogger("gallery")

Step = dict[str, Any]
_APPLY: dict[str, Callable[[Operation, Step], Step]] = {}
_REVERT: dict[str, Callable[[Operation, Step], None]] = {}
# Steps that touch files: an operation with any of these runs in the worker.
FILE_STEPS: set[str] = set()


def step(name: str, *, touches_files: bool):
    """Register `apply` for a step type; pair it with `@reverter(name)`."""

    def register(fn: Callable[[Operation, Step], Step]):
        _APPLY[name] = fn
        if touches_files:
            FILE_STEPS.add(name)
        return fn

    return register


def reverter(name: str):
    def register(fn: Callable[[Operation, Step], None]):
        _REVERT[name] = fn
        return fn

    return register


class CannotUndo(ConflictError):
    code = "cannot_undo"


def _describe(exc: BaseException) -> str:
    if isinstance(exc, BusinessRuleError):
        return exc.message
    return "".join(traceback.format_exception_only(type(exc), exc)).strip()


def _save_steps(op: Operation) -> None:
    Operation.objects.filter(pk=op.pk).update(steps=op.steps)


def _finish(op: Operation, state: str, error: str = "") -> None:
    op.state = state
    op.error = error
    op.finished_at = timezone.now()
    Operation.objects.filter(pk=op.pk).update(state=state, error=error, finished_at=op.finished_at, steps=op.steps)


def apply_steps(op: Operation, plan: Iterable[Step]) -> None:
    """Apply and record each step, stopping at the first that fails.

    A step's database changes are atomic with it. A step that also touches a
    file puts the file back itself if its database change fails, so a step is
    either wholly done (and recorded) or not done at all.
    """
    for planned in plan:
        with transaction.atomic():
            done = _APPLY[planned["t"]](op, planned)
        op.steps.append(done)
        _save_steps(op)


def revert_steps(op: Operation) -> list[str]:
    """Revert recorded steps newest first, dropping each once reverted.

    Returns the problems met; a step that cannot be reverted is kept, so the
    operation still says what remains.
    """
    problems: list[str] = []
    while op.steps:
        last = op.steps[-1]
        try:
            with transaction.atomic():
                _REVERT[last["t"]](op, last)
        except Exception as exc:  # noqa: BLE001 - recorded, and the step stays listed
            logger.exception("operation %s: reverting %s failed", op.pk, last["t"])
            problems.append(_describe(exc))
            break
        op.steps.pop()
        _save_steps(op)
    return problems


def run(op: Operation, plan: Iterable[Step], *, finish: bool = True, reversible: bool = True) -> Operation:
    """All of `plan`, or none of it. With finish=False the operation stays
    pending - its file steps are still to come, in the worker.

    `reversible=False` is for purges, whose steps cannot be taken back: a
    failure stops there, keeps what was done, and says so.
    """
    try:
        apply_steps(op, plan)
    except Exception as exc:  # noqa: BLE001 - every failure is reversed and reported
        reason = _describe(exc)
        logger.warning("operation %s (%s) failed: %s", op.pk, op.kind, reason)
        if not reversible:
            done = len(op.steps)
            Operation.objects.filter(pk=op.pk).update(undoable=False)
            op.undoable = False
            _finish(op, OpState.FAILED, f"{reason} Stopped after {done} item(s); those are gone.")
            return op
        problems = revert_steps(op)
        detail = reason if not problems else f"{reason} Reversing it also failed: {'; '.join(problems)}"
        _finish(op, OpState.FAILED, detail)
        return op
    if finish:
        _finish(op, OpState.APPLIED)
    return op


def needs_worker(steps: Iterable[Step]) -> bool:
    return any(s["t"] in FILE_STEPS for s in steps)


def undo(op: Operation) -> Operation:
    """Revert an applied operation. Synchronous here; the caller decides where."""
    if op.state != OpState.APPLIED and op.state != OpState.UNDOING:
        raise CannotUndo(f"This change is {op.state}, so there is nothing to undo.")
    if not op.undoable:
        raise CannotUndo("This can no longer be undone: the trash it relies on has been emptied.")
    Operation.objects.filter(pk=op.pk).update(state=OpState.UNDOING)
    problems = revert_steps(op)
    if problems:
        # Partly undone: say so, and leave it undoable so it can be retried.
        Operation.objects.filter(pk=op.pk).update(state=OpState.APPLIED, error="Undo stopped: " + "; ".join(problems))
        op.state = OpState.APPLIED
        op.error = "Undo stopped: " + "; ".join(problems)
        return op
    op.state = OpState.UNDONE
    op.undone_at = timezone.now()
    Operation.objects.filter(pk=op.pk).update(state=OpState.UNDONE, undone_at=op.undone_at, error="", steps=[])
    return op


def recover(op: Operation) -> Operation:
    """An operation found `pending` or `undoing` with no worker on it: the
    process died part-way. Reverse whatever it got done (NFR-03)."""
    if op.state == OpState.UNDOING:
        return undo(op)
    problems = revert_steps(op)
    reason = "Interrupted before it finished, so everything it had done was reversed."
    _finish(op, OpState.FAILED, reason if not problems else f"{reason} Reversing failed: {'; '.join(problems)}")
    return op
