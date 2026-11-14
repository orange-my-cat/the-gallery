"""Grouping faces into people (PPL-02, HLD "Clustering").

A full run - after new faces are found, and at least once a day:

1. **Match** - faces join the confirmed people they resemble (see
   `matching`: several of the person's faces, never one they were taken from,
   never one that looks more like a face the owner removed). Confirmed groups
   are never reshaped otherwise.
2. **Cluster** - HDBSCAN (at least 3 faces to a group) over every face that is
   unassigned or in a provisional group. Each group found takes over the
   provisional Person it shares the most faces with, so groups keep their ids
   - and the screen its place - from one run to the next.

A quick run - matching alone - follows every change the owner makes to a
person, so naming someone gathers their other faces within seconds.
"""

from __future__ import annotations

import logging

import numpy as np
from datetime import timedelta

from django.db import connection, transaction
from django.db.models import Count, Q
from django.utils import timezone

from jobs.models import Job, JobState
from jobs.services import Retry, enqueue
from library.models import FileState
from people.models import AssignedBy, Face, MatchRun, Person
from people.services import matching


logger = logging.getLogger("gallery")

# A face the clustering calls noise stays in a group the app found if it is
# at least this alike to it (SFace's same-person cut-off, rounded up).
STAY = 0.40

# A full run at least this often, new photos or not.
FULL_EVERY = timedelta(hours=24)
# How alike, on average, every two faces of a lone group must be to count as
# one person: SFace's own same-person cut-off (strangers average about 0).
COHESION = 0.363
MIN_GROUP = 3
_LOCK = 0x7065_6f70  # "peop": one run at a time


def live_faces():
    """Faces on files in the library, not set aside by the owner."""
    return Face.objects.filter(ignored=False, file__state=FileState.PRESENT)


def _matrix(faces) -> np.ndarray:
    return np.stack([f.vector() for f in faces]) if faces else np.zeros((0, 128), np.float32)


def _labels(vectors: np.ndarray) -> np.ndarray:
    """HDBSCAN's groups (-1: no group). By default HDBSCAN never returns the
    whole pool as one group, so a pool that is just one person comes back as
    all noise: only then is that one group allowed - and only if its faces
    really are alike, so a pool of strangers never becomes one junk group."""
    from sklearn.cluster import HDBSCAN

    if len(vectors) < MIN_GROUP:
        return np.full(len(vectors), -1)
    labels = HDBSCAN(min_cluster_size=MIN_GROUP, copy=True).fit_predict(vectors)
    if (labels >= 0).any():
        return labels
    return lone_group(vectors)


def lone_group(vectors: np.ndarray) -> np.ndarray:
    """The whole pool as one group, if its faces are alike enough; else noise."""
    from sklearn.cluster import HDBSCAN

    single = HDBSCAN(min_cluster_size=MIN_GROUP, allow_single_cluster=True, copy=True).fit_predict(vectors)
    members = vectors[single >= 0]
    n = len(members)
    if n >= MIN_GROUP:
        sims = members @ members.T
        pairwise = (sims.sum() - np.trace(sims)) / (n * (n - 1))
        if float(pairwise) >= COHESION:
            return single
    return np.full(len(vectors), -1)


def recluster() -> dict[str, int]:
    matched = matching.match()
    pool = list(live_faces().filter(Q(person__isnull=True) | Q(person__confirmed=False)).order_by("pk"))
    labels = _labels(_matrix(pool))
    groups: dict[int, list[Face]] = {}
    for face, label in zip(pool, labels, strict=True):
        groups.setdefault(int(label), []).append(face)

    with transaction.atomic():
        provisional = {p.pk: p for p in Person.objects.filter(confirmed=False)}
        held: dict[int, set[int]] = {}
        for face in pool:
            if face.person_id in provisional:
                held.setdefault(face.person_id, set()).add(face.pk)
        taken: set[int] = set()
        assignment: dict[int, int | None] = {f.pk: None for f in pool}
        created = 0
        for label, faces in sorted(groups.items(), key=lambda g: -len(g[1])):
            if label < 0:
                continue
            ids = {f.pk for f in faces}
            best = max(
                (pk for pk in held if pk not in taken), key=lambda pk: len(held[pk] & ids), default=None
            )
            if best is None or not held[best] & ids:
                person = Person.objects.create()
                created += 1
                best = person.pk
            taken.add(best)
            for face in faces:
                assignment[face.pk] = None if best in (face.rejected or []) else best
        # A face the run calls noise stays in its provisional group if that
        # group lives on and the face still looks like it: groups should not
        # shed faces from one run to the next, nor keep ones that never fit.
        centre_of: dict[int, np.ndarray] = {}
        for person_pk in taken:
            members = [f.vector() for f in pool if assignment.get(f.pk) == person_pk]
            if members:
                mean = np.mean(members, axis=0)
                centre_of[person_pk] = mean / (np.linalg.norm(mean) or 1.0)
        for face in groups.get(-1, []):
            centre = centre_of.get(face.person_id)
            if centre is not None and float(face.vector() @ centre) >= STAY:
                assignment[face.pk] = face.person_id
        by_person: dict[int | None, list[int]] = {}
        for face_pk, person_pk in assignment.items():
            by_person.setdefault(person_pk, []).append(face_pk)
        for person_pk, face_pks in by_person.items():
            Face.objects.filter(pk__in=face_pks).update(
                person_id=person_pk, assigned_by=AssignedBy.CLUSTER if person_pk else ""
            )
        emptied = Person.objects.filter(confirmed=False).annotate(n=Count("faces")).filter(n=0)
        removed = emptied.count()
        emptied.delete()
    refresh_covers()
    return {**matched, "pool": len(pool), "groups": len(taken), "created": created, "removed": removed}


def refresh_covers() -> None:
    """Each person shows a face of theirs: the clearest, unless still valid."""
    for person in Person.objects.all():
        if person.cover_id and live_faces().filter(pk=person.cover_id, person=person).exists():
            continue
        best = live_faces().filter(person=person).order_by("-score").values_list("pk", flat=True).first()
        if best != person.cover_id:
            Person.objects.filter(pk=person.pk).update(cover_id=best)


# ---------------------------------------------------------------------------
# The job
# ---------------------------------------------------------------------------


def request_cluster() -> None:
    if not Job.objects.filter(kind="cluster_faces", state=JobState.QUEUED).exists():
        enqueue("cluster_faces", {})


def request_match() -> None:
    """After the owner names or changes someone: match at once, no regrouping."""
    if not Job.objects.filter(kind="match_faces", state=JobState.QUEUED).exists():
        enqueue("match_faces", {})


def _locked(work, kind: str) -> None:
    with connection.cursor() as cursor:
        cursor.execute("SELECT pg_try_advisory_lock(%s)", [_LOCK])
        (locked,) = cursor.fetchone()
    if not locked:
        raise Retry(15)  # the other kind of run is going; this one follows
    run = MatchRun.objects.create(kind=kind)
    try:
        summary = work()
        MatchRun.objects.filter(pk=run.pk).update(finished_at=timezone.now(), summary=summary)
        logger.info("people %s run: %s", kind, summary)
    finally:
        with connection.cursor() as cursor:
            cursor.execute("SELECT pg_advisory_unlock(%s)", [_LOCK])


def match_job(job: Job) -> None:
    def work():
        summary = matching.match()
        refresh_covers()
        return summary

    _locked(work, "match")


def schedule_due(now=None) -> bool:
    """Worker scheduler: a full run when a day has passed since the last one,
    or at once when the matching rule has changed since (so faces joined
    under the old rule are decided again under the new one)."""
    now = now or timezone.now()
    last = MatchRun.objects.filter(kind="full", finished_at__isnull=False).order_by("-finished_at").first()
    current = last is not None and (last.summary or {}).get("rule") == matching.RULE
    if current and last.finished_at > now - FULL_EVERY:
        return False
    if not Face.objects.exists():
        return False
    request_cluster()
    return True


def cluster_job(job: Job) -> None:
    # Faces still being found would be left out, and provisional groups would
    # shift twice; wait for them. The last search to find a face asks again.
    if Job.objects.filter(kind="faces", state__in=[JobState.QUEUED, JobState.RUNNING]).exists():
        raise Retry(30)
    _locked(recluster, "full")
