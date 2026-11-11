"""`/media/...` - thumbnails, previews and originals (HLD, "Media serving").

Django decides whether a file may be served; something else may send it:

- `MEDIA_DELIVERY=accel` (production): an empty response carrying
  `X-Accel-Redirect`, and Nginx streams the bytes from its read-only mount with
  Range support - so video seeks, and no Gunicorn worker is held by a download.
- `MEDIA_DELIVERY=django` (development): Django streams the file itself, and
  answers single byte-range requests (206) so video can seek. The HLD had
  accepted no seeking in development; in practice a browser that asks for the
  middle of a video and gets the start restarts playback, which is unusable.

Plain Django views rather than DRF: the answer is bytes, not JSON. Each checks
the session itself, since DRF's default permission does not reach them.
"""

from __future__ import annotations

import mimetypes
import re
from pathlib import Path
from urllib.parse import quote

from django.conf import settings
from django.http import FileResponse, Http404, HttpResponse, StreamingHttpResponse
from django.utils.http import content_disposition_header
from django.views.decorators.http import require_GET

from library.models import FileState, MediaFile
from library.services.paths import extension, library_root, to_abs
from library.services.thumbnails import cache_path, cache_root

IMMUTABLE = "private, max-age=31536000, immutable"
# Formats a browser shows inline; anything else is offered as a download.
INLINE_EXTENSIONS = frozenset({"jpg", "jpeg", "png", "webp", "gif", "mp4", "m4v", "mov"})


def _forbidden() -> HttpResponse:
    return HttpResponse(status=403)


def _not_found() -> HttpResponse:
    # Never cached: a thumbnail missing now is usually one still being made.
    response = HttpResponse(status=404)
    response["Cache-Control"] = "no-store"
    return response


def _file(file_id: int, *, trashed_ok: bool = False) -> MediaFile:
    qs = MediaFile.objects.filter(pk=file_id)
    if not trashed_ok:
        qs = qs.exclude(state=FileState.TRASHED)
    media = qs.first()
    if media is None:
        raise Http404
    return media


_RANGE = re.compile(r"^bytes=(\d*)-(\d*)$")
_CHUNK = 256 * 1024


def _read_range(path: Path, start: int, length: int):
    with path.open("rb") as handle:
        handle.seek(start)
        while length > 0:
            chunk = handle.read(min(_CHUNK, length))
            if not chunk:
                return
            length -= len(chunk)
            yield chunk


def _stream(request, path: Path, content_type: str):
    """The file, or the one byte range the request asks for (RFC 9110, 14.2).

    Multi-range requests are answered with the whole file, which the RFC
    allows; browsers do not send them for media.
    """
    size = path.stat().st_size
    match = _RANGE.match(request.headers.get("Range", "").strip())
    if not match or not (match.group(1) or match.group(2)):
        response = FileResponse(path.open("rb"), content_type=content_type)
        response["Accept-Ranges"] = "bytes"
        return response

    if match.group(1):
        start = int(match.group(1))
        end = min(int(match.group(2)) if match.group(2) else size - 1, size - 1)
    else:  # a suffix: the last N bytes
        start, end = max(size - int(match.group(2)), 0), size - 1
    if start >= size or start > end:
        response = HttpResponse(status=416)
        response["Content-Range"] = f"bytes */{size}"
        return response

    length = end - start + 1
    response = StreamingHttpResponse(_read_range(path, start, length), status=206, content_type=content_type)
    response["Content-Length"] = str(length)
    response["Content-Range"] = f"bytes {start}-{end}/{size}"
    response["Accept-Ranges"] = "bytes"
    return response


def _send(request, path: Path, *, internal: str, content_type: str, cache: str, attachment_name: str | None = None):
    if settings.MEDIA_DELIVERY == "accel":
        response = HttpResponse(content_type=content_type)
        # Percent-encoded so spaces, '#' and non-ASCII names survive the trip
        # through Nginx (HLD: "verify early" - see docker/nginx/gallery.conf).
        # Nginx handles Range itself.
        response["X-Accel-Redirect"] = quote(internal, safe="/")
    else:
        response = _stream(request, path, content_type)
    response["Cache-Control"] = cache
    response["X-Content-Type-Options"] = "nosniff"
    if attachment_name is not None:
        response["Content-Disposition"] = content_disposition_header(True, attachment_name)
    return response


def _cached(request, file_id: int, size: str):
    if not request.user.is_authenticated:
        return _forbidden()
    # The Trash view shows what is in it; originals stay out of reach there.
    media = _file(file_id, trashed_ok=True)
    path = cache_path(media.sha256, size)
    if not path.is_file():
        return _not_found()
    rel = path.relative_to(cache_root()).as_posix()
    return _send(
        request,
        path,
        internal=settings.MEDIA_ACCEL_CACHE_PREFIX + rel,
        content_type="image/webp",
        cache=IMMUTABLE,
    )


@require_GET
def thumb(request, file_id: int):
    return _cached(request, file_id, "small")


@require_GET
def preview(request, file_id: int):
    return _cached(request, file_id, "large")


@require_GET
def original(request, file_id: int):
    if not request.user.is_authenticated:
        return _forbidden()
    media = _file(file_id)
    path = to_abs(media.rel_path, library_root())
    if not path.is_file():
        return _not_found()
    content_type = media.mime or mimetypes.guess_type(media.name)[0] or "application/octet-stream"
    inline = extension(media.rel_path) in INLINE_EXTENSIONS
    return _send(
        request,
        path,
        internal=settings.MEDIA_ACCEL_LIBRARY_PREFIX + media.rel_path,
        content_type=content_type,
        cache="private, no-cache",
        attachment_name=None if inline else media.name,
    )
