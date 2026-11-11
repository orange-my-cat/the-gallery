"""Where the library is, which files belong to it, and how paths are spelled.

Every path the database holds is relative to LIBRARY_ROOT with forward slashes.
Every absolute path the application touches is made here, by `to_abs`, which
refuses anything that would resolve outside the root.
"""

from __future__ import annotations

from pathlib import Path, PurePosixPath

from django.conf import settings

from core.models import AppSettings
from core.services.exceptions import BusinessRuleError

# LIB-02.
PHOTO_EXTENSIONS = frozenset({"jpg", "jpeg", "png", "heic", "heif", "webp", "tif", "tiff", "gif"})
RAW_EXTENSIONS = frozenset({"cr2", "cr3", "nef", "arw", "raf", "orf", "rw2", "dng"})
VIDEO_EXTENSIONS = frozenset({"mp4", "mov", "m4v", "mkv", "avi", "3gp"})
# The half of a RAW+JPEG pair that the asset displays (LIB-05).
PAIRABLE_JPEG_EXTENSIONS = frozenset({"jpg", "jpeg"})

# Inside the library so a trash move is an instant rename (HLD). Never scanned.
TRASH_DIR = ".gallery-trash"
# In the name of the temporary copy a metadata write works on (fileops.xmp).
# Never scanned: it exists for a moment, and only while being verified.
TMP_MARK = ".gallery-tmp"


class LibraryUnavailable(BusinessRuleError):
    code = "library_unavailable"


class LibraryNotChosen(LibraryUnavailable):
    code = "library_not_chosen"


class UnsafePath(BusinessRuleError):
    code = "unsafe_path"


def must_choose_folder() -> bool:
    """True where LIBRARY_ROOT holds whole drives and the owner picks the library in Settings."""
    return bool(getattr(settings, "LIBRARY_DRIVES", []))


def extension(rel_path: str) -> str:
    name = rel_path.rsplit("/", 1)[-1]
    return name.rsplit(".", 1)[-1].lower() if "." in name else ""


def file_type(rel_path: str) -> str | None:
    """'photo', 'raw', 'video', or None for a file the library ignores."""
    ext = extension(rel_path)
    if ext in PHOTO_EXTENSIONS:
        return "photo"
    if ext in RAW_EXTENSIONS:
        return "raw"
    if ext in VIDEO_EXTENSIONS:
        return "video"
    return None


def content_type(rel_path: str, mime: str) -> str | None:
    """`file_type`, corrected by what the file actually holds.

    The extension decides whether the library looks at a file at all; what it
    is comes from ExifTool's MIMEType, which reads the bytes. They disagree
    more than one would hope: tools that rename Live Photo pairs have been seen
    to swap the halves' extensions, leaving the still in `IMG_1234.MOV` and the
    clip in `IMG_1234.JPG`. Trusting the name, the clip fails every thumbnail
    as a photo and the still is offered as a video that will not play.
    A RAW stays a RAW: its MIME type is an image one, and it needs LibRaw.
    """
    kind = file_type(rel_path)
    if kind in (None, "raw"):
        return kind
    if mime.startswith("video/"):
        return "video"
    if mime.startswith("image/"):
        return "photo"
    return kind


def pair_key(rel_path: str) -> str:
    """Folder and stem, lower-cased: `2024/IMG_0001.CR3` and `2024/img_0001.jpg` match."""
    folder, _, name = rel_path.rpartition("/")
    stem = name.rsplit(".", 1)[0] if "." in name else name
    return f"{folder}/{stem}".lower()


def library_root() -> Path:
    root = settings.LIBRARY_ROOT
    if root is None:
        raise LibraryUnavailable("LIBRARY_ROOT is not set in .env, so there is no library to scan.")
    root = Path(root)
    if not root.is_dir():
        raise LibraryUnavailable(f"The library folder {root} does not exist or cannot be read.")
    return root


def normalise_rel(value: str) -> str:
    """Forward slashes, no leading or trailing slash, no `.` segments."""
    parts = [p for p in value.replace("\\", "/").split("/") if p not in ("", ".")]
    return "/".join(parts)


def to_rel(root: Path, absolute: Path | str) -> str:
    return PurePosixPath(Path(absolute).relative_to(root).as_posix()).as_posix()


def to_abs(rel_path: str, root: Path | None = None) -> Path:
    """The absolute path for a stored relative path, never outside the root."""
    root = (root or library_root()).resolve()
    if "\x00" in rel_path:
        raise UnsafePath("A path may not contain a NUL character.")
    rel = normalise_rel(rel_path)
    candidate = (root / rel).resolve()
    if candidate != root and not candidate.is_relative_to(root):
        raise UnsafePath(f"{rel_path!r} is outside the library.")
    return candidate


def scan_root(root: Path | None = None) -> Path:
    """The folder the scan walks: the root, or the folder chosen in settings."""
    root = root or library_root()
    sub = normalise_rel(AppSettings.load().library_subfolder)
    if not sub and must_choose_folder():
        raise LibraryNotChosen("Choose your library folder in Settings.")
    folder = to_abs(sub, root) if sub else root
    if not folder.is_dir():
        raise LibraryUnavailable(f"The library folder {folder} chosen in settings does not exist.")
    return folder


def is_excluded(rel_dir: str, exclusions: list[str]) -> bool:
    """True for a trash folder and for an excluded folder or anything inside one.

    `rel_dir` and the exclusions are relative to the library folder. Compared
    case-insensitively: the library lives on Windows, where `Exports` and
    `exports` are the same folder.
    """
    rel = rel_dir.lower()
    if TRASH_DIR in rel.split("/"):
        return True
    for raw in exclusions:
        ex = normalise_rel(raw).lower()
        if ex and (rel == ex or rel.startswith(ex + "/")):
            return True
    return False
