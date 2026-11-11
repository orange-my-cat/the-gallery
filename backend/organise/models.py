"""Albums and tags (ORG-01, ORG-02).

Albums are the app's own: they group assets without moving a file, one asset
can be in many, and they live only in the database. Tags are the files' own
keywords: they are written into the files (XMP dc:subject, MET-02) and read
back by every scan, so other editors see them and the folder stays the source
of truth.
"""

from __future__ import annotations

from django.db import models
from django.db.models.functions import Lower


class Album(models.Model):
    name = models.CharField(max_length=200)
    cover = models.ForeignKey("library.Asset", null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(Lower("name"), name="album_name_unique_ci")]

    def __str__(self) -> str:
        return self.name


class AlbumItem(models.Model):
    album = models.ForeignKey(Album, on_delete=models.CASCADE, related_name="items")
    asset = models.ForeignKey("library.Asset", on_delete=models.CASCADE, related_name="album_items")
    added_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["album", "asset"], name="album_item_unique")]


class Tag(models.Model):
    name = models.CharField(max_length=100)

    class Meta:
        constraints = [models.UniqueConstraint(Lower("name"), name="tag_name_unique_ci")]

    def __str__(self) -> str:
        return self.name


class AssetTag(models.Model):
    asset = models.ForeignKey("library.Asset", on_delete=models.CASCADE, related_name="asset_tags")
    tag = models.ForeignKey(Tag, on_delete=models.CASCADE, related_name="asset_tags")

    class Meta:
        constraints = [models.UniqueConstraint(fields=["asset", "tag"], name="asset_tag_unique")]
