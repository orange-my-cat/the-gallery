"""What the owner can do with people (PPL-02, PPL-04), as logged, undoable changes.

- name a person (or rename one);
- merge people into one;
- add faces to a person - from nobody, or from someone else - or start a new
  person with them;
- remove faces from their person (never suggested for that person again);
- mark detections as not a face;
- delete a person: the group goes, and its faces are set aside so the same
  group is not found again. The photos are never touched;
- mark groups as not a person (a statue, a poster): as deleting, and faces
  found later that closely resemble them are set aside by themselves.

All database-only, so each is done at once and undoable from Activity.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from core.services.exceptions import BusinessRuleError, NotFoundError
from fileops.models import Operation, OpKind
from fileops.services import engine
from people.models import Face, Person

MAX_NAME = 200
MAX_FACES = 5000


class InvalidRequest(BusinessRuleError):
    code = "validation_failed"


def _new(summary: str) -> Operation:
    return Operation.objects.create(kind=OpKind.PEOPLE, summary=summary[:300])


def _rematch(op: Operation) -> Operation:
    """Who a person is just changed: gather their other faces now."""
    if op.state == "applied":
        from people.services.cluster import request_match

        request_match()
    return op


def _person(person_id: Any) -> Person:
    try:
        person = Person.objects.filter(pk=int(person_id)).first()
    except (TypeError, ValueError):
        person = None
    if person is None:
        raise NotFoundError("That person is no longer in the list. Reload and try again.")
    return person


def _faces(ids: Iterable[Any]) -> list[int]:
    try:
        wanted = list(dict.fromkeys(int(i) for i in ids))
    except (TypeError, ValueError) as exc:
        raise InvalidRequest("Face ids are numbers.", field="faces") from exc
    if not wanted:
        raise InvalidRequest("Select at least one face.", field="faces")
    if len(wanted) > MAX_FACES:
        raise InvalidRequest(f"At most {MAX_FACES} faces at once.", field="faces")
    found = set(Face.objects.filter(pk__in=wanted).values_list("pk", flat=True))
    if len(found) != len(wanted):
        raise NotFoundError("Some of those faces are no longer there. Reload and try again.")
    return wanted


def _name(value: Any) -> str:
    if not isinstance(value, str):
        raise InvalidRequest("A name is text.", field="name")
    name = " ".join(value.split())
    if len(name) > MAX_NAME:
        raise InvalidRequest(f"At most {MAX_NAME} characters.", field="name")
    return name


def label(person: Person) -> str:
    return person.name or "an unnamed person"


def _n(count: int, what: str = "face") -> str:
    return f"{count} {what}" if count == 1 else f"{count} {what}s"


def rename(person_id: Any, name: Any) -> Operation:
    person = _person(person_id)
    name = _name(name)
    summary = f"Named {name}" if not person.name else f"Renamed {person.name} to {name or 'nobody'}"
    return _rematch(
        engine.run(_new(summary), [{"t": "person_update", "person": person.pk, "set": {"name": name, "confirmed": True}}])
    )


def merge(ids: Iterable[Any], into: Any = None) -> Operation:
    """Merge people into one: `into`, or else the named one with most faces."""
    people = [_person(pk) for pk in dict.fromkeys(ids)]
    if len(people) < 2:
        raise InvalidRequest("Choose at least two people to merge.", field="ids")
    if into is not None:
        keep = _person(into)
        if keep.pk not in {p.pk for p in people}:
            raise InvalidRequest("Merge into one of the people chosen.", field="into")
    else:
        keep = max(people, key=lambda p: (bool(p.name), p.confirmed, p.faces.count()))
    others = [p for p in people if p.pk != keep.pk]
    faces = list(Face.objects.filter(person__in=others).values_list("pk", flat=True))
    plan: list[dict] = []
    if faces:
        plan.append({"t": "faces_set", "faces": faces, "person": keep.pk})
    plan.append({"t": "person_update", "person": keep.pk, "set": {"confirmed": True}})
    plan += [{"t": "person_delete", "person": p.pk} for p in others]
    return _rematch(engine.run(_new(f"Merged {len(people)} people into {label(keep)}"), plan))


def add_faces(face_ids: Iterable[Any], person_id: Any = None, name: Any = None) -> Operation:
    """Put faces with a person, or with a new one (`name` may be empty)."""
    faces = _faces(face_ids)
    if person_id is not None:
        person = _person(person_id)
        plan = [
            {"t": "faces_set", "faces": faces, "person": person.pk},
            {"t": "person_update", "person": person.pk, "set": {"confirmed": True}},
        ]
        summary = f"Added {_n(len(faces))} to {label(person)}"
    else:
        new_name = _name(name or "")
        plan = [{"t": "person_create", "name": new_name}, {"t": "faces_set", "faces": faces, "person": "new"}]
        summary = f"Started {new_name or 'a new person'} with {_n(len(faces))}"
    return _rematch(engine.run(_new(summary), plan))


def remove_faces(face_ids: Iterable[Any]) -> Operation:
    faces = _faces(face_ids)
    holders = set(Face.objects.filter(pk__in=faces, person__isnull=False).values_list("person_id", flat=True))
    plan = [{"t": "faces_set", "faces": faces, "person": None, "reject_current": True}]
    plan += [{"t": "person_update", "person": pk, "set": {"confirmed": True}} for pk in sorted(holders)]
    return _rematch(engine.run(_new(f"Removed {_n(len(faces))} from their person"), plan))


def not_them(face_id: Any, person_id: Any) -> Operation:
    """An answer of "no" to "is this them?": never theirs, and a lesson."""
    (face,) = _faces([face_id])
    person = _person(person_id)
    return _rematch(
        engine.run(
            _new(f"Not {label(person)}: 1 face"),
            [{"t": "faces_set", "faces": [face], "reject": person.pk}],
        )
    )


def not_faces(face_ids: Iterable[Any]) -> Operation:
    faces = _faces(face_ids)
    return engine.run(
        _new(f"Marked {_n(len(faces), 'detection')} as not a face"),
        [{"t": "faces_set", "faces": faces, "person": None, "ignored": True, "because": "not_face"}],
    )


def _set_aside(ids: Iterable[Any], because: str) -> tuple[list[Person], list[int], list[dict]]:
    people = [_person(pk) for pk in dict.fromkeys(ids)]
    if not people:
        raise InvalidRequest("Choose at least one person.", field="ids")
    faces = list(Face.objects.filter(person__in=people).values_list("pk", flat=True))
    plan: list[dict] = []
    if faces:
        plan.append({"t": "faces_set", "faces": faces, "person": None, "ignored": True, "because": because})
    plan += [{"t": "person_delete", "person": p.pk} for p in people]
    return people, faces, plan


def not_people(ids: Iterable[Any]) -> Operation:
    """Groups that are not people - a statue, a poster, a face on a T-shirt.
    Their faces are set aside, and faces found later that closely resemble
    them are set aside too (see `matching`)."""
    people, faces, plan = _set_aside(ids, "not_person")
    what = "a group" if len(people) == 1 else f"{len(people)} groups"
    return _rematch(engine.run(_new(f"Marked {what} as not a person ({_n(len(faces))})"), plan))


def delete_people(ids: Iterable[Any]) -> Operation:
    """Delete people - one change, however many. Their faces are set aside so
    the same groups are not found again; the photos stay as they are."""
    people, faces, plan = _set_aside(ids, "deleted")
    what = label(people[0]) if len(people) == 1 else f"{len(people)} people"
    return engine.run(_new(f"Deleted {what} ({_n(len(faces))} set aside; the photos stay)"), plan)


def delete_person(person_id: Any) -> Operation:
    return delete_people([person_id])
