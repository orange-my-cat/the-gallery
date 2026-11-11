"""Reading metadata with ExifTool, and normalising it onto the Asset (MET-01).

ExifTool runs as one long-lived process per worker (`-stay_open`, through
PyExifTool), so a 100,000-file scan does not start 100,000 Perl interpreters.
Tags come back grouped (`-G`, e.g. `EXIF:DateTimeOriginal`) and numeric (`-n`:
GPS as signed decimals, exposure as seconds).

`normalise` is a pure function of that output, so the rules for which tag wins
are tested without ExifTool installed.
"""

from __future__ import annotations

import atexit
import re
import threading
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from django.conf import settings

from core.services.exceptions import BusinessRuleError
from library.services.folders import folder_date


class ExifToolUnavailable(BusinessRuleError):
    code = "exiftool_unavailable"


# ---------------------------------------------------------------------------
# The ExifTool process
# ---------------------------------------------------------------------------

_lock = threading.Lock()
_helper: Any = None


def _exiftool():
    global _helper
    with _lock:
        if _helper is not None and _helper.running:
            return _helper
        import exiftool

        try:
            # PyExifTool looks the executable up as soon as it is constructed,
            # so a missing ExifTool surfaces here, not at run().
            helper = exiftool.ExifToolHelper(
                executable=settings.EXIFTOOL_PATH,
                # -charset filename=utf8: without it ExifTool on Windows cannot
                # open a file whose name is not in the ANSI code page.
                common_args=["-G", "-n", "-charset", "filename=utf8"],
                encoding="utf-8",
                auto_start=False,
            )
            helper.run()
        except (FileNotFoundError, OSError) as exc:
            raise ExifToolUnavailable(
                f"ExifTool could not be started ({settings.EXIFTOOL_PATH}). Install it, or set "
                "EXIFTOOL_PATH in .env to the full path of exiftool.exe."
            ) from exc
        _helper = helper
        return helper


@atexit.register
def _shutdown() -> None:  # pragma: no cover - process exit
    if _helper is not None and _helper.running:
        _helper.terminate()


def read(path: Path) -> dict[str, Any]:
    """Every tag ExifTool can read from the file, as one flat dict."""
    result = _exiftool().get_metadata(str(path))
    return dict(result[0]) if result else {}


def write_tags(path: Path, args: list[str]) -> None:
    """Run an ExifTool write. Only fileops.services.xmp calls this, on a temporary copy."""
    from exiftool.exceptions import ExifToolExecuteError

    try:
        _exiftool().execute(*args, str(path))
    except ExifToolExecuteError as exc:
        detail = (exc.stderr or exc.stdout or str(exc)).strip()
        raise BusinessRuleError(f"ExifTool could not write {path.name}: {detail[:300]}", code="write_failed") from exc


def read_sidecar(path: Path) -> dict[str, Any]:
    """The XMP tags of a sidecar, keyed `Sidecar:<Tag>` so they outrank the
    file's own (MET-02: what the owner wrote wins over what the camera did)."""
    raw = read(path)
    return {f"Sidecar:{key.split(':', 1)[1]}": value for key, value in raw.items() if key.startswith("XMP:")}


def embedded_preview(path: Path) -> bytes | None:
    """The largest JPEG a RAW file carries inside it, or None."""
    helper = _exiftool()
    for tag in ("-JpgFromRaw", "-PreviewImage", "-OtherImage"):
        data = helper.execute("-b", tag, str(path), raw_bytes=True)
        if isinstance(data, bytes) and data[:2] == b"\xff\xd8":
            return data
    return None


# ---------------------------------------------------------------------------
# Normalising
# ---------------------------------------------------------------------------

_DATETIME = re.compile(
    r"^(\d{4})[:-](\d{2})[:-](\d{2})[ T](\d{2}):(\d{2})(?::(\d{2}))?(\.\d+)?\s*(Z|[+-]\d{2}:?\d{2})?"
)
_OFFSET = re.compile(r"^([+-])(\d{2}):?(\d{2})$")

# Cameras that stamp their own name into the description field.
_JUNK_DESCRIPTIONS = frozenset({"", "olympus digital camera", "sony dsc", "default", "dcim"})

# (tag, the offset tag that goes with it). The first that parses wins.
_LOCAL_DATE_TAGS: tuple[tuple[str, str | None], ...] = (
    # Written by the owner through The Gallery, into a sidecar (MET-02).
    ("Sidecar:DateTimeOriginal", None),
    ("Composite:SubSecDateTimeOriginal", None),
    ("EXIF:DateTimeOriginal", "EXIF:OffsetTimeOriginal"),
    ("XMP:DateTimeOriginal", None),
    # Apple's Keys:CreationDate, which states its offset.
    ("QuickTime:CreationDate", None),
    ("EXIF:CreateDate", "EXIF:OffsetTimeDigitized"),
    ("XMP:CreateDate", None),
    ("RIFF:DateTimeOriginal", None),
)
# QuickTime's own CreateDate is UTC by specification, when a camera follows it.
_UTC_DATE_TAGS = ("QuickTime:CreateDate", "QuickTime:MediaCreateDate", "QuickTime:TrackCreateDate")


@dataclass(frozen=True)
class Normalised:
    capture_at: datetime
    capture_offset: int | None
    # 'metadata', 'file_name' or 'file_time' - library.models.CaptureSource.
    capture_source: str
    lat: float | None = None
    lon: float | None = None
    width: int | None = None
    height: int | None = None
    mime: str = ""
    camera_make: str = ""
    camera_model: str = ""
    lens: str = ""
    exposure_time: float | None = None
    f_number: float | None = None
    iso: int | None = None
    focal_length: float | None = None
    duration: float | None = None
    title: str = ""
    description: str = ""
    rating: int = 0
    # The file's keywords, which are the asset's tags (ORG-02, MET-02).
    keywords: tuple[str, ...] = ()
    # A location name written by the owner (ORG-04).
    place: str = ""
    # Apple's Live Photo identifier: one UUID, written into the still and its clip.
    live_id: str = ""


def _present(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return value.strip() != ""
    return not (isinstance(value, list) and not value)


def _first(raw: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        value = raw.get(key)
        if isinstance(value, list):
            value = next((v for v in value if _present(v)), None)
        if _present(value):
            return value
    return None


def _text(raw: dict[str, Any], *keys: str) -> str:
    value = _first(raw, *keys)
    return str(value).strip() if value is not None else ""


def _float(raw: dict[str, Any], *keys: str) -> float | None:
    for key in keys:
        value = raw.get(key)
        try:
            number = float(value)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            continue
        if number == number and number not in (float("inf"), float("-inf")):  # not NaN or infinite
            return number
    return None


def parse_offset(value: Any) -> int | None:
    """'+08:00' -> 480. None for anything else."""
    if not isinstance(value, str):
        return None
    if value.strip() == "Z":
        return 0
    match = _OFFSET.match(value.strip())
    if not match:
        return None
    sign = -1 if match.group(1) == "-" else 1
    minutes = sign * (int(match.group(2)) * 60 + int(match.group(3)))
    return minutes if -14 * 60 <= minutes <= 14 * 60 else None


def parse_datetime(value: Any) -> tuple[datetime, int | None] | None:
    """An ExifTool date as (naive wall-clock time, offset minutes or None).

    Rejects the zero dates cameras write when their clock was never set.
    """
    if not isinstance(value, str):
        return None
    match = _DATETIME.match(value.strip())
    if not match:
        return None
    year, month, day, hour, minute = (int(match.group(i)) for i in range(1, 6))
    second = int(match.group(6) or 0)
    if year < 1900 or (year, month, day) in ((1904, 1, 1), (1970, 1, 1)):
        return None
    fraction = match.group(7)
    micro = int(round(float(fraction) * 1_000_000)) if fraction else 0
    try:
        naive = datetime(year, month, day, hour, minute, min(second, 59), min(micro, 999_999))
    except ValueError:
        return None
    return naive, parse_offset(match.group(8)) if match.group(8) else None


# Dates phones and apps write into file names when the file itself has none
# (WhatsApp and Facebook strip EXIF). Four-digit years only: `180221` could be
# YYMMDD or DDMMYY, and a wrong guess is worse than the file time.
_NAME_DATETIME = re.compile(
    r"(?<!\d)((?:19|20)\d{2})[-_.]?(0[1-9]|1[0-2])[-_.]?(0[1-9]|[12]\d|3[01])[ _T-]?"
    # Pixel phones glue milliseconds on: PXL_20250302_101500123.jpg.
    r"([01]\d|2[0-3])[-_.:]?([0-5]\d)[-_.:]?([0-5]\d)(?:\d{1,3})?(?!\d)"
)
_NAME_DATE = re.compile(r"(?<!\d)((?:19|20)\d{2})[-_.]?(0[1-9]|1[0-2])[-_.]?(0[1-9]|[12]\d|3[01])(?!\d)")


def name_datetime(name: str) -> datetime | None:
    """A capture time spelled in a file name: `IMG_20170608_084734_01.jpg`,
    `Screenshot_2019-02-03-10-11-12.png`, `IMG-20170608-WA0001.jpg` (noon)."""
    stem = name.rsplit("/", 1)[-1]
    for pattern, has_time in ((_NAME_DATETIME, True), (_NAME_DATE, False)):
        match = pattern.search(stem)
        if not match:
            continue
        parts = [int(g) for g in match.groups()]
        try:
            # Date-only names take noon, so no time zone can move them a day.
            return datetime(*parts) if has_time else datetime(*parts, 12, 0, 0)  # type: ignore[misc]
        except ValueError:
            continue
    return None


def capture_time(
    raw: dict[str, Any], file_mtime: datetime, zone: str, name: str = ""
) -> tuple[datetime, int | None, str]:
    """(instant, offset minutes or None, source: 'metadata', 'file_name', 'folder' or 'file_time')."""
    for tag, offset_tag in _LOCAL_DATE_TAGS:
        parsed = parse_datetime(raw.get(tag))
        if parsed is None:
            continue
        naive, offset = parsed
        if offset is None and offset_tag:
            offset = parse_offset(raw.get(offset_tag))
        if offset is not None:
            return naive.replace(tzinfo=timezone(timedelta(minutes=offset))), offset, "metadata"
        return naive.replace(tzinfo=ZoneInfo(zone)), None, "metadata"

    for tag in _UTC_DATE_TAGS:
        parsed = parse_datetime(raw.get(tag))
        if parsed is not None:
            naive, offset = parsed
            if offset is not None:
                return naive.replace(tzinfo=timezone(timedelta(minutes=offset))), offset, "metadata"
            return naive.replace(tzinfo=UTC), None, "metadata"

    from_name = name_datetime(name) if name else None
    if from_name is not None:
        return from_name.replace(tzinfo=ZoneInfo(zone)), None, "file_name"

    # The owner's folder convention: a lower bound or a period, never the
    # photo's own date - shown as approximate (library/services/folders.py).
    # Still far closer than the modified time, which is usually a copy date.
    found = folder_date(name) if name else None
    if found is not None:
        start = datetime(found.start.year, found.start.month, found.start.day)
        return start.replace(tzinfo=ZoneInfo(zone)), None, "folder"

    return file_mtime, None, "file_time"


def _gps(raw: dict[str, Any]) -> tuple[float | None, float | None]:
    lat = _float(raw, "Composite:GPSLatitude", "XMP:GPSLatitude", "EXIF:GPSLatitude")
    lon = _float(raw, "Composite:GPSLongitude", "XMP:GPSLongitude", "EXIF:GPSLongitude")
    if lat is None or lon is None:
        return None, None
    # Only the bare EXIF value is unsigned; its hemisphere is in the Ref tag.
    if raw.get("Composite:GPSLatitude") is None and raw.get("XMP:GPSLatitude") is None:
        if str(raw.get("EXIF:GPSLatitudeRef", "")).upper().startswith("S"):
            lat = -abs(lat)
        if str(raw.get("EXIF:GPSLongitudeRef", "")).upper().startswith("W"):
            lon = -abs(lon)
    if not (-90 <= lat <= 90 and -180 <= lon <= 180) or (lat == 0 and lon == 0):
        return None, None
    return lat, lon


def _dimensions(raw: dict[str, Any]) -> tuple[int | None, int | None]:
    width = height = None
    size = raw.get("Composite:ImageSize")
    if isinstance(size, str):
        parts = re.split(r"[x\s]+", size.strip())
        if len(parts) == 2 and all(p.isdigit() for p in parts):
            width, height = int(parts[0]), int(parts[1])
    if width is None:
        for w_key, h_key in (
            ("File:ImageWidth", "File:ImageHeight"),
            ("EXIF:ExifImageWidth", "EXIF:ExifImageHeight"),
            ("QuickTime:ImageWidth", "QuickTime:ImageHeight"),
            ("PNG:ImageWidth", "PNG:ImageHeight"),
        ):
            w, h = _float(raw, w_key), _float(raw, h_key)
            if w and h:
                width, height = int(w), int(h)
                break
    if width is None or height is None:
        return None, None
    # Shown as the viewer will show it: orientations 5-8 and 90/270 rotations
    # turn the frame on its side.
    orientation = _float(raw, "EXIF:Orientation", "XMP:Orientation")
    rotation = _float(raw, "Composite:Rotation", "QuickTime:Rotation")
    if (orientation is not None and 5 <= orientation <= 8) or (rotation is not None and int(rotation) % 180 == 90):
        width, height = height, width
    return width, height


def _lens(raw: dict[str, Any]) -> str:
    lens = _text(raw, "EXIF:LensModel", "XMP:LensModel", "XMP:Lens", "MakerNotes:LensModel", "MakerNotes:LensType")
    if lens:
        return lens
    # With -n, Composite:LensID is the maker's numeric code - useless as text.
    lens_id = _text(raw, "Composite:LensID")
    return "" if lens_id.replace(".", "").isdigit() else lens_id


def normalise(raw: dict[str, Any], *, file_mtime: datetime, zone: str | None = None, name: str = "") -> Normalised:
    capture_at, offset, source = capture_time(raw, file_mtime, zone or settings.TIME_ZONE, name)
    lat, lon = _gps(raw)
    width, height = _dimensions(raw)

    description = _text(raw, "Sidecar:Description", "XMP:Description", "IPTC:Caption-Abstract", "EXIF:ImageDescription")
    if description.lower() in _JUNK_DESCRIPTIONS:
        description = ""

    rating_value = _float(raw, "Sidecar:Rating", "XMP:Rating", "EXIF:Rating")
    rating = int(rating_value) if rating_value is not None and 0 < rating_value <= 5 else 0

    iso = _float(raw, "EXIF:ISO", "Composite:ISO", "MakerNotes:ISO")
    duration = _float(raw, "QuickTime:Duration", "Composite:Duration", "Matroska:Duration", "RIFF:Duration")

    return Normalised(
        capture_at=capture_at,
        capture_offset=offset,
        capture_source=source,
        lat=lat,
        lon=lon,
        width=width,
        height=height,
        mime=_text(raw, "File:MIMEType"),
        camera_make=_text(raw, "EXIF:Make", "QuickTime:Make", "XMP:Make")[:128],
        camera_model=_text(raw, "EXIF:Model", "QuickTime:Model", "XMP:Model")[:128],
        lens=_lens(raw)[:255],
        exposure_time=_float(raw, "EXIF:ExposureTime", "Composite:ShutterSpeed"),
        f_number=_float(raw, "EXIF:FNumber", "Composite:Aperture"),
        iso=int(iso) if iso is not None and iso > 0 else None,
        focal_length=_float(raw, "EXIF:FocalLength"),
        duration=duration if duration is not None and duration > 0 else None,
        title=_text(raw, "Sidecar:Title", "XMP:Title", "IPTC:ObjectName", "QuickTime:Title")[:500],
        description=description,
        rating=rating,
        keywords=_keywords(raw),
        place=_text(raw, "Sidecar:Location", "XMP:Location")[:255],
        live_id=_live_id(raw),
    )


# Where the Live Photo identifier lives. The clip carries it as QuickTime's
# ContentIdentifier; the still in Apple's maker notes, which ExifTool 12.x
# names MediaGroupUUID and some other builds ContentIdentifier.
LIVE_ID_TAGS = (
    "QuickTime:ContentIdentifier",
    "Keys:ContentIdentifier",
    "MakerNotes:MediaGroupUUID",
    "MakerNotes:ContentIdentifier",
)


def _live_id(raw: dict[str, Any]) -> str:
    return _text(raw, *LIVE_ID_TAGS).upper()[:64]


def _keywords(raw: dict[str, Any]) -> tuple[str, ...]:
    """The first source that has any: sidecar, then XMP, then IPTC. Case-
    insensitively de-duplicated, in the file's order."""
    for key in ("Sidecar:Subject", "XMP:Subject", "IPTC:Keywords"):
        value = raw.get(key)
        items = value if isinstance(value, list) else [value] if value not in (None, "") else []
        names: list[str] = []
        for item in items:
            name = str(item).strip()[:100]
            if name and name.lower() not in {n.lower() for n in names}:
                names.append(name)
        if names:
            return tuple(names)
    return ()
