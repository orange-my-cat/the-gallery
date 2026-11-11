"""Tags and albums: the database side (ORG-01, ORG-02).

Changes the owner makes go through fileops (logged, undoable). These are the
primitives both fileops and the indexer use.
"""

from __future__ import annotations

from collections.abc import Iterable

from django.db.models import Count
from django.db.models.functions import Lower

from library.models import Asset
from organise.models import Album, AlbumItem, AssetTag, Tag

MAX_TAG = 100
MAX_ALBUM = 200


def tag(name: str) -> Tag:
    """The tag with this name, matched case-insensitively, created if new."""
    clean = name.strip()[:MAX_TAG]
    found = Tag.objects.annotate(lname=Lower("name")).filter(lname=clean.lower()).first()
    return found or Tag.objects.create(name=clean)


def tags_of(asset: Asset) -> list[str]:
    return list(asset.asset_tags.order_by(Lower("tag__name")).values_list("tag__name", flat=True))


def set_tags(asset: Asset, names: Iterable[str]) -> None:
    """Make the asset's tags exactly `names` (case-insensitive)."""
    wanted: dict[str, str] = {}
    for name in names:
        clean = name.strip()[:MAX_TAG]
        if clean and clean.lower() not in wanted:
            wanted[clean.lower()] = clean
    current = {at.tag.name.lower(): at for at in asset.asset_tags.select_related("tag")}
    for lower, at in current.items():
        if lower not in wanted:
            at.delete()
    for lower, name in wanted.items():
        if lower not in current:
            AssetTag.objects.get_or_create(asset=asset, tag=tag(name))
    prune_tags()


def prune_tags() -> None:
    """Tags no asset carries any more are forgotten."""
    Tag.objects.filter(asset_tags__isnull=True).delete()


def suggestions(limit: int = 12) -> list[str]:
    """The most used tags, for the tag popover."""
    return list(
        Tag.objects.annotate(n=Count("asset_tags")).order_by("-n", Lower("name")).values_list("name", flat=True)[:limit]
    )


def album_named(name: str) -> Album | None:
    return Album.objects.annotate(lname=Lower("name")).filter(lname=name.strip().lower()).first()


def album_members(album: Album) -> list[int]:
    return list(album.items.order_by("pk").values_list("asset_id", flat=True))


def add_to_album(album: Album, asset_ids: Iterable[int]) -> list[int]:
    """Adds those not already in it; returns the ids actually added."""
    present = set(album.items.values_list("asset_id", flat=True))
    added = [pk for pk in dict.fromkeys(asset_ids) if pk not in present]
    AlbumItem.objects.bulk_create([AlbumItem(album=album, asset_id=pk) for pk in added])
    if album.cover_id is None and added:
        album.cover_id = added[0]
        album.save(update_fields=["cover"])
    return added


def remove_from_album(album: Album, asset_ids: Iterable[int]) -> list[int]:
    ids = list(asset_ids)
    removed = list(album.items.filter(asset_id__in=ids).values_list("asset_id", flat=True))
    album.items.filter(asset_id__in=removed).delete()
    if album.cover_id in removed:
        album.cover_id = album.items.order_by("pk").values_list("asset_id", flat=True).first()
        album.save(update_fields=["cover"])
    return removed
