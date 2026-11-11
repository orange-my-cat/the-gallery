"""Read-side queries: the timeline, the library summary, scan status (BRW-01, LIB-06).

Shapes match `frontend/src/lib/types.ts` field for field, so the API layer only
wraps them. Times leave here as local wall-clock strings with the offset beside
them, because when a photo was taken is a local fact.
"""

from __future__ import annotations

import base64
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import PureWindowsPath
from typing import Any
from zoneinfo import ZoneInfo

from django.conf import settings
from django.db.models import Count, Exists, Max, OuterRef, Prefetch, Q, QuerySet, Sum
from django.db.models.functions import Lower

from core.models import AppSettings
from core.services.exceptions import BusinessRuleError
from jobs.models import Job, JobState
from jobs.services import worker_health
from library.models import (
    ACTIVE_SCAN_STATES,
    Asset,
    AssetKind,
    CaptureSource,
    FileRole,
    FileState,
    MediaFile,
    ScanRun,
    ScanState,
)
from library.services.folders import approx_label, folder_date, parse_folder_name, span_label
from library.services.paths import RAW_EXTENSIONS, extension, must_choose_folder, normalise_rel, scan_root
from library.services.thumbnails import cache_path
from organise.models import AlbumItem, AssetTag

# A file missing for this many consecutive scans no longer shows (HLD, Reconcile).
MISSING_GRACE = 2
DEFAULT_PAGE = 500
MAX_PAGE = 2000
# Used for layout when a file states no dimensions.
FALLBACK_SIZE = (1600, 1200)


class BadCursor(BusinessRuleError):
    code = "bad_cursor"


# ---------------------------------------------------------------------------
# Visibility
# ---------------------------------------------------------------------------


def visible_files() -> QuerySet[MediaFile]:
    return MediaFile.objects.filter(
        Q(state=FileState.PRESENT) | Q(state=FileState.MISSING, missed_scans__lt=MISSING_GRACE)
    )


def visible_assets() -> QuerySet[Asset]:
    return Asset.objects.filter(Exists(visible_files().filter(asset=OuterRef("pk"))))


# ---------------------------------------------------------------------------
# Display helpers
# ---------------------------------------------------------------------------


def display_root() -> str:
    """The library root as the owner knows it: `D:\\Photos`, not `/library`."""
    if must_choose_folder():
        return "This PC"
    raw = (getattr(settings, "LIBRARY_HOST_DIR", "") or "").strip() or str(settings.LIBRARY_ROOT or "")
    if len(raw) >= 2 and raw[1] == ":":
        return str(PureWindowsPath(raw))
    return raw.rstrip("/")


def display_path(rel: str) -> str:
    """A folder relative to the root, as the owner knows it.

    With whole drives mounted, the first segment is the drive letter:
    `D/Media/Photos` is `D:\\Media\\Photos`.
    """
    rel = normalise_rel(rel)
    if must_choose_folder():
        drive, _, rest = rel.partition("/")
        return f"{drive}:\\" + rest.replace("/", "\\") if drive else display_root()
    root = display_root()
    if not rel:
        return root
    sep = "\\" if "\\" in root or (len(root) >= 2 and root[1] == ":") else "/"
    return root.rstrip("\\/") + sep + rel.replace("/", sep)


def display_folder(rel_path: str) -> str:
    """The folder a file is in, with a trailing separator: `D:\\Photos\\2024\\`."""
    shown = display_path(rel_path.rpartition("/")[0])
    sep = "\\" if "\\" in shown or (len(shown) >= 2 and shown[1] == ":") else "/"
    return shown if shown.endswith(sep) else shown + sep


def local_capture(asset: Asset) -> tuple[str, str]:
    """('2024-07-14T18:30:00', '+08:00')."""
    if asset.capture_offset is not None:
        zone: Any = timezone(timedelta(minutes=asset.capture_offset))
    else:
        zone = ZoneInfo(settings.TIME_ZONE)
    local = asset.capture_at.astimezone(zone)
    minutes = int((local.utcoffset() or timedelta()).total_seconds() // 60)
    sign = "-" if minutes < 0 else "+"
    return local.strftime("%Y-%m-%dT%H:%M:%S"), f"{sign}{abs(minutes) // 60:02d}:{abs(minutes) % 60:02d}"


def camera_name(make: str, model: str) -> str:
    """'Canon' + 'Canon EOS R5' -> 'Canon EOS R5'; 'FUJIFILM' + 'X-T5' -> 'FUJIFILM X-T5'."""
    make, model = make.strip(), model.strip()
    if not model:
        return make
    first = make.split(" ")[0].lower() if make else ""
    return model if not first or model.lower().startswith(first) else f"{make} {model}"


def shutter(seconds: float | None) -> str:
    if not seconds or seconds <= 0:
        return ""
    if seconds >= 1:
        return f"{seconds:g}"
    return f"1/{round(1 / seconds)}"


def _media_kind(asset: Asset, primary: MediaFile, raw: MediaFile | None) -> str:
    if asset.kind == AssetKind.VIDEO:
        return "video"
    if raw is not None and primary.role == FileRole.PAIRED_JPEG:
        return "pair"
    ext = extension(primary.rel_path)
    if ext in RAW_EXTENSIONS:
        return "raw"
    if ext in ("heic", "heif"):
        return "heic"
    return "jpg"


def present_asset(asset: Asset, *, trashed: bool = False) -> dict[str, Any] | None:
    """The asset as the screens see it - its trashed files only, for the Trash view."""
    every = [f for f in asset.files.all() if (f.state == FileState.TRASHED) == trashed]
    # A Live Photo's clip plays over the still; it is never what is shown.
    motion = next((f for f in every if f.role == FileRole.LIVE_VIDEO and f.state != FileState.MISSING), None)
    files = [f for f in every if f.role != FileRole.LIVE_VIDEO]
    primary = next((f for f in files if f.pk == asset.primary_file_id), None) or (files[0] if files else None)
    if primary is None:
        return None
    raw = next((f for f in files if f.role == FileRole.RAW), None)
    kind = _media_kind(asset, primary, raw)
    # A pair is named by its RAW, the original kept alongside the JPEG shown.
    named = raw if kind == "pair" and raw is not None else primary
    captured, offset = local_capture(asset)
    width, height = primary.width or FALLBACK_SIZE[0], primary.height or FALLBACK_SIZE[1]
    version = primary.sha256[:16]
    approx = None
    if asset.capture_source == CaptureSource.FOLDER:
        found = folder_date(named.rel_path)
        if found is not None:
            approx = {"precision": found.precision, "label": approx_label(found)}
    # Shown where it lives now, or for a trashed item where it came from.
    shown_path = named.trashed_from if trashed and named.trashed_from else named.rel_path
    return {
        "dateApprox": approx,
        "id": str(asset.pk),
        "capturedAt": captured,
        "captureOffset": offset,
        "kind": kind,
        "ext": extension(named.rel_path).upper(),
        "name": named.name,
        "pairName": primary.name if kind == "pair" else None,
        "width": width,
        "height": height,
        "durationS": round(asset.duration or 0),
        "size": named.size,
        "pairSize": primary.size if kind == "pair" else 0,
        "camera": camera_name(asset.camera_make, asset.camera_model),
        "lens": asset.lens,
        "focalLength": round(asset.focal_length) if asset.focal_length else 0,
        "fNumber": asset.f_number or 0,
        "shutter": shutter(asset.exposure_time),
        "iso": asset.iso or 0,
        "place": asset.place,
        "gps": [asset.lat, asset.lon] if asset.lat is not None and asset.lon is not None else None,
        "folder": display_folder(shown_path),
        "title": asset.title,
        "description": asset.description,
        "dateEdited": asset.date_edited,
        "favourite": asset.favourite,
        "rating": asset.rating,
        "tags": sorted((at.tag.name for at in asset.asset_tags.all()), key=str.lower),
        "albums": sorted((ai.album.name for ai in asset.album_items.all()), key=str.lower),
        "thumbUrl": f"/media/thumb/{primary.pk}/400?v={version}",
        "previewUrl": f"/media/preview/{primary.pk}?v={version}",
        "originalUrl": f"/media/original/{named.pk}",
        "videoUrl": f"/media/original/{primary.pk}" if kind == "video" else None,
        "liveUrl": f"/media/original/{motion.pk}" if motion is not None else None,
        "thumbReady": cache_path(primary.sha256, "small").exists(),
        "missing": all(f.state == FileState.MISSING for f in files),
    }


# ---------------------------------------------------------------------------
# The timeline
# ---------------------------------------------------------------------------


def encode_cursor(asset: Asset) -> str:
    raw = f"{asset.capture_at.isoformat()}|{asset.pk}".encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def decode_cursor(cursor: str) -> tuple[datetime, int]:
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        when, pk = base64.urlsafe_b64decode(padded.encode()).decode().split("|")
        return datetime.fromisoformat(when), int(pk)
    except (ValueError, UnicodeDecodeError) as exc:
        raise BadCursor("That page link is not valid. Reload the timeline.") from exc


def search(qs: QuerySet[Asset], query: str) -> QuerySet[Asset]:
    """BRW-07: file name and folder, title, description, place, camera, tags
    and albums. Every word must match somewhere."""
    for word in query.split():
        qs = qs.filter(
            Q(files__rel_path__icontains=word)
            | Q(title__icontains=word)
            | Q(description__icontains=word)
            | Q(place__icontains=word)
            | Q(camera_make__icontains=word)
            | Q(camera_model__icontains=word)
            | Q(lens__icontains=word)
            | Q(asset_tags__tag__name__icontains=word)
            | Q(album_items__album__name__icontains=word)
        )
    return qs.distinct() if query.split() else qs


@dataclass(frozen=True)
class Page:
    results: list[dict[str, Any]]
    next: str | None


def in_folder(qs: QuerySet[Asset], folder: str) -> QuerySet[Asset]:
    """Assets with a visible file in this folder or anything below it."""
    folder = normalise_rel(folder)
    if not folder:
        return qs
    return qs.filter(Exists(visible_files().filter(asset=OuterRef("pk"), rel_path__startswith=folder + "/")))


def with_details(qs: QuerySet[Asset]) -> QuerySet[Asset]:
    """Everything present_asset reads, in a fixed number of queries."""
    return qs.prefetch_related(
        Prefetch("files", queryset=MediaFile.objects.order_by("pk")),
        Prefetch("asset_tags", queryset=AssetTag.objects.select_related("tag")),
        Prefetch("album_items", queryset=AlbumItem.objects.select_related("album")),
    )


def timeline_page(
    *,
    cursor: str | None = None,
    limit: int = DEFAULT_PAGE,
    query: str = "",
    folder: str = "",
    album: int | None = None,
    favourites: bool = False,
) -> Page:
    limit = max(1, min(limit, MAX_PAGE))
    qs = search(in_folder(visible_assets(), folder), query.strip())
    if album is not None:
        qs = qs.filter(album_items__album_id=album)
    if favourites:
        qs = qs.filter(favourite=True)
    if cursor:
        when, pk = decode_cursor(cursor)
        qs = qs.filter(Q(capture_at__lt=when) | Q(capture_at=when, pk__lt=pk))
    rows = list(with_details(qs.order_by("-capture_at", "-pk"))[: limit + 1])
    more = len(rows) > limit
    rows = rows[:limit]
    results = [dto for dto in (present_asset(a) for a in rows) if dto is not None]
    return Page(results=results, next=encode_cursor(rows[-1]) if more and rows else None)


# ---------------------------------------------------------------------------
# Folders (BRW-03): the folder tree, one level at a time, as album cards
# ---------------------------------------------------------------------------

# Dates that belong to the photo itself, as opposed to its folder or file time.
REAL_DATE_SOURCES = frozenset({CaptureSource.METADATA, CaptureSource.FILE_NAME})


def _local_date(capture_at: datetime, offset: int | None):
    zone: Any = timezone(timedelta(minutes=offset)) if offset is not None else ZoneInfo(settings.TIME_ZONE)
    return capture_at.astimezone(zone).date()


def folder_listing(path: str = "") -> dict[str, Any]:
    """The folders directly inside `path`, each with its title, dates, size and cover.

    One query over the visible files under `path`; folders are derived from
    the stored paths, so a folder exists here exactly when it holds something
    the library shows. No path means the library folder itself.
    """
    base = normalise_rel(AppSettings.load().library_subfolder)
    path = normalise_rel(path) or base
    prefix = f"{path}/" if path else ""
    files = visible_files().filter(rel_path__startswith=prefix) if prefix else visible_files()
    rows = files.values_list(
        "pk", "rel_path", "sha256", "asset_id", "asset__primary_file_id",
        "asset__capture_at", "asset__capture_offset", "asset__capture_source", "asset__kind",
    )

    children: dict[str, dict[str, Any]] = {}
    loose: set[int] = set()
    for pk, rel, sha, asset_id, primary_id, captured, offset, source, kind in rows.iterator(chunk_size=5000):
        rest = rel[len(prefix):]
        name, sep, below = rest.partition("/")
        if not sep:
            loose.add(asset_id)
            continue
        child = children.setdefault(
            name, {"assets": set(), "sub": set(), "first": None, "last": None, "cover": None, "fallback": None}
        )
        child["assets"].add(asset_id)
        if "/" in below:
            child["sub"].add(below.split("/", 1)[0])
        if pk != primary_id:
            continue
        if child["fallback"] is None:
            child["fallback"] = (pk, sha)
        if source in REAL_DATE_SOURCES:
            day = _local_date(captured, offset)
            child["first"] = day if child["first"] is None else min(child["first"], day)
            child["last"] = day if child["last"] is None else max(child["last"], day)
            # The cover is the trip's first photo: where the story starts.
            if kind == AssetKind.PHOTO and (child["cover"] is None or captured < child["cover"][0]):
                child["cover"] = (captured, pk, sha)

    folders = []
    for name, child in children.items():
        found = parse_folder_name(name)
        cover = child["cover"][1:] if child["cover"] else child["fallback"]
        cover_url = None
        if cover is not None and cache_path(cover[1], "small").exists():
            cover_url = f"/media/thumb/{cover[0]}/400?v={cover[1][:16]}"
        folders.append(
            {
                "path": f"{prefix}{name}",
                "name": name,
                "title": (found.title if found and found.title else name),
                "date": found.start.isoformat() if found else None,
                "precision": found.precision if found else None,
                "span": span_label(found, child["first"], child["last"]),
                "count": len(child["assets"]),
                "subfolders": len(child["sub"]),
                "coverUrl": cover_url,
            }
        )
    # Newest first, as the timeline is; folders without a date after, by name.
    folders.sort(key=lambda f: f["name"].lower())
    folders.sort(key=lambda f: f["date"] or "", reverse=True)

    here = parse_folder_name(path.rsplit("/", 1)[-1]) if path else None
    return {
        "path": path,
        # The library folder: Folders' top level, above which it does not go.
        "base": base,
        "title": (here.title if here and here.title else path.rsplit("/", 1)[-1]) if path != base else "",
        "folders": folders,
        "looseCount": len(loose),
    }


# ---------------------------------------------------------------------------
# Trash, albums, move targets (FOP-02, ORG-01, FOP-01)
# ---------------------------------------------------------------------------


def trash_listing() -> dict[str, Any]:
    """What is in the trash, most recently deleted first, with when each goes for good."""
    days = AppSettings.load().trash_retention_days
    assets = with_details(
        Asset.objects.filter(files__state=FileState.TRASHED)
        .annotate(last_trashed=Max("files__trashed_at", filter=Q(files__state=FileState.TRASHED)))
        .order_by("-last_trashed", "-pk")
        .distinct()
    )
    items = []
    for asset in assets:
        dto = present_asset(asset, trashed=True)
        if dto is None:
            continue
        when = asset.last_trashed
        dto["trashedAt"] = when.isoformat() if when else None
        dto["purgeAt"] = (when + timedelta(days=days)).isoformat() if when else None
        items.append(dto)
    return {"items": items, "count": len(items), "retentionDays": days}


def trash_count() -> int:
    return Asset.objects.filter(files__state=FileState.TRASHED).distinct().count()


def albums_listing() -> list[dict[str, Any]]:
    from organise.models import Album

    visible = visible_assets().values("pk")
    out = []
    for album in Album.objects.select_related("cover__primary_file").order_by(Lower("name")):
        n = album.items.filter(asset__in=visible).count()
        cover = album.cover.primary_file if album.cover and album.cover.primary_file else None
        ready = cover is not None and cache_path(cover.sha256, "small").exists()
        out.append(
            {
                "id": str(album.pk),
                "name": album.name,
                "count": n,
                "coverUrl": f"/media/thumb/{cover.pk}/400?v={cover.sha256[:16]}" if ready and cover else None,
            }
        )
    return out


def move_targets() -> list[str]:
    """Folders inside the library folder that hold something, relative to it."""
    base = normalise_rel(AppSettings.load().library_subfolder)
    prefix = f"{base}/" if base else ""
    folders: set[str] = set()
    for rel in visible_files().filter(rel_path__startswith=prefix).values_list("rel_path", flat=True):
        folder = rel[len(prefix):].rpartition("/")[0]
        if folder:
            folders.add(folder)
    return sorted(folders, key=str.lower)


# ---------------------------------------------------------------------------
# Library summary and scan status
# ---------------------------------------------------------------------------


def library_available() -> bool:
    """There is a library folder to scan: the root exists, and (where whole
    drives are mounted) a folder has been chosen and still exists."""
    try:
        scan_root()
    except BusinessRuleError:
        return False
    return True


def library_folder_display() -> str:
    return display_path(AppSettings.load().library_subfolder)


def library_info() -> dict[str, Any]:
    if not library_available():
        return {"path": None, "itemCount": 0, "totalBytes": 0}
    return {
        "path": library_folder_display(),
        "itemCount": visible_assets().count(),
        "totalBytes": visible_files().aggregate(total=Sum("size"))["total"] or 0,
    }


def _failed_files() -> int:
    return Job.objects.filter(
        state=JobState.FAILED, kind__in=["index_file", "thumb_small", "thumb_large"]
    ).count()


def _pending() -> int:
    """Jobs still to run: thumbnails keep going after a scan has reconciled."""
    return Job.objects.filter(state__in=[JobState.QUEUED, JobState.RUNNING]).count()


def scan_status() -> dict[str, Any]:
    last_done = ScanRun.objects.filter(finished_at__isnull=False).order_by("-finished_at").first()
    last_at = last_done.finished_at.isoformat() if last_done and last_done.finished_at else None
    common = {"lastScanAt": last_at, "pending": _pending(), "workerOnline": worker_health()["online"]}

    if not library_available():
        return {**common, "state": "none", "firstScan": False, "done": 0, "total": 0, "failedFiles": 0,
                "message": None}

    active = ScanRun.objects.filter(state__in=ACTIVE_SCAN_STATES).first()
    if active is not None:
        failed = Job.objects.filter(scan=active, kind="index_file", state=JobState.FAILED).count()
        return {
            **common,
            "state": "scanning",
            "firstScan": active.first_scan,
            "done": min(active.indexed + failed, active.queued),
            "total": active.queued,
            "failedFiles": 0,
            "message": None,
        }

    failed_files = _failed_files()
    message = last_done.error if last_done and last_done.state == ScanState.FAILED else None
    return {
        **common,
        "state": "errors" if failed_files or message else "idle",
        "firstScan": False,
        "done": 0,
        "total": 0,
        "failedFiles": failed_files,
        "message": message or None,
    }


FAILED_SHOWN = 200


def scan_details() -> dict[str, Any]:
    """The status view behind the scan pill (LIB-06; HLD: it doubles as the
    health view): the worker, the queue, files that could not be read, and
    the last few scans."""
    queue: dict[str, int] = {}
    for kind, n in (
        Job.objects.filter(state__in=[JobState.QUEUED, JobState.RUNNING])
        .values_list("kind")
        .annotate(n=Count("pk"))
    ):
        queue[kind] = n

    failed_jobs = list(
        Job.objects.filter(state=JobState.FAILED).order_by("-pk")[:FAILED_SHOWN]
    )
    file_ids = [j.payload.get("file") for j in failed_jobs if "file" in j.payload]
    paths = dict(MediaFile.objects.filter(pk__in=file_ids).values_list("pk", "rel_path"))
    failed = [
        {
            "id": job.pk,
            "kind": job.kind,
            "path": job.payload.get("path") or paths.get(job.payload.get("file"), ""),
            "error": job.error,
            "attempts": job.attempts,
        }
        for job in failed_jobs
    ]

    recent = [
        {
            "id": run.pk,
            "trigger": run.trigger,
            "state": run.state,
            "startedAt": run.started_at.isoformat(),
            "finishedAt": run.finished_at.isoformat() if run.finished_at else None,
            "seen": run.seen,
            "new": run.new,
            "changed": run.changed,
            "moved": run.moved,
            "missing": run.missing,
            "error": run.error or None,
        }
        for run in ScanRun.objects.order_by("-started_at")[:8]
    ]

    return {
        "status": scan_status(),
        "worker": worker_health(),
        "queue": queue,
        "failed": failed,
        "failedTotal": Job.objects.filter(state=JobState.FAILED).count(),
        "recent": recent,
    }
