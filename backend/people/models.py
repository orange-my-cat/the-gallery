"""People (PPL-01 to PPL-04): faces found in photos, and the people they belong to.

Faces are found and described on the PC (OpenCV Zoo's YuNet and SFace - the
owner's choice after the Phase 2 spike, 2026-11-14). A Face is one detection
in one file, with a 128-value description; a Person is a group of faces.

Groups the clustering found and the owner has not touched are provisional:
each clustering run may reshape them. Once the owner names a group or changes
it - merges, adds, removes - it is `confirmed`: clustering never reshapes it,
and new faces that look like it join it.
"""

from __future__ import annotations

import numpy as np
from django.db import models


class Person(models.Model):
    name = models.CharField(max_length=200, blank=True, default="")
    confirmed = models.BooleanField(default=False)
    # The face shown for the person; the clearest one unless the owner chose.
    cover = models.ForeignKey("Face", null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self) -> str:
        return self.name or f"Unknown person {self.pk}"


class AssignedBy(models.TextChoices):
    CLUSTER = "cluster"  # grouped by the clustering run
    MATCH = "match"  # joined a confirmed person it looks like
    OWNER = "owner"  # put there by the owner


class IgnoredBecause(models.TextChoices):
    NOT_FACE = "not_face"  # the owner: this detection is not a face
    NOT_PERSON = "not_person"  # the owner: this group is not a person (a statue, a poster)
    DELETED = "deleted"  # the owner deleted the group it was in
    # The app: it closely resembles faces the owner marked as not a face or
    # not a person. Decided again on every run.
    LOOKS_NOT_PERSON = "auto"


class Face(models.Model):
    file = models.ForeignKey("library.MediaFile", on_delete=models.CASCADE, related_name="faces")
    # Where, as fractions of the picture as shown (0 to 1).
    x = models.FloatField()
    y = models.FloatField()
    w = models.FloatField()
    h = models.FloatField()
    score = models.FloatField()
    # SFace's description: 128 float32, normalised to length 1.
    embedding = models.BinaryField()
    person = models.ForeignKey(Person, null=True, blank=True, on_delete=models.SET_NULL, related_name="faces")
    assigned_by = models.CharField(max_length=16, choices=AssignedBy.choices, blank=True, default="")
    # Not a face (PPL-04), or its group was deleted: never grouped again.
    ignored = models.BooleanField(default=False)
    ignored_because = models.CharField(max_length=16, choices=IgnoredBecause.choices, blank=True, default="")
    # People the owner took this face away from: never suggested for them again.
    rejected = models.JSONField(default=list, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [models.Index(fields=["ignored", "person"], name="face_pool_idx")]

    def vector(self) -> np.ndarray:
        return np.frombuffer(bytes(self.embedding), dtype=np.float32)


class FaceScan(models.Model):
    """That a file's picture has been searched for faces - by its pixels, so a
    metadata edit (new bytes, same picture) does not search it again."""

    file = models.OneToOneField("library.MediaFile", on_delete=models.CASCADE, related_name="face_scan")
    pixel_hash = models.CharField(max_length=64)
    found = models.PositiveSmallIntegerField(default=0)
    scanned_at = models.DateTimeField(auto_now=True)


class MatchRun(models.Model):
    """One matching run - `match` after the owner changes someone, `full`
    (matching and regrouping) after new faces and at least daily."""

    kind = models.CharField(max_length=8)
    started_at = models.DateTimeField(auto_now_add=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    summary = models.JSONField(default=dict, blank=True)

    class Meta:
        indexes = [models.Index(fields=["kind", "-finished_at"], name="matchrun_recent_idx")]
