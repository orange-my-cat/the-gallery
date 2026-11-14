"""The people steps operations are made of (see fileops/services/engine.py).

Database only. Each keeps what it replaced, so undo puts back exactly the
faces and people as they were. Registered on app load.
"""

from __future__ import annotations

from fileops.models import Operation
from fileops.services.engine import Step, reverter, step
from people.models import AssignedBy, Face, Person


def _created(op: Operation) -> int:
    """The person an earlier `person_create` step of this operation made."""
    for done in reversed(op.steps):
        if done["t"] == "person_create":
            return done["person"]
    raise LookupError("no person was created in this operation")


@step("faces_set", touches_files=False)
def apply_faces_set(op: Operation, s: Step) -> Step:
    """Move faces to a person (or to nobody), and/or set them aside.

    `person`: an id, None, or "new" (the person this operation created).
    `reject_current`: the owner took them away from their person - never
    suggest them for that person again. Putting a face with a person by hand
    lifts any such refusal.
    """
    target = _created(op) if s.get("person") == "new" else s.get("person")
    before: dict[str, dict] = {}
    for face in Face.objects.select_for_update().filter(pk__in=s["faces"]):
        before[str(face.pk)] = {
            "person": face.person_id, "assigned_by": face.assigned_by,
            "ignored": face.ignored, "ignored_because": face.ignored_because, "rejected": list(face.rejected or []),
        }
        rejected = list(face.rejected or [])
        if s.get("reject_current") and face.person_id and face.person_id not in rejected:
            rejected.append(face.person_id)
        # "Not them": a counter-example for one person, and out of their group
        # if the app had put it there.
        refuse = s.get("reject")
        if refuse is not None:
            if refuse not in rejected:
                rejected.append(refuse)
            if "person" not in s and face.person_id == refuse:
                face.person_id = None
                face.assigned_by = ""
        if "person" in s:
            if target is not None:
                rejected = [pk for pk in rejected if pk != target]
            face.person_id = target
            face.assigned_by = AssignedBy.OWNER if target is not None else ""
        if "ignored" in s:
            face.ignored = s["ignored"]
            face.ignored_because = s.get("because", "") if s["ignored"] else ""
        face.rejected = rejected
        face.save(update_fields=["person", "assigned_by", "ignored", "ignored_because", "rejected"])
    return {**s, "target": target, "before": before}


@reverter("faces_set")
def revert_faces_set(op: Operation, s: Step) -> None:
    for pk, was in s["before"].items():
        Face.objects.filter(pk=int(pk)).update(
            person_id=was["person"] if Person.objects.filter(pk=was["person"]).exists() else None,
            assigned_by=was["assigned_by"], ignored=was["ignored"],
            ignored_because=was.get("ignored_because", ""), rejected=was["rejected"],
        )


@step("person_create", touches_files=False)
def apply_person_create(op: Operation, s: Step) -> Step:
    person = Person.objects.create(name=s.get("name", ""), confirmed=True)
    return {**s, "person": person.pk}


@reverter("person_create")
def revert_person_create(op: Operation, s: Step) -> None:
    Person.objects.filter(pk=s["person"]).delete()


_FIELDS = ("name", "confirmed", "cover_id")


@step("person_update", touches_files=False)
def apply_person_update(op: Operation, s: Step) -> Step:
    person = Person.objects.select_for_update().get(pk=s["person"])
    before = {f: getattr(person, f) for f in s["set"]}
    for field, value in s["set"].items():
        if field not in _FIELDS:
            raise ValueError(f"{field} cannot be set")
        setattr(person, field, value)
    person.save()
    return {**s, "before": before}


@reverter("person_update")
def revert_person_update(op: Operation, s: Step) -> None:
    Person.objects.filter(pk=s["person"]).update(**s["before"])


@step("person_delete", touches_files=False)
def apply_person_delete(op: Operation, s: Step) -> Step:
    person = Person.objects.select_for_update().get(pk=s["person"])
    snapshot = {"name": person.name, "confirmed": person.confirmed, "cover_id": person.cover_id}
    person.delete()
    return {**s, "snapshot": snapshot}


@reverter("person_delete")
def revert_person_delete(op: Operation, s: Step) -> None:
    # Under its old id, so the steps before it can put its faces back.
    snap = s["snapshot"]
    cover = snap["cover_id"] if Face.objects.filter(pk=snap["cover_id"]).exists() else None
    Person.objects.create(pk=s["person"], name=snap["name"], confirmed=snap["confirmed"], cover_id=cover)
