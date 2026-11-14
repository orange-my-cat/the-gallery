""""Are they the same person?" - quick questions that teach the matcher.

The app asks about the faces it is least sure of: those whose resemblance to a
confirmed person lies near the line where it would join them by itself
(`matching.MATCH`), including joins it made only just. One answer teaches more
there than anywhere else (active learning):

- yes: the face becomes one of the person's own faces - an anchor;
- no: the face becomes a counter-example for the person (and leaves them if
  the app had put it there);
- not a face: set aside, and a lesson for "looks not a person".

Each answer is a logged, undoable change, and is followed by a quick
matching run, so one answer can settle other faces too.
"""

from __future__ import annotations

from typing import Any

import numpy as np
from django.db.models import Q

from people.models import AssignedBy, Face, Person
from people.services import matching
from people.services.matching import live_faces

# The band worth asking about, either side of the join line (0.50).
ASK_LOW = 0.35
ASK_HIGH = 0.62
SAMPLE = 4  # faces of the person shown beside the question


def questions(limit: int = 10, skip: set[int] | None = None) -> dict[str, Any]:
    skip = skip or set()
    ref = matching.reference()
    if ref.empty:
        return {"questions": [], "people": 0}
    candidates = [
        f
        for f in live_faces()
        .filter(Q(person__isnull=True) | Q(person__confirmed=False) | Q(assigned_by=AssignedBy.MATCH))
        .select_related("file")
        .only("pk", "embedding", "person_id", "rejected", "assigned_by", "file__asset_id")
        if f.pk not in skip
    ]
    if not candidates:
        return {"questions": [], "people": len(ref.people)}
    vectors = np.stack([f.vector() for f in candidates])
    score, _ = matching.scores(vectors, ref)
    asks: list[tuple[float, Face, int, float]] = []
    for i, face in enumerate(candidates):
        refused = set(face.rejected or [])
        order = np.argsort(-score[i])
        # The person it most resembles that it may be asked about.
        j = next((j for j in order if ref.people[j] not in refused), None)
        if j is None:
            continue
        s = float(score[i, j])
        if ASK_LOW <= s <= ASK_HIGH:
            asks.append((abs(s - matching.MATCH), face, ref.people[j], s))
    asks.sort(key=lambda t: t[0])  # nearest the line first: the most uncertain
    chosen = asks[:limit]
    people = {p.pk: p for p in Person.objects.filter(pk__in={pk for _, _, pk, _ in chosen})}
    out = []
    for _, face, person_pk, s in chosen:
        person = people[person_pk]
        sample = list(
            live_faces().filter(person=person).exclude(assigned_by=AssignedBy.MATCH).order_by("-score")[:SAMPLE]
            .values_list("pk", flat=True)
        )
        out.append(
            {
                "face": {"id": str(face.pk), "url": f"/media/face/{face.pk}", "assetId": str(face.file.asset_id)},
                "person": {
                    "id": str(person.pk),
                    "name": person.name,
                    "faces": [f"/media/face/{pk}" for pk in sample],
                },
                # The app had already put it with them, only just.
                "matched": face.person_id == person.pk and face.assigned_by == AssignedBy.MATCH,
                "similarity": round(s, 3),
            }
        )
    return {"questions": out, "people": len(ref.people), "left": len(asks)}
