"""Playable copies of videos browsers cannot play (AVI, MKV, 3GP)."""

from __future__ import annotations

import json
import shutil
import subprocess

import pytest
from django.conf import settings

from jobs.models import Job, JobState
from library.models import Asset, FileRole, FileState, MediaFile
from library.services import playable
from library.services.browse import present_asset, with_details
from library.services.playable import copy_path, make_copy, needs_copy

from .conftest import write

pytestmark = pytest.mark.django_db

has_ffmpeg = pytest.mark.skipif(
    shutil.which(settings.FFMPEG_PATH) is None or shutil.which(settings.FFPROBE_PATH) is None,
    reason="ffmpeg is not installed on this machine",
)


def _media(library, rel: str, mime: str, data: bytes = b"video", sha: str = "cd" * 32, **extra) -> MediaFile:
    write(library / rel, data)
    asset = Asset.objects.create(kind="video", capture_at="2024-01-01T00:00:00Z", capture_source="metadata")
    media = MediaFile.objects.create(
        asset=asset, rel_path=rel, pair_key=rel.lower(), size=len(data), mtime_ns=0, sha256=sha, mime=mime, **extra
    )
    asset.primary_file = media
    asset.save()
    return media


@pytest.mark.parametrize(
    "rel,mime,expected",
    [
        ("a.avi", "video/x-msvideo", True),
        ("a.mkv", "video/x-matroska", True),
        ("a.3gp", "video/3gpp", True),
        ("a.mp4", "video/mp4", False),
        ("a.mov", "video/quicktime", False),
        ("IMG_1.JPG", "video/quicktime", False),  # a clip with the still's name plays as it is
        ("a.jpg", "image/jpeg", False),
    ],
)
def test_needs_copy(rel, mime, expected):
    assert needs_copy(MediaFile(rel_path=rel, mime=mime)) is expected


def test_queued_once_and_not_for_playable_videos(library):
    avi = _media(library, "old.avi", "video/x-msvideo")
    _media(library, "new.mp4", "video/mp4", sha="ef" * 32)
    assert playable.schedule_due() == 1
    assert playable.schedule_due() == 0  # already queued
    Job.objects.filter(kind="playable").update(state=JobState.FAILED)
    assert playable.schedule_due() == 0  # a failure waits for the owner's retry
    assert list(Job.objects.values_list("payload__file", flat=True)) == [avi.pk]


def test_not_queued_once_the_copy_exists(library):
    avi = _media(library, "old.avi", "video/x-msvideo")
    write(copy_path(avi.sha256), b"mp4")
    assert playable.schedule_due() == 0
    assert playable.queue_missing(avi) is False


def test_payload_waits_for_the_copy_then_plays_it(library):
    avi = _media(library, "old.avi", "video/x-msvideo")
    first = present_asset(with_details(Asset.objects.filter(pk=avi.asset_id)).get())
    assert (first["videoUrl"], first["videoPending"]) == (None, True)

    write(copy_path(avi.sha256), b"mp4")
    ready = present_asset(with_details(Asset.objects.filter(pk=avi.asset_id)).get())
    assert ready["videoUrl"].startswith(f"/media/video/{avi.pk}?v=")
    assert ready["videoPending"] is False


def test_payload_plays_an_mp4_from_the_original(library):
    mp4 = _media(library, "new.mp4", "video/mp4")
    payload = present_asset(with_details(Asset.objects.filter(pk=mp4.asset_id)).get())
    assert (payload["videoUrl"], payload["videoPending"]) == (f"/media/original/{mp4.pk}", False)


def test_video_view_serves_the_copy(library, client, django_user_model):
    avi = _media(library, "old.avi", "video/x-msvideo")
    url = f"/media/video/{avi.pk}"
    assert client.get(url).status_code == 403
    client.force_login(django_user_model.objects.create_user("owner", password="x" * 12))
    assert client.get(url).status_code == 404  # not made yet
    write(copy_path(avi.sha256), b"0123456789")
    response = client.get(url, HTTP_RANGE="bytes=2-5")
    assert response.status_code == 206
    assert response["Content-Type"] == "video/mp4"
    assert b"".join(response.streaming_content) == b"2345"


def test_job_skips_trashed_and_live_clips(library):
    trashed = _media(library, "a.avi", "video/x-msvideo", state=FileState.TRASHED)
    clip = _media(library, "b.avi", "video/x-msvideo", sha="ef" * 32, role=FileRole.LIVE_VIDEO)
    for media in (trashed, clip):
        playable.playable_job(Job(payload={"file": media.pk}))
        assert not copy_path(media.sha256).exists()


def test_missing_ffmpeg_is_explained(library, settings):
    settings.FFPROBE_PATH = "definitely-not-ffprobe-xyz"
    avi = _media(library, "a.avi", "video/x-msvideo")
    with pytest.raises(playable.ToolUnavailable, match="ffprobe"):
        make_copy(avi)


# ---------------------------------------------------------------------------
# With the real ffmpeg
# ---------------------------------------------------------------------------


def _make_clip(path, *codec_args: str) -> bytes:
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            settings.FFMPEG_PATH, "-v", "error", "-y",
            "-f", "lavfi", "-i", "testsrc=size=640x360:rate=10:duration=1",
            "-f", "lavfi", "-i", "sine=duration=1",
            *codec_args, str(path),
        ],
        check=True,
    )
    return path.read_bytes()


def _streams(path) -> dict[str, dict]:
    out = subprocess.run(
        [settings.FFPROBE_PATH, "-v", "error", "-show_entries", "stream=codec_type,codec_name,pix_fmt",
         "-show_entries", "format=format_name", "-of", "json", str(path)],
        capture_output=True, check=True,
    ).stdout
    data = json.loads(out)
    found = {s["codec_type"]: s for s in data["streams"]}
    found["format"] = data["format"]
    return found


@has_ffmpeg
def test_a_camera_avi_is_converted_to_h264_and_aac(library, tmp_path):
    # What the library's AVIs hold: Motion JPEG (4:2:2) with PCM sound.
    data = _make_clip(tmp_path / "src" / "cam.avi", "-c:v", "mjpeg", "-pix_fmt", "yuvj422p", "-c:a", "pcm_s16le")
    avi = _media(library, "cam.avi", "video/x-msvideo", data=data)
    out = make_copy(avi)
    streams = _streams(out)
    assert "mp4" in streams["format"]["format_name"]
    assert (streams["video"]["codec_name"], streams["video"]["pix_fmt"]) == ("h264", "yuv420p")
    assert streams["audio"]["codec_name"] == "aac"
    assert not list(out.parent.glob("*.part"))


@has_ffmpeg
def test_h264_in_another_container_is_copied_not_encoded(library, tmp_path):
    data = _make_clip(tmp_path / "src" / "phone.mkv", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac")
    mkv = _media(library, "phone.mkv", "video/x-matroska", data=data)
    args = playable.ffmpeg_args(library / "phone.mkv", tmp_path / "x.mp4", "h264", "aac")
    assert args[args.index("-c:v") + 1] == "copy" and args[args.index("-c:a") + 1] == "copy"
    streams = _streams(make_copy(mkv))
    assert (streams["video"]["codec_name"], streams["audio"]["codec_name"]) == ("h264", "aac")


@has_ffmpeg
def test_a_file_ffmpeg_cannot_read_fails_cleanly(library):
    avi = _media(library, "broken.avi", "video/x-msvideo", data=b"RIFF not really")
    with pytest.raises(ValueError, match="no video stream|could not convert"):
        make_copy(avi)
    assert not copy_path(avi.sha256).exists()
    assert not list(copy_path(avi.sha256).parent.glob("*.part"))
