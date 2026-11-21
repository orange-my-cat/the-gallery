"""Browser-playable copies of videos browsers cannot play (BRD risk: "add H.264
preview transcoding").

Browsers play MP4 and QuickTime. AVI, MKV and 3GP they do not: an AVI from a
camera is typically Motion JPEG with PCM sound, which no browser decodes. For
those the worker writes an H.264/AAC MP4 into the cache, named by the file's
SHA-256 like the thumbnails, and the viewer plays that instead of the original.

The decision is made on ExifTool's MIME type, not the extension, so a
QuickTime clip renamed `.JPG` still plays as itself.

Where the streams are already H.264 / AAC / MP3 (a phone's 3GP, most MKVs) they
are copied as they are - quick, and nothing is lost. Anything else is encoded,
capped at 1080p so a long clip still finishes well inside the worker's
30-minute hung-job backstop.
"""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
from pathlib import Path

from django.conf import settings

from jobs.models import Job
from jobs.services import enqueue
from library.models import FileRole, FileState, MediaFile
from library.services.paths import content_type, library_root, to_abs
from library.services.thumbnails import CACHE_FILE_MODE, ToolUnavailable, cache_root

# What every current browser plays without help (HEVC in a MOV aside: BRD risk).
PLAYABLE_MIMES = frozenset({"video/mp4", "video/quicktime"})
COPYABLE_VIDEO = frozenset({"h264"})
COPYABLE_AUDIO = frozenset({"aac", "mp3"})
# Under jobs.services.STALE_AFTER (30 min), so a slow encode fails cleanly
# instead of being handed to a second worker while the first still runs.
ENCODE_TIMEOUT_S = 25 * 60
BACKFILL_BATCH = 50


def needs_copy(media: MediaFile) -> bool:
    return content_type(media.rel_path, media.mime) == "video" and media.mime not in PLAYABLE_MIMES


def copy_path(sha: str) -> Path:
    return cache_root() / "video" / sha[:2] / f"{sha}.mp4"


def queue_missing(media: MediaFile, *, scan_id: int | None = None) -> bool:
    if media.role == FileRole.LIVE_VIDEO or not needs_copy(media) or copy_path(media.sha256).exists():
        return False
    enqueue("playable", {"file": media.pk}, scan_id=scan_id)
    return True


def schedule_due(now=None) -> int:
    """Worker scheduler: copies for videos indexed before copies were made.

    A file with a playable job in any state - queued, running, or failed - is
    left alone: a failed one waits for the owner's retry in the status view.
    """
    candidates = (
        MediaFile.objects.filter(state=FileState.PRESENT, mime__startswith="video/")
        .exclude(mime__in=PLAYABLE_MIMES)
        .exclude(role=FileRole.LIVE_VIDEO)
        .order_by("pk")
    )
    # A finished job's row is deleted, so every row left is queued, running or failed.
    pending = set(Job.objects.filter(kind="playable").values_list("payload__file", flat=True))
    queued = 0
    for media in candidates:
        if media.pk in pending or not needs_copy(media) or copy_path(media.sha256).exists():
            continue
        enqueue("playable", {"file": media.pk})
        queued += 1
        if queued >= BACKFILL_BATCH:
            break
    return queued


# ---------------------------------------------------------------------------
# Making the copy
# ---------------------------------------------------------------------------


def _run(args: list[str], *, tool: str, setting: str, timeout: float) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(args, capture_output=True, timeout=timeout, check=False)  # noqa: S603 - no shell
    except FileNotFoundError as exc:
        raise ToolUnavailable(
            f"{tool} could not be started ({args[0]}). Install ffmpeg, or set {setting} in .env."
        ) from exc


def _codecs(path: Path) -> tuple[str | None, str | None]:
    """The first video and audio streams' codec names, None where there is none."""
    result = _run(
        [settings.FFPROBE_PATH, "-v", "error", "-show_entries", "stream=codec_type,codec_name", "-of", "json", str(path)],
        tool="ffprobe",
        setting="FFPROBE_PATH",
        timeout=60,
    )
    try:
        streams = json.loads(result.stdout or b"{}").get("streams", [])
    except ValueError:
        streams = []
    video = next((s.get("codec_name") for s in streams if s.get("codec_type") == "video"), None)
    audio = next((s.get("codec_name") for s in streams if s.get("codec_type") == "audio"), None)
    return video, audio


def ffmpeg_args(source: Path, target: Path, video: str | None, audio: str | None) -> list[str]:
    args = [settings.FFMPEG_PATH, "-v", "error", "-y", "-i", str(source), "-map", "0:v:0", "-map", "0:a:0?", "-sn"]
    if video in COPYABLE_VIDEO:
        args += ["-c:v", "copy"]
    else:
        args += [
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
            # Noisy old camera footage otherwise comes out near its MJPEG size.
            "-maxrate", "6M", "-bufsize", "12M",
            # Within 1920 x 1920 either way round (1080p, portrait or not),
            # even sides as H.264 needs, and 4:2:0 - MJPEG's 4:2:2 does not play.
            "-vf", "scale='min(iw,1920)':'min(ih,1920)':force_original_aspect_ratio=decrease:force_divisible_by=2",
            "-pix_fmt", "yuv420p",
        ]
    if audio is None:
        args += ["-an"]
    elif audio in COPYABLE_AUDIO:
        args += ["-c:a", "copy"]
    else:
        args += ["-c:a", "aac", "-b:a", "160k"]
    # The index up front, so playback starts before the whole file arrives.
    return [*args, "-movflags", "+faststart", "-f", "mp4", str(target)]


def make_copy(media: MediaFile) -> Path | None:
    """Write the playable copy if it is not already in the cache. None if the file is gone."""
    target = copy_path(media.sha256)
    if target.exists():
        return target
    source = to_abs(media.rel_path, library_root())
    if not source.is_file():
        return None  # moved or missing since it was indexed; the next scan re-queues it
    video, audio = _codecs(source)
    if video is None:
        raise ValueError("ffprobe found no video stream in the file")

    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=target.parent, suffix=".part")
    os.close(fd)
    try:
        try:
            result = _run(
                ffmpeg_args(source, Path(tmp), video, audio), tool="ffmpeg", setting="FFMPEG_PATH",
                timeout=ENCODE_TIMEOUT_S,
            )
        except subprocess.TimeoutExpired as exc:
            raise ValueError(f"ffmpeg took longer than {ENCODE_TIMEOUT_S // 60} minutes to convert the video") from exc
        if result.returncode != 0 or os.path.getsize(tmp) == 0:
            message = result.stderr.decode("utf-8", "replace").strip() or "no output"
            raise ValueError(f"ffmpeg could not convert the video: {message[:500]}")
        os.chmod(tmp, CACHE_FILE_MODE)  # Nginx, another user, sends it (see thumbnails)
        os.replace(tmp, target)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
    return target


def playable_job(job: Job) -> None:
    media = (
        MediaFile.objects.filter(pk=job.payload["file"])
        .exclude(state=FileState.TRASHED)
        .exclude(role=FileRole.LIVE_VIDEO)
        .first()
    )
    if media is not None:
        make_copy(media)
