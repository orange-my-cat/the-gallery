"""Matching faces to the people the owner has confirmed (PPL-02).

A face joins a confirmed person when it is close to *several* of that person's
faces, not to their average - so someone whose looks changed over the years
(a child growing up, glasses or none) is still recognised from whichever of
their faces the new one resembles. And it never joins someone if it looks
more like the faces the owner took away from that person than the ones kept.

Faces the owner marked as not a face, or in groups marked as not a person,
teach the other way: a face that closely resembles several of them - and
resembles them more than anyone confirmed - is set aside by itself.

What counts as a person's faces ("anchors") is what the owner has placed or
confirmed: faces put there by hand, and the faces in a group when the owner
named or changed it. Faces the matcher added itself are not anchors, so one
lucky match cannot pull in the next and drift the person away. Those matched
faces are decided again on every run: a later removal can release them.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from django.db.models import Q

from people.models import AssignedBy, Face, IgnoredBecause

# How alike a face must be to a person's faces to join them by itself. SFace's
# own same-person cut-off is 0.363, but checked against the owner's library
# (2026-11-14: the first production run, 7,895 faces joined) matches between
# 0.40 and 0.50 mixed in other people while those above 0.50 were nearly all
# right. Faces below the bar are still offered under "Find more faces".
MATCH = 0.50
# How many of a person's faces a newcomer must resemble, once they have
# enough of them to ask: "several", not one lucky look-alike.
NEED = 2
_CHUNK = 1000
# Set a face aside by itself when it resembles faces the owner marked as not a
# face or not a person this closely: high, since a person's face set aside by
# mistake is worse than a statue left in.
NOT_PERSON = 0.60
# Recorded with each run: a run under another rule is out of date at once.
RULE = f"join>={MATCH};need={NEED};not_person>={NOT_PERSON}"


def live_faces():
    from library.models import FileState

    return Face.objects.filter(ignored=False, file__state=FileState.PRESENT)


@dataclass
class Reference:
    """Every confirmed person's anchors and counter-examples, as matrices."""

    people: list[int]
    anchors: np.ndarray  # all anchors, stacked
    spans: list[tuple[int, int]]  # each person's rows in `anchors`
    removed: np.ndarray  # the faces taken away from someone, stacked
    removed_spans: list[tuple[int, int]]

    @property
    def empty(self) -> bool:
        return not self.people


def reference() -> Reference:
    anchors: dict[int, list[np.ndarray]] = {}
    for face in (
        live_faces()
        .filter(person__confirmed=True)
        .exclude(assigned_by=AssignedBy.MATCH)
        .only("person_id", "embedding")
    ):
        anchors.setdefault(face.person_id, []).append(face.vector())
    removed: dict[int, list[np.ndarray]] = {}
    for face in Face.objects.exclude(rejected=[]).only("rejected", "embedding"):
        for pk in face.rejected or []:
            if pk in anchors:
                removed.setdefault(pk, []).append(face.vector())

    people = sorted(anchors)
    rows, spans, start = [], [], 0
    for pk in people:
        rows += anchors[pk]
        spans.append((start, start + len(anchors[pk])))
        start += len(anchors[pk])
    neg_rows, neg_spans, start = [], [], 0
    for pk in people:
        got = removed.get(pk, [])
        neg_rows += got
        neg_spans.append((start, start + len(got)))
        start += len(got)
    return Reference(
        people=people,
        anchors=np.stack(rows) if rows else np.zeros((0, 128), np.float32),
        spans=spans,
        removed=np.stack(neg_rows) if neg_rows else np.zeros((0, 128), np.float32),
        removed_spans=neg_spans,
    )


def _top_mean(sims: np.ndarray, k: int) -> np.ndarray:
    """Per row, the mean of its k highest values (k >= 1, k <= columns)."""
    if sims.shape[1] == k:
        return sims.mean(axis=1)
    return -np.partition(-sims, k - 1, axis=1)[:, :k].mean(axis=1)


def scores(vectors: np.ndarray, ref: Reference) -> tuple[np.ndarray, np.ndarray]:
    """(score, eligible) per face and person.

    The score is how much the face resembles the person's faces it is closest
    to; it is eligible when enough of those faces clear `MATCH`, and it does
    not look at least as much like the faces taken away from them.
    """
    n, m = len(vectors), len(ref.people)
    score = np.full((n, m), -1.0, np.float32)
    ok = np.zeros((n, m), bool)
    if not n or not m:
        return score, ok
    sims = vectors @ ref.anchors.T
    neg = vectors @ ref.removed.T if len(ref.removed) else None
    for j, (a, b) in enumerate(ref.spans):
        own = sims[:, a:b]
        k = 1 if (b - a) < 3 else NEED
        score[:, j] = _top_mean(own, k)
        ok[:, j] = (own >= MATCH).sum(axis=1) >= k
        na, nb = ref.removed_spans[j]
        if neg is not None and nb > na:
            against = _top_mean(neg[:, na:nb], min(k, nb - na))
            ok[:, j] &= score[:, j] > against
    return score, ok


def best_person(score: np.ndarray, ok: np.ndarray, people: list[int], refused: list[list[int]]) -> list[int | None]:
    """Per face, the eligible person it resembles most (None if nobody)."""
    out: list[int | None] = []
    for i in range(len(score)):
        pick = next((people[j] for j in np.argsort(-score[i]) if ok[i, j] and people[j] not in refused[i]), None)
        out.append(pick)
    return out


def not_person_examples() -> np.ndarray:
    """Faces the owner said are not a face, or in a group that is not a person."""
    rows = [
        f.vector()
        for f in Face.objects.filter(
            ignored=True, ignored_because__in=[IgnoredBecause.NOT_FACE, IgnoredBecause.NOT_PERSON]
        ).only("embedding")
    ]
    return np.stack(rows) if rows else np.zeros((0, 128), np.float32)


def looks_not_person(vectors: np.ndarray, examples: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """(score, ok) per face: it closely resembles several faces that are not
    people (the same statue again, the same poster)."""
    if not len(vectors) or not len(examples):
        return np.full(len(vectors), -1.0, np.float32), np.zeros(len(vectors), bool)
    sims = vectors @ examples.T
    k = 1 if len(examples) < 3 else NEED
    return _top_mean(sims, k), (sims >= NOT_PERSON).sum(axis=1) >= k


def match() -> dict[str, int]:
    """Decide every face nobody holds, every face in a group the app found,
    every face the matcher placed before, and every face it set aside before.
    Returns counts for the log."""
    ref = reference()
    examples = not_person_examples()
    candidates = list(
        live_faces()
        .filter(Q(person__isnull=True) | Q(person__confirmed=False) | Q(assigned_by=AssignedBy.MATCH))
        .only("pk", "embedding", "person_id", "rejected", "assigned_by", "ignored", "ignored_because")
        .order_by("pk")
    ) + list(
        Face.objects.filter(ignored=True, ignored_because=IgnoredBecause.LOOKS_NOT_PERSON)
        .only("pk", "embedding", "person_id", "rejected", "assigned_by", "ignored", "ignored_because")
        .order_by("pk")
    )
    confirmed = set(ref.people)
    joined: dict[int, list[int]] = {}
    released: list[int] = []
    aside: list[int] = []
    restored: list[int] = []
    moved = 0
    for start in range(0, len(candidates), _CHUNK):
        chunk = candidates[start : start + _CHUNK]
        vectors = np.stack([f.vector() for f in chunk])
        score, ok = scores(vectors, ref)
        picks = best_person(score, ok, ref.people, [list(f.rejected or []) for f in chunk])
        np_score, np_ok = looks_not_person(vectors, examples)
        for i, (face, pick) in enumerate(zip(chunk, picks, strict=True)):
            was_aside = face.ignored
            pick_score = score[i, ref.people.index(pick)] if pick is not None else -1.0
            if np_ok[i] and np_score[i] > pick_score:
                if not was_aside:
                    aside.append(face.pk)
                continue
            if was_aside:
                restored.append(face.pk)
            was_match = face.assigned_by == AssignedBy.MATCH and face.person_id in confirmed
            if pick is not None and (pick != face.person_id or was_aside):
                joined.setdefault(pick, []).append(face.pk)
                moved += was_match
            elif pick is None and was_match:
                released.append(face.pk)
    if aside:
        Face.objects.filter(pk__in=aside).update(
            person_id=None, assigned_by="", ignored=True, ignored_because=IgnoredBecause.LOOKS_NOT_PERSON
        )
    if restored:
        Face.objects.filter(pk__in=restored).update(ignored=False, ignored_because="")
    for person_pk, faces in joined.items():
        Face.objects.filter(pk__in=faces).update(person_id=person_pk, assigned_by=AssignedBy.MATCH)
    if released:
        Face.objects.filter(pk__in=released).update(person_id=None, assigned_by="")
    return {
        "joined": sum(len(v) for v in joined.values()) - moved,
        "moved": moved,
        "released": len(released),
        "set_aside": len(aside),
        "restored": len(restored),
        "rule": RULE,
    }


def likely(person_pk: int, vectors: np.ndarray) -> np.ndarray:
    """For suggestions: how much each face resembles this person (its closest
    few faces), or -1 where it looks more like a face taken away from them."""
    ref = reference()
    if person_pk not in ref.people:
        # Not confirmed yet: everything in the group is an anchor.
        own = np.stack([f.vector() for f in live_faces().filter(person_id=person_pk)]) if live_faces().filter(
            person_id=person_pk
        ).exists() else np.zeros((0, 128), np.float32)
        if not len(own) or not len(vectors):
            return np.full(len(vectors), -1.0)
        return _top_mean(vectors @ own.T, min(NEED, len(own)))
    j = ref.people.index(person_pk)
    a, b = ref.spans[j]
    k = min(NEED, b - a)
    score = _top_mean(vectors @ ref.anchors[a:b].T, k)
    na, nb = ref.removed_spans[j]
    if nb > na:
        against = _top_mean(vectors @ ref.removed[na:nb].T, min(k, nb - na))
        score = np.where(score > against, score, -1.0)
    return score
