"""What the People screens show (PPL-02, PPL-03)."""

from __future__ import annotations

from typing import Any

import numpy as np
from django.db.models import Count, F, Q

from core.services.exceptions import NotFoundError
from library.models import FileState, MediaFile
from people.models import Face, FaceScan, Person
from people.services import matching
from people.services.cluster import live_faces
from people.services.faces import searchable

SUGGEST = 0.25  # loose: the owner picks from these, nothing joins by itself
SUGGEST_LIMIT = 60


def face_url(face_id: int) -> str:
    return f"/media/face/{face_id}"


def _face(face: Face) -> dict[str, Any]:
    return {"id": str(face.pk), "url": face_url(face.pk), "assetId": str(face.file.asset_id)}


def people() -> dict[str, Any]:
    rows = (
        Person.objects.annotate(
            n=Count("faces", filter=Q(faces__ignored=False, faces__file__state=FileState.PRESENT)),
            items=Count(
                "faces__file__asset",
                filter=Q(faces__ignored=False, faces__file__state=FileState.PRESENT),
                distinct=True,
            ),
        )
        .filter(n__gt=0)
    )
    listed = sorted(rows, key=lambda p: (not p.name, p.name.lower() if p.name else "", -p.n))
    total = sum(
        1
        for m in MediaFile.objects.filter(state=FileState.PRESENT).exclude(pixel_hash="").only("pk", "rel_path", "mime", "role", "state")
        if searchable(m)
    )
    scanned = FaceScan.objects.filter(file__state=FileState.PRESENT, pixel_hash=F("file__pixel_hash")).count()
    return {
        "people": [
            {
                "id": str(p.pk),
                "name": p.name,
                "confirmed": p.confirmed,
                "faces": p.n,
                "items": p.items,
                "coverUrl": face_url(p.cover_id) if p.cover_id else None,
            }
            for p in listed
        ],
        "unassigned": live_faces().filter(person__isnull=True).count(),
        "scan": {"done": min(scanned, total), "total": total},
    }


def person(person_id: Any) -> dict[str, Any]:
    try:
        p = Person.objects.filter(pk=int(person_id)).first()
    except (TypeError, ValueError):
        p = None
    if p is None:
        raise NotFoundError("That person is no longer in the list.")
    faces = list(live_faces().filter(person=p).select_related("file").order_by("-score"))
    return {
        "id": str(p.pk),
        "name": p.name,
        "confirmed": p.confirmed,
        "items": len({f.file.asset_id for f in faces}),
        "faces": [_face(f) for f in faces],
        "coverUrl": face_url(p.cover_id) if p.cover_id else None,
    }


def suggestions(person_id: Any) -> list[dict[str, Any]]:
    """Faces nobody holds, or in a provisional group, that look most like this
    person - for "add faces". Scored as the matcher scores them (their closest
    few faces; never one that looks more like a face taken away from them),
    but with a looser bar: the owner chooses, nothing moves by itself."""
    p = Person.objects.filter(pk=int(person_id)).first() if str(person_id).isdigit() else None
    if p is None:
        raise NotFoundError("That person is no longer in the list.")
    candidates = [
        f
        for f in live_faces().filter(Q(person__isnull=True) | Q(person__confirmed=False)).exclude(person=p)
        .select_related("file")
        if p.pk not in (f.rejected or [])
    ]
    if not candidates:
        return []
    score = matching.likely(p.pk, np.stack([f.vector() for f in candidates]))
    scored = sorted(zip(score.tolist(), candidates, strict=True), key=lambda t: -t[0])
    return [{**_face(f), "similarity": round(s, 3)} for s, f in scored[:SUGGEST_LIMIT] if s >= SUGGEST]


def unassigned(limit: int = 300) -> list[dict[str, Any]]:
    faces = live_faces().filter(person__isnull=True).select_related("file").order_by("-score")[:limit]
    return [_face(f) for f in faces]

