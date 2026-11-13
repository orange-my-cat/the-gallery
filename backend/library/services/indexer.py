"""`index_file`: one file's hash, metadata, asset, and pairing.

The expensive part - hashing and reading metadata - happens before any lock is
taken. The database work is one short transaction holding an advisory lock on
the file's pair key, so two workers indexing `IMG_1.CR3` and `IMG_1.JPG` at the
same moment cannot each create an asset for half of the pair. A Live Photo's
still and clip are matched the same way (`library.services.live`).
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from django.db import connection, transaction
from django.db.models import F

from jobs.models import Job
from library.models import (
    Asset,
    AssetKind,
    CaptureSource,
    FileMetadata,
    FileRole,
    FileState,
    MediaFile,
    ScanRun,
)
from library.services import live, metadata, thumbnails
from library.services.metadata import Normalised
from library.services.paths import (
    PAIRABLE_JPEG_EXTENSIONS,
    content_type,
    extension,
    file_type,
    library_root,
    pair_key,
    to_abs,
)

logger = logging.getLogger("gallery")

_CHUNK = 1024 * 1024
# Machine-specific, and wrong as soon as the file moves.
_DROPPED_TAGS = frozenset({"SourceFile", "File:Directory", "File:FileAccessDate", "File:FileInodeChangeDate"})


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(_CHUNK):
            digest.update(chunk)
    return digest.hexdigest()


def clean_raw(value: Any) -> Any:
    """ExifTool's output made safe for `jsonb`, which refuses NUL characters."""
    if isinstance(value, dict):
        return {k: clean_raw(v) for k, v in value.items() if k not in _DROPPED_TAGS}
    if isinstance(value, list):
        return [clean_raw(v) for v in value]
    if isinstance(value, str):
        return value.replace("\x00", "")
    return value


# Asset fields that come from the file. `favourite` and `place` do not, so a
# re-read never touches them.
_ASSET_FIELDS = (
    "capture_at",
    "capture_offset",
    "lat",
    "lon",
    "title",
    "description",
    "rating",
    "camera_make",
    "camera_model",
    "lens",
    "exposure_time",
    "f_number",
    "iso",
    "focal_length",
    "duration",
)


def _apply(asset: Asset, norm: Normalised) -> None:
    moved = (asset.lat, asset.lon) != (norm.lat, norm.lon)
    for field in _ASSET_FIELDS:
        setattr(asset, field, getattr(norm, field))
    if moved:  # MET-03: its place is worked out again for the new position
        asset.geo_place = None
        asset.geocoded = False
    asset.capture_source = CaptureSource(norm.capture_source)
    # A place name the file states wins; without one, the place stays as it
    # is. (A name worked out from GPS is kept apart, in geo_place.)
    if norm.place:
        asset.place = norm.place


def _sync_tags(asset: Asset, norm: Normalised) -> None:
    from organise.services import set_tags

    set_tags(asset, norm.keywords)


def read_with_sidecars(path: Path) -> dict[str, Any]:
    """The file's tags plus its XMP sidecar's, which outrank them (MET-02).

    `IMG_1234.xmp` (Lightroom's) and `IMG_1234.CR3.xmp` (darktable's) are both
    read; the per-file one wins where they disagree.
    """
    raw = dict(metadata.read(path))
    for sidecar in (path.with_suffix(".xmp"), path.with_name(path.name + ".xmp")):
        if sidecar.is_file():
            raw.update(metadata.read_sidecar(sidecar))
    return raw


def file_mtime(mtime_ns: int) -> datetime:
    return datetime.fromtimestamp(mtime_ns / 1e9, tz=UTC)


def renormalise(batch: int = 500) -> int:
    """Re-apply the normalising rules to every asset from its stored raw tags.

    No file is opened and ExifTool is not run: FileMetadata keeps everything
    ExifTool read, which is what makes a rule change cheap to roll out.
    """
    changed = 0
    qs = (
        Asset.objects.filter(primary_file__isnull=False, primary_file__metadata__isnull=False)
        .select_related("primary_file__metadata")
        .order_by("pk")
    )
    for asset in qs.iterator(chunk_size=batch):
        media = asset.primary_file
        assert media is not None
        norm = metadata.normalise(media.metadata.raw, file_mtime=file_mtime(media.mtime_ns), name=media.rel_path)
        if asset.date_edited:
            # A date set by hand stands, whatever the rules now say.
            norm = replace(norm, capture_at=asset.capture_at, capture_offset=asset.capture_offset,
                           capture_source=asset.capture_source)
        before = {f: getattr(asset, f) for f in (*_ASSET_FIELDS, "capture_source", "place")}
        _apply(asset, norm)
        if any(getattr(asset, f) != v for f, v in before.items()):
            asset.save()
            changed += 1
    return changed


def _file_fields(norm: Normalised, size: int, mtime_ns: int, sha: str, scan_id: int | None) -> dict:
    return {
        "size": size,
        "mtime_ns": mtime_ns,
        "sha256": sha,
        "width": norm.width,
        "height": norm.height,
        "mime": norm.mime[:100],
        "live_id": norm.live_id,
        "state": FileState.PRESENT,
        "missed_scans": 0,
        "last_seen_scan_id": scan_id,
    }


def _is_pairable_jpeg(rel: str, mime: str) -> bool:
    # Named like a JPEG and holding a photo: a clip named .JPG pairs with nothing.
    return extension(rel) in PAIRABLE_JPEG_EXTENSIONS and content_type(rel, mime) == "photo"


def _find_partner(rel: str, key: str, mime: str) -> MediaFile | None:
    """The other half of a RAW+JPEG pair, if it is indexed and still single."""
    mine = file_type(rel)
    if mine != "raw" and not _is_pairable_jpeg(rel, mime):
        return None
    for candidate in MediaFile.objects.select_for_update().filter(pair_key=key).exclude(
        state=FileState.TRASHED
    ).exclude(rel_path=rel):
        if candidate.role != FileRole.PRIMARY:
            continue  # already half of a pair
        if mine == "raw" and _is_pairable_jpeg(candidate.rel_path, candidate.mime):
            return candidate
        if mine != "raw" and file_type(candidate.rel_path) == "raw":
            return candidate
    return None


def _asset_kind(rel: str, mime: str) -> AssetKind:
    return AssetKind.VIDEO if content_type(rel, mime) == "video" else AssetKind.PHOTO


def _find_moved(sha: str, rel: str, root: Path) -> MediaFile | None:
    """A known file with these bytes that is no longer where it was (LIB-04)."""
    for candidate in MediaFile.objects.select_for_update().filter(sha256=sha).exclude(
        state=FileState.TRASHED
    ).exclude(rel_path=rel):
        if not to_abs(candidate.rel_path, root).exists():
            return candidate
    return None


def index_path(rel: str, scan_id: int | None = None) -> str:
    """Index one file. Returns 'new', 'changed', 'moved', 'unchanged' or 'gone'."""
    root = library_root()
    path = to_abs(rel, root)
    if not path.is_file():
        return "gone"  # vanished since the walk; reconcile will notice

    stat = path.stat()
    sha = sha256_file(path)
    raw = clean_raw(read_with_sidecars(path))
    norm = metadata.normalise(raw, file_mtime=file_mtime(stat.st_mtime_ns), name=rel)
    key = pair_key(rel)
    fields = _file_fields(norm, stat.st_size, stat.st_mtime_ns, sha, scan_id)

    with transaction.atomic():
        with connection.cursor() as cursor:
            cursor.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", [key])
            # A Live Photo's halves can be named apart (IMG_1.JPG, IMG_1_a.MOV),
            # so they also meet on their identifier. Always taken second, after
            # the pair key, so two indexers can never wait on each other.
            if norm.live_id:
                cursor.execute(
                    "SELECT pg_advisory_xact_lock(hashtext(%s))", [f"live:{live.folder_of(rel)}/{norm.live_id}"]
                )

        media = MediaFile.objects.select_for_update().filter(rel_path=rel).first()
        if media is not None:
            outcome = "changed" if media.sha256 != sha else "unchanged"
        else:
            media = _find_moved(sha, rel, root)
            outcome = "moved" if media is not None else "new"

        if media is not None:
            for name, value in fields.items():
                setattr(media, name, value)
            media.rel_path = rel
            media.pair_key = key
            media.save()
            if outcome == "changed":
                asset = media.asset
                if asset.primary_file_id == media.pk:
                    _apply(asset, norm)
                    if media.role == FileRole.PRIMARY:  # a pair's kind is the photo's
                        asset.kind = _asset_kind(rel, norm.mime)
                    asset.save()
                    _sync_tags(asset, norm)
        else:
            media = _create(rel, key, fields, norm)

        FileMetadata.objects.update_or_create(file=media, defaults={"raw": raw})

    if scan_id is not None:
        counter = {"new": "new", "changed": "changed", "moved": "moved"}.get(outcome)
        updates = {"indexed": F("indexed") + 1}
        if counter:
            updates[counter] = F(counter) + 1
        ScanRun.objects.filter(pk=scan_id).update(**updates)

    if media.role != FileRole.LIVE_VIDEO:  # a Live Photo's clip is never a tile
        thumbnails.queue_missing(media, scan_id=scan_id)
    return outcome


def _create(rel: str, key: str, fields: dict, norm: Normalised) -> MediaFile:
    # A Live Photo's clip whose still is already here joins it.
    still = live.still_for_clip(rel, norm.mime, norm.live_id, norm.duration)
    if still is not None:
        return MediaFile.objects.create(
            asset=still.asset, rel_path=rel, pair_key=key, role=FileRole.LIVE_VIDEO, **fields
        )

    media = _create_item(rel, key, fields, norm)
    # And a still whose clip came first takes it in.
    clip = live.clip_for_still(media)
    if clip is not None:
        live.attach(clip, media.asset)
    return media


def _create_item(rel: str, key: str, fields: dict, norm: Normalised) -> MediaFile:
    partner = _find_partner(rel, key, norm.mime)
    if partner is None:
        asset = Asset(kind=_asset_kind(rel, norm.mime))
        _apply(asset, norm)
        asset.save()
        media = MediaFile.objects.create(asset=asset, rel_path=rel, pair_key=key, role=FileRole.PRIMARY, **fields)
        asset.primary_file = media
        asset.save(update_fields=["primary_file", "updated_at"])
        _sync_tags(asset, norm)
        return media

    # LIB-05: one asset for the pair, shown through the JPEG.
    asset = partner.asset
    if file_type(rel) == "raw":
        media = MediaFile.objects.create(asset=asset, rel_path=rel, pair_key=key, role=FileRole.RAW, **fields)
        partner.role = FileRole.PAIRED_JPEG
        partner.save(update_fields=["role", "updated_at"])
    else:
        media = MediaFile.objects.create(
            asset=asset, rel_path=rel, pair_key=key, role=FileRole.PAIRED_JPEG, **fields
        )
        partner.role = FileRole.RAW
        partner.save(update_fields=["role", "updated_at"])
        asset.primary_file = media
        _apply(asset, norm)
        asset.save()
        _sync_tags(asset, norm)
    return media


def index_file_job(job: Job) -> None:
    index_path(job.payload["path"], scan_id=job.scan_id)
