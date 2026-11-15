"""Pixel fingerprints (DUP-01, as the owner set it on 2026-11-14: only
pixel-identical copies are duplicates).

A photo's fingerprint is the SHA-256 of its decoded pixels, turned the way it
is shown. Two files with the same fingerprint are the same picture to the last
pixel, whatever their metadata says - so a copy that had a title written into
it still matches its original, while a resized or re-compressed one does not.

A video's fingerprint is the SHA-256 of its encoded streams - ffmpeg's
`streamhash` of each video and audio stream, copied, never decoded. The same
clip with a title or date written into it, or moved from .MOV to .MP4 without
re-encoding, matches; a re-encoded one does not. Cover art and data tracks are
left out: tools add and rewrite them like any other metadata.

RAW and a Live Photo's clip are not opened: their fingerprint is their byte
hash, so only byte-identical copies match. A Live Photo is found through its
still anyway.
"""

from __future__ import annotations

import hashlib
import logging
import subprocess
from pathlib import Path

from django.conf import settings

from jobs.models import Job
from library.models import FileRole, FileState, MediaFile
from library.services.paths import content_type, library_root, to_abs
from library.services.thumbnails import _open_photo

logger = logging.getLogger("gallery")

# Reading a long clip end to end takes a while on a slow disk; the job system
# takes a job that runs for 30 minutes to be hung.
VIDEO_TIMEOUT = 20 * 60


def _kind(media: MediaFile) -> str | None:
    """'photo' or 'video' when the file's content is fingerprinted, else None."""
    if media.role == FileRole.LIVE_VIDEO:
        return None
    kind = content_type(media.rel_path, media.mime)
    return kind if kind in ("photo", "video") else None


def decodes(media: MediaFile) -> bool:
    """Photos are fingerprinted by their pixels and videos by their streams;
    everything else by its bytes."""
    return _kind(media) is not None


def of_pixels(path) -> str:
    image = _open_photo(path)  # decoded in full and turned as shown
    digest = hashlib.sha256(f"{image.mode} {image.width}x{image.height}\n".encode())
    digest.update(image.tobytes())
    return digest.hexdigest()


class FfmpegMissing(Exception):  # noqa: N818 - a condition, named for it
    pass


def of_streams(path: Path) -> str:
    """The hash of a video's encoded video and audio streams. Raises if ffmpeg
    is missing (FfmpegMissing), too slow (TimeoutExpired) or cannot read it."""
    try:
        result = subprocess.run(  # noqa: S603 - fixed argument list, no shell
            [
                settings.FFMPEG_PATH, "-v", "error", "-nostdin", "-i", str(path),
                # V, not v: a video stream that is not cover art.
                "-map", "0:V?", "-map", "0:a?", "-c", "copy",
                "-f", "streamhash", "-hash", "sha256", "-",
            ],
            capture_output=True,
            timeout=VIDEO_TIMEOUT,
            check=False,
        )
    except FileNotFoundError as exc:
        raise FfmpegMissing(settings.FFMPEG_PATH) from exc
    # One "index,type,SHA256=..." line per stream.
    lines = result.stdout.decode("ascii", "replace").split()
    if result.returncode != 0 or not lines:
        message = result.stderr.decode("utf-8", "replace").strip() or "no video or audio stream"
        raise ValueError(f"ffmpeg could not read the streams: {message[:500]}")
    return hashlib.sha256("\n".join(lines).encode()).hexdigest()


def fingerprint(media: MediaFile) -> str | None:
    """The file's fingerprint, or None if it is gone. A file that cannot be
    read still matches by its bytes."""
    kind = _kind(media)
    if kind is None:
        return media.sha256
    path = to_abs(media.rel_path, library_root())
    if not path.is_file():
        return None
    try:
        return of_pixels(path) if kind == "photo" else of_streams(path)
    except FfmpegMissing:
        logger.warning(
            "fingerprint: ffmpeg could not be started (%s); %s matches by its bytes", settings.FFMPEG_PATH, media.rel_path
        )
        return media.sha256
    except Exception as exc:  # noqa: BLE001 - an unreadable file still matches by its bytes
        logger.warning("fingerprint: %s could not be read (%s); matching by its bytes", media.rel_path, exc)
        return media.sha256


def fingerprint_job(job: Job) -> None:
    from duplicates.services.detect import request_dedupe

    media = MediaFile.objects.filter(pk=job.payload["file"], state=FileState.PRESENT).first()
    if media is None or media.pixel_hash:
        return
    value = fingerprint(media)
    if value is None:
        return
    # Byte-identical copies share it.
    MediaFile.objects.filter(sha256=media.sha256, pixel_hash="").update(pixel_hash=value)
    request_dedupe()
