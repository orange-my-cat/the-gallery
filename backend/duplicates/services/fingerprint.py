"""Pixel fingerprints (DUP-01, as the owner set it on 2026-11-14: only
pixel-identical copies are duplicates).

A photo's fingerprint is the SHA-256 of its decoded pixels, turned the way it
is shown. Two files with the same fingerprint are the same picture to the last
pixel, whatever their metadata says - so a copy that had a title written into
it still matches its original, while a resized or re-compressed one does not.

RAW and video are not decoded (too costly for what it would find): their
fingerprint is their byte hash, so only byte-identical copies match.
"""

from __future__ import annotations

import hashlib
import logging

from jobs.models import Job
from library.models import FileRole, FileState, MediaFile
from library.services.paths import content_type, library_root, to_abs
from library.services.thumbnails import _open_photo

logger = logging.getLogger("gallery")


def decodes(media: MediaFile) -> bool:
    """Photos are fingerprinted by their pixels; everything else by its bytes."""
    return media.role != FileRole.LIVE_VIDEO and content_type(media.rel_path, media.mime) == "photo"


def of_pixels(path) -> str:
    image = _open_photo(path)  # decoded in full and turned as shown
    digest = hashlib.sha256(f"{image.mode} {image.width}x{image.height}\n".encode())
    digest.update(image.tobytes())
    return digest.hexdigest()


def fingerprint(media: MediaFile) -> str | None:
    """The file's fingerprint, or None if it is gone or cannot be decoded."""
    if not decodes(media):
        return media.sha256
    path = to_abs(media.rel_path, library_root())
    if not path.is_file():
        return None
    try:
        return of_pixels(path)
    except Exception as exc:  # noqa: BLE001 - an undecodable photo still matches by its bytes
        logger.warning("fingerprint: %s could not be decoded (%s); matching by its bytes", media.rel_path, exc)
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
