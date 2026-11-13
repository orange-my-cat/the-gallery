"""Writing edits into files (MET-02): write, verify, then replace.

- Formats with a documented XMP home - JPEG, HEIC, TIFF, PNG and DNG - get it
  embedded, unless the write-back setting says sidecars only.
- Everything else (camera RAW, video, GIF, WebP) gets an XMP sidecar beside it.
  Named Lightroom's way, `IMG_1234.xmp` (owner's choice, 2026-11-08), which
  most cataloguers read - except when another file in the folder shares the
  stem, when the unambiguous `IMG_1234.CR3.xmp` is used. An existing sidecar
  of either name is always the one updated.

Every write goes to a temporary copy in the same folder; ExifTool writes it,
the copy is read back and checked, and only then does `os.replace` swap it in.
A failure at any point leaves the original exactly as it was.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Any

from core.models import AppSettings, WriteBackMode
from core.services.exceptions import BusinessRuleError
from library.models import FileState, MediaFile
from library.services import metadata
from library.services.paths import TMP_MARK, extension

EMBEDDED_EXTENSIONS = frozenset({"jpg", "jpeg", "heic", "heif", "tif", "tiff", "png", "dng"})

# What each editable field is written as, and the family-0 key it reads back as.
FIELD_TAGS: dict[str, list[tuple[str, str]]] = {
    "title": [("XMP-dc:Title", "XMP:Title")],
    "description": [("XMP-dc:Description", "XMP:Description")],
    "rating": [("XMP-xmp:Rating", "XMP:Rating")],
    "keywords": [("XMP-dc:Subject", "XMP:Subject")],
    "place": [("XMP-iptcCore:Location", "XMP:Location")],
    "date": [("XMP-exif:DateTimeOriginal", "XMP:DateTimeOriginal")],
    # Filled in by a duplicate merge (DUP-05), from the copy that had them.
    "camera_make": [("XMP-tiff:Make", "XMP:Make")],
    "camera_model": [("XMP-tiff:Model", "XMP:Model")],
    "lens": [("XMP-exifEX:LensModel", "XMP:LensModel")],
}
# GPS as signed decimal degrees in XMP; EXIF keeps a hemisphere Ref instead.
GPS_TAGS = [
    ("XMP-exif:GPSLatitude", "XMP:GPSLatitude"),
    ("XMP-exif:GPSLongitude", "XMP:GPSLongitude"),
]
# Embedded files also carry EXIF's own date, which readers prefer over XMP's.
EMBEDDED_DATE_TAGS = [
    ("EXIF:DateTimeOriginal", "EXIF:DateTimeOriginal"),
    ("EXIF:OffsetTimeOriginal", "EXIF:OffsetTimeOriginal"),
]
# Likewise for the camera and position: readers take EXIF's over XMP's.
EMBEDDED_TAGS = {
    "camera_make": ("EXIF:Make", "EXIF:Make"),
    "camera_model": ("EXIF:Model", "EXIF:Model"),
    "lens": ("EXIF:LensModel", "EXIF:LensModel"),
}
EMBEDDED_GPS_TAGS = [
    ("EXIF:GPSLatitude", "EXIF:GPSLatitude"),
    ("EXIF:GPSLatitudeRef", "EXIF:GPSLatitudeRef"),
    ("EXIF:GPSLongitude", "EXIF:GPSLongitude"),
    ("EXIF:GPSLongitudeRef", "EXIF:GPSLongitudeRef"),
]

_EMPTY_XMP = (
    "<?xpacket begin='﻿' id='W5M0MpCehiHzreSzNTczkc9d'?>\n"
    "<x:xmpmeta xmlns:x='adobe:ns:meta/'>\n"
    " <rdf:RDF xmlns:rdf='http://www.w3.org/1999/02/22-rdf-syntax-ns#'/>\n"
    "</x:xmpmeta>\n"
    "<?xpacket end='w'?>\n"
)


class WriteFailed(BusinessRuleError):
    code = "write_failed"


# ---------------------------------------------------------------------------
# Where a file's metadata goes
# ---------------------------------------------------------------------------


def writes_embedded(rel_path: str) -> bool:
    if AppSettings.load().write_back_mode == WriteBackMode.SIDECAR:
        return False
    return extension(rel_path) in EMBEDDED_EXTENSIONS


def sidecar_candidates(rel_path: str) -> tuple[str, str]:
    """(`IMG_1234.xmp`, `IMG_1234.CR3.xmp`) as paths relative to the root."""
    folder, _, name = rel_path.rpartition("/")
    stem = name.rsplit(".", 1)[0] if "." in name else name
    prefix = f"{folder}/" if folder else ""
    return f"{prefix}{stem}.xmp", f"{prefix}{name}.xmp"


def sidecar_for(media: MediaFile, root: Path) -> str:
    """The sidecar this file's metadata lives in, relative to the root."""
    short, long = sidecar_candidates(media.rel_path)
    if (root / long).exists():
        return long
    folder = media.rel_path.rpartition("/")[0]
    stem = short.rpartition("/")[2][: -len(".xmp")].lower()
    # Another file - not this asset's - with the same stem would share it.
    shared = (
        MediaFile.objects.filter(rel_path__startswith=f"{folder}/" if folder else "")
        .exclude(asset_id=media.asset_id)
        .exclude(state=FileState.TRASHED)
        .values_list("rel_path", flat=True)
    )
    for other in shared:
        o_folder, _, o_name = other.rpartition("/")
        if o_folder == folder and (o_name.rsplit(".", 1)[0] if "." in o_name else o_name).lower() == stem:
            return long
    return short


def target_for(media: MediaFile, root: Path) -> tuple[str, bool]:
    """(relative path to write, whether it is a sidecar)."""
    if writes_embedded(media.rel_path):
        return media.rel_path, False
    return sidecar_for(media, root), True


# ---------------------------------------------------------------------------
# Values
# ---------------------------------------------------------------------------


def assignments(values: dict[str, Any], *, embedded: bool) -> dict[str, Any]:
    """Field values -> ExifTool tag assignments. None removes a tag."""
    out: dict[str, Any] = {}
    for field, value in values.items():
        if field == "date":
            local, offset = (value or {}).get("local"), (value or {}).get("offset")
            stamp = local.replace("-", ":", 2).replace("T", " ") if local else None
            out["XMP-exif:DateTimeOriginal"] = f"{stamp}{offset or ''}" if stamp else None
            if embedded:
                out["EXIF:DateTimeOriginal"] = stamp
                out["EXIF:OffsetTimeOriginal"] = offset if stamp else None
            continue
        if field == "gps":
            lat, lon = value if value else (None, None)
            known = lat is not None and lon is not None
            out["XMP-exif:GPSLatitude"] = float(lat) if known else None
            out["XMP-exif:GPSLongitude"] = float(lon) if known else None
            if embedded:
                out["EXIF:GPSLatitude"] = abs(float(lat)) if known else None
                out["EXIF:GPSLatitudeRef"] = ("S" if lat < 0 else "N") if known else None
                out["EXIF:GPSLongitude"] = abs(float(lon)) if known else None
                out["EXIF:GPSLongitudeRef"] = ("W" if lon < 0 else "E") if known else None
            continue
        if embedded and field in EMBEDDED_TAGS:
            out[EMBEDDED_TAGS[field][0]] = (value or "").strip() or None
        for write_tag, _ in FIELD_TAGS[field]:
            if field == "keywords":
                out[write_tag] = list(value or []) or None
            elif field == "rating":
                out[write_tag] = int(value) if value else None
            else:
                out[write_tag] = (value or "").strip() or None
    return out


def _read_key(write_tag: str) -> str:
    for pairs in (*FIELD_TAGS.values(), EMBEDDED_DATE_TAGS, GPS_TAGS, EMBEDDED_GPS_TAGS, list(EMBEDDED_TAGS.values())):
        for w, r in pairs:
            if w == write_tag:
                return r
    raise KeyError(write_tag)


def current(path: Path, tags: list[str]) -> dict[str, Any]:
    """What the file holds now for each tag, None where it has none."""
    if not path.exists():
        return {tag: None for tag in tags}
    raw = metadata.read(path)
    return {tag: _normal(raw.get(_read_key(tag))) for tag in tags}


def _normal(value: Any) -> Any:
    if value in ("", [], None):
        return None
    return value


def _same(expected: Any, got: Any) -> bool:
    if expected is None:
        return _normal(got) is None
    if isinstance(expected, list):
        got_list = got if isinstance(got, list) else [got] if got not in (None, "") else []
        return [str(x) for x in got_list] == [str(x) for x in expected]
    if isinstance(expected, float):
        try:
            return abs(float(got) - expected) < 1e-5  # about a metre
        except (TypeError, ValueError):
            return False
    if isinstance(expected, int):
        try:
            return int(float(got)) == expected
        except (TypeError, ValueError):
            return False
    return str(got).strip() == str(expected).strip()


# ---------------------------------------------------------------------------
# Writing
# ---------------------------------------------------------------------------


def _args(assign: dict[str, Any]) -> list[str]:
    args: list[str] = ["-overwrite_original", "-P"]
    for tag, value in assign.items():
        if value is None:
            args.append(f"-{tag}=")
        elif isinstance(value, list):
            # Repeated assignments to a list tag replace the whole list.
            args.extend(f"-{tag}={item}" for item in value)
        else:
            args.append(f"-{tag}={value}")
    return args


def write(path: Path, assign: dict[str, Any], *, sidecar: bool) -> None:
    """Write `assign` into `path` (creating a sidecar if need be), verified."""
    if not assign:
        return
    if sidecar and not path.exists():
        path.write_text(_EMPTY_XMP, encoding="utf-8")
    tmp = path.with_name(f".{path.stem}{TMP_MARK}{path.suffix}")
    shutil.copy2(path, tmp)
    try:
        metadata.write_tags(tmp, _args(assign))
        readback = metadata.read(tmp)
        wrong = [tag for tag, value in assign.items() if not _same(value, readback.get(_read_key(tag)))]
        if wrong:
            raise WriteFailed(f"{path.name}: {', '.join(wrong)} did not read back as written.")
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)
