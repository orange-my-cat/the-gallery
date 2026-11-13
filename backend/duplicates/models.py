"""Duplicate groups (DUP-01 to DUP-06).

A group is a set of assets - items in the library - that are copies of one
picture: byte-identical (`exact`), or pixel-identical with different metadata
(`pixels`). Only identical copies are duplicates (the owner's rule,
2026-11-14): pictures that merely look alike are never grouped.

Groups are recomputed from the files after every scan; a group keeps its id
as long as its members stay the same, so the screen does not jump while the
owner reviews it.

A dismissed group is remembered by its members: it is not raised again unless
something new joins it. A merged group is history - the operation that merged
it says what happened, and undoing that operation reopens it.
"""

from __future__ import annotations

from django.db import models


class GroupKind(models.TextChoices):
    EXACT = "exact"  # the same bytes
    PIXELS = "pixels"  # the same picture, to the pixel; other metadata


class GroupState(models.TextChoices):
    OPEN = "open"
    MERGED = "merged"
    DISMISSED = "dismissed"


class DuplicateGroup(models.Model):
    kind = models.CharField(max_length=16, choices=GroupKind.choices)
    state = models.CharField(max_length=16, choices=GroupState.choices, default=GroupState.OPEN)
    # The member asset ids, sorted and comma-joined: how a recomputed group is
    # recognised as the same one, and a dismissed one as not to be raised again.
    key = models.TextField()
    # "3 copies of DSCF4821".
    title = models.CharField(max_length=300)
    # The merge under way or done; while it is pending, recomputing never
    # removes the group from under it.
    operation = models.ForeignKey(
        "fileops.Operation", null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    resolved_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        indexes = [models.Index(fields=["state", "kind"], name="dupgroup_state_kind_idx")]

    def __str__(self) -> str:
        return f"{self.title} ({self.kind}, {self.state})"

    @property
    def asset_ids(self) -> list[int]:
        return [int(pk) for pk in self.key.split(",") if pk]


class DuplicateMember(models.Model):
    group = models.ForeignKey(DuplicateGroup, on_delete=models.CASCADE, related_name="members")
    asset = models.ForeignKey("library.Asset", on_delete=models.CASCADE, related_name="+")
    # The file that matched - the asset's displayed file, or the copy of one
    # of its other files (a RAW, a Live Photo's clip) found elsewhere.
    file = models.ForeignKey("library.MediaFile", null=True, on_delete=models.SET_NULL, related_name="+")

    class Meta:
        constraints = [models.UniqueConstraint(fields=["group", "asset"], name="dupmember_unique")]
