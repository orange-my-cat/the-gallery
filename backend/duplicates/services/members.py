"""What the duplicate code needs to know about each asset, loaded in bulk.

`load(ids)` returns, per asset, its displayed file, every present file (for
keeper feasibility), and the `rules.Copy` the merge rules work on.
"""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from library.models import Asset, CaptureSource, FileRole, FileState, MediaFile
from library.services.browse import local_capture
from organise.models import AlbumItem, AssetTag

from duplicates.services.rules import Copy

# "IMG_1 (1)", "IMG_1 - Copy", "IMG_1 copy 2", "IMG_1-1" (Windows, macOS, Explorer).
_COPY_NAME = re.compile(r"(\s\(\d+\)|\s-\s?copy(\s\(\d+\))?|\scopy(\s\d+)?|-\d)$", re.IGNORECASE)


def copy_name(name: str) -> bool:
    stem = name.rsplit(".", 1)[0] if "." in name else name
    return bool(_COPY_NAME.search(stem))


@dataclass
class Member:
    asset: Asset
    display: MediaFile | None
    files: list[MediaFile]
    raw: dict[str, Any]
    copy: Copy
    shas: set[str] = field(default_factory=set)
    # Pixel fingerprints of its files: a copy here is covered by one there.
    pixels: set[str] = field(default_factory=set)

    @property
    def extras(self) -> list[MediaFile]:
        """Files besides the displayed one: a RAW, a Live Photo's clip."""
        return [f for f in self.files if self.display is None or f.pk != self.display.pk]


def _has(raw: dict[str, Any], *keys: str) -> bool:
    return any(raw.get(k) not in (None, "", []) for k in keys)


def camera_original(raw: dict[str, Any]) -> bool:
    return (
        _has(raw, "EXIF:Make", "QuickTime:Make")
        and _has(raw, "EXIF:Model", "QuickTime:Model")
        and _has(raw, "EXIF:DateTimeOriginal", "Composite:SubSecDateTimeOriginal", "QuickTime:CreationDate")
    )


def _local(asset: Asset) -> datetime:
    return datetime.fromisoformat(local_capture(asset)[0])


def load(ids) -> dict[int, Member]:
    ids = list(dict.fromkeys(int(i) for i in ids))
    assets = {a.pk: a for a in Asset.objects.filter(pk__in=ids)}
    files: dict[int, list[MediaFile]] = defaultdict(list)
    for f in (
        MediaFile.objects.filter(asset_id__in=ids, state=FileState.PRESENT)
        .select_related("metadata")
        .order_by("pk")
    ):
        files[f.asset_id].append(f)
    tags: dict[int, list[str]] = defaultdict(list)
    for asset_id, name in AssetTag.objects.filter(asset_id__in=ids).order_by("tag__name").values_list(
        "asset_id", "tag__name"
    ):
        tags[asset_id].append(name)
    albums: dict[int, set[int]] = defaultdict(set)
    for asset_id, album_id in AlbumItem.objects.filter(asset_id__in=ids).values_list("asset_id", "album_id"):
        albums[asset_id].add(album_id)

    out: dict[int, Member] = {}
    for pk in ids:
        asset = assets.get(pk)
        present = files.get(pk, [])
        if asset is None or not present:
            continue
        shown = [f for f in present if f.role != FileRole.LIVE_VIDEO]
        display = next((f for f in shown if f.pk == asset.primary_file_id), None) or (shown[0] if shown else present[0])
        raw = dict(getattr(getattr(display, "metadata", None), "raw", None) or {})
        dated = asset.capture_source == CaptureSource.METADATA or asset.date_edited
        copy = Copy(
            id=pk,
            pixels=(display.width or 0) * (display.height or 0),
            size=display.size,
            camera_original=camera_original(raw),
            copy_name=copy_name(display.name),
            first_seen=datetime.fromtimestamp(min(f.mtime_ns for f in present) / 1e9, tz=UTC),
            capture_at=asset.capture_at if dated else None,
            capture_local=_local(asset) if dated else None,
            capture_offset=asset.capture_offset if dated else None,
            lat=asset.lat,
            lon=asset.lon,
            camera_make=asset.camera_make,
            camera_model=asset.camera_model,
            lens=asset.lens,
            keywords=tuple(tags.get(pk, [])),
            rating=asset.rating or 0,
            title=asset.title,
            description=asset.description,
            place=asset.place,
            favourite=asset.favourite,
            albums=frozenset(albums.get(pk, set())),
        )
        out[pk] = Member(
            asset=asset,
            display=display,
            files=present,
            raw=raw,
            copy=copy,
            shas={f.sha256 for f in present},
            pixels={f.pixel_hash for f in present if f.pixel_hash},
        )
    return out
