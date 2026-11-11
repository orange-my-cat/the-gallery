"""Grid thumbnails and viewer previews, as WebP in the cache (HLD, "Thumbnail sources").

- `small`: the grid. The shorter side is 400 px - the timeline's rows are about
  210 CSS px tall, so this stays sharp on a 2x display whatever the aspect
  ratio - and the longer side is capped at 1,600 px for panoramas.
- `large`: the viewer, longest side 2,048 px. Every format gets one, because
  browsers show neither HEIC nor RAW.

Both are named by the file's SHA-256, so a moved file keeps its thumbnails and
byte-identical copies share them. Neither is ever upscaled. Each is written to a
temporary name and renamed into place, so a half-written WebP is never served.
"""

from __future__ import annotations

import io
import os
import subprocess
import tempfile
from pathlib import Path

from django.conf import settings
from PIL import Image, ImageOps

from core.services.exceptions import BusinessRuleError
from jobs.models import Job
from jobs.services import enqueue
from library.models import FileRole, FileState, MediaFile
from library.services import metadata
from library.services.paths import content_type, library_root, to_abs

SMALL_SHORT_SIDE = 400
SMALL_LONG_CAP = 1600
LARGE_LONG_SIDE = 2048
SIZES = {"small": 400, "large": 2048}

_registered = False


class CacheUnavailable(BusinessRuleError):
    code = "cache_unavailable"


class ToolUnavailable(BusinessRuleError):
    code = "tool_unavailable"


def _register_heif() -> None:
    global _registered
    if not _registered:
        import pillow_heif

        pillow_heif.register_heif_opener()
        _registered = True


def cache_root() -> Path:
    root = settings.CACHE_ROOT
    if root is None:
        raise CacheUnavailable("CACHE_ROOT is not set, so there is nowhere to keep thumbnails.")
    return Path(root)


def cache_path(sha: str, size: str) -> Path:
    return cache_root() / "thumbs" / sha[:2] / f"{sha}_{SIZES[size]}.webp"


def queue_missing(media: MediaFile, *, scan_id: int | None = None) -> list[str]:
    queued = []
    for size in ("small", "large"):
        if not cache_path(media.sha256, size).exists():
            enqueue(f"thumb_{size}", {"file": media.pk}, scan_id=scan_id)
            queued.append(size)
    return queued


# ---------------------------------------------------------------------------
# Decoding
# ---------------------------------------------------------------------------


def _orientation_of(media: MediaFile) -> int:
    raw = getattr(getattr(media, "metadata", None), "raw", None) or {}
    try:
        return int(raw.get("EXIF:Orientation") or 1)
    except (TypeError, ValueError):
        return 1


def _orient(image: Image.Image, orientation: int) -> Image.Image:
    """Apply an EXIF orientation the image itself does not carry."""
    ops = {
        2: [Image.Transpose.FLIP_LEFT_RIGHT],
        3: [Image.Transpose.ROTATE_180],
        4: [Image.Transpose.FLIP_TOP_BOTTOM],
        5: [Image.Transpose.TRANSPOSE],
        6: [Image.Transpose.ROTATE_270],
        7: [Image.Transpose.TRANSVERSE],
        8: [Image.Transpose.ROTATE_90],
    }.get(orientation, [])
    for op in ops:
        image = image.transpose(op)
    return image


def _open_photo(path: Path) -> Image.Image:
    _register_heif()
    image = Image.open(path)
    image.seek(0)  # the first frame of a GIF or a multi-page TIFF
    image = ImageOps.exif_transpose(image)
    image.load()
    return image


def _open_raw(path: Path, media: MediaFile) -> Image.Image:
    preview = metadata.embedded_preview(path)
    if preview:
        image = Image.open(io.BytesIO(preview))
        image.load()
        # Previews rarely carry their own orientation; the RAW's applies.
        own = image.getexif().get(0x0112)
        return ImageOps.exif_transpose(image) if own and own != 1 else _orient(image, _orientation_of(media))

    import rawpy

    with rawpy.imread(str(path)) as raw:
        # LibRaw applies the orientation itself.
        rgb = raw.postprocess(use_camera_wb=True, half_size=True, output_bps=8)
    return Image.fromarray(rgb)


def _video_duration(path: Path) -> float | None:
    try:
        result = subprocess.run(  # noqa: S603 - fixed argument list, no shell
            [settings.FFPROBE_PATH, "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
    except FileNotFoundError as exc:
        raise ToolUnavailable(
            f"ffprobe could not be started ({settings.FFPROBE_PATH}). Install ffmpeg, or set FFPROBE_PATH in .env."
        ) from exc
    try:
        return float(result.stdout.strip())
    except ValueError:
        return None


def _open_video(path: Path) -> Image.Image:
    duration = _video_duration(path)
    at = max((duration or 0) * 0.1, 0)
    try:
        result = subprocess.run(  # noqa: S603
            [
                settings.FFMPEG_PATH, "-v", "error", "-ss", f"{at:.3f}", "-i", str(path),
                "-frames:v", "1", "-f", "image2pipe", "-vcodec", "png", "-",
            ],
            capture_output=True,
            timeout=120,
            check=False,
        )
    except FileNotFoundError as exc:
        raise ToolUnavailable(
            f"ffmpeg could not be started ({settings.FFMPEG_PATH}). Install it, or set FFMPEG_PATH in .env."
        ) from exc
    if result.returncode != 0 or not result.stdout:
        message = result.stderr.decode("utf-8", "replace").strip() or "no frame"
        raise ValueError(f"ffmpeg could not read a frame: {message[:500]}")
    image = Image.open(io.BytesIO(result.stdout))
    image.load()
    return image


def open_image(path: Path, media: MediaFile) -> Image.Image:
    # By what the file holds, not its name: a clip named .JPG goes to ffmpeg.
    kind = content_type(media.rel_path, media.mime)
    if kind == "raw":
        return _open_raw(path, media)
    if kind == "video":
        return _open_video(path)
    return _open_photo(path)


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------


def target_size(width: int, height: int, size: str) -> tuple[int, int]:
    """Scaled dimensions for a thumbnail, never larger than the source."""
    if size == "small":
        scale = SMALL_SHORT_SIDE / min(width, height)
        scale = min(scale, SMALL_LONG_CAP / max(width, height))
    else:
        scale = LARGE_LONG_SIDE / max(width, height)
    scale = min(scale, 1.0)
    return max(round(width * scale), 1), max(round(height * scale), 1)


def render(image: Image.Image, size: str) -> bytes:
    if image.mode not in ("RGB", "RGBA"):
        has_alpha = image.mode in ("LA", "PA") or (image.mode == "P" and "transparency" in image.info)
        image = image.convert("RGBA" if has_alpha else "RGB")
    width, height = target_size(image.width, image.height, size)
    if (width, height) != image.size:
        image = image.resize((width, height), Image.Resampling.LANCZOS, reducing_gap=3.0)
    out = io.BytesIO()
    image.save(out, "WEBP", quality=80 if size == "small" else 85, method=4)
    return out.getvalue()


def _write_atomic(target: Path, data: bytes) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=target.parent, suffix=".part")
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
        os.replace(tmp, target)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def make_thumbnail(media: MediaFile, size: str) -> Path | None:
    """Render one thumbnail if it is not already in the cache. None if the file is gone."""
    target = cache_path(media.sha256, size)
    if target.exists():
        return target
    path = to_abs(media.rel_path, library_root())
    if not path.is_file():
        return None  # moved or missing since it was indexed; the next scan re-queues it
    image = open_image(path, media)
    _write_atomic(target, render(image, size))
    return target


def _job(job: Job, size: str) -> None:
    media = (
        MediaFile.objects.select_related("metadata")
        .filter(pk=job.payload["file"])
        .exclude(state=FileState.TRASHED)
        .exclude(role=FileRole.LIVE_VIDEO)  # folded into its still since it was queued
        .first()
    )
    if media is not None:
        make_thumbnail(media, size)


def thumb_small_job(job: Job) -> None:
    _job(job, "small")


def thumb_large_job(job: Job) -> None:
    _job(job, "large")
