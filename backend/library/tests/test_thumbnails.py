"""Thumbnails: size rules, orientation, formats browsers cannot show."""

from __future__ import annotations

import io
import os
import stat

import pytest
from PIL import Image

from library.models import Asset, FileState, MediaFile
from library.services import thumbnails
from library.services.thumbnails import CacheUnavailable, cache_path, make_thumbnail, render, target_size

from .conftest import jpeg_bytes, write

pytestmark = pytest.mark.django_db


@pytest.mark.parametrize(
    "source,size,expected",
    [
        ((4000, 3000), "small", (533, 400)),  # shorter side 400
        ((3000, 4000), "small", (400, 533)),
        ((12000, 2000), "small", (1600, 267)),  # panorama: long side capped
        ((300, 200), "small", (300, 200)),  # never upscaled
        ((4000, 3000), "large", (2048, 1536)),
        ((1000, 800), "large", (1000, 800)),
    ],
)
def test_target_size(source, size, expected):
    assert target_size(*source, size) == expected


def _webp(data: bytes) -> Image.Image:
    image = Image.open(io.BytesIO(data))
    assert image.format == "WEBP"
    return image


def test_render_handles_palette_and_alpha():
    palette = Image.new("P", (50, 50))
    assert _webp(render(palette, "small")).mode == "RGB"
    alpha = Image.new("LA", (50, 50))
    assert _webp(render(alpha, "small")).mode == "RGBA"
    cmyk = Image.new("CMYK", (50, 50))
    assert _webp(render(cmyk, "large")).size == (50, 50)


def _media(library, rel: str, data: bytes, sha: str = "ab" * 32) -> MediaFile:
    write(library / rel, data)
    asset = Asset.objects.create(kind="photo", capture_at="2024-01-01T00:00:00Z", capture_source="metadata")
    return MediaFile.objects.create(
        asset=asset, rel_path=rel, pair_key=rel.lower(), size=len(data), mtime_ns=0, sha256=sha
    )


def test_exif_orientation_is_applied(library):
    media = _media(library, "rot.jpg", jpeg_bytes((800, 600), exif_orientation=6))
    out = make_thumbnail(media, "large")
    assert Image.open(out).size == (600, 800)


def test_heic_is_decoded(library):
    import pillow_heif

    image = Image.new("RGB", (640, 480), (30, 60, 90))
    buffer = io.BytesIO()
    pillow_heif.from_pillow(image).save(buffer, quality=80)
    media = _media(library, "phone.HEIC", buffer.getvalue())
    out = make_thumbnail(media, "small")
    assert Image.open(out).size == (533, 400)


def test_raw_uses_its_embedded_preview_with_the_raw_orientation(library, monkeypatch):
    from library.models import FileMetadata

    monkeypatch.setattr(thumbnails.metadata, "embedded_preview", lambda p: jpeg_bytes((1200, 800)))
    media = _media(library, "x.NEF", b"raw bytes")
    FileMetadata.objects.create(file=media, raw={"EXIF:Orientation": 8})
    media = MediaFile.objects.select_related("metadata").get(pk=media.pk)
    out = make_thumbnail(media, "large")
    assert Image.open(out).size == (800, 1200)


def test_raw_without_a_preview_falls_back_to_rawpy(library, monkeypatch):
    import numpy as np

    class FakeRaw:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def postprocess(self, **kwargs):
            return np.zeros((300, 450, 3), dtype=np.uint8)

    import rawpy

    monkeypatch.setattr(thumbnails.metadata, "embedded_preview", lambda p: None)
    monkeypatch.setattr(rawpy, "imread", lambda path: FakeRaw())
    media = _media(library, "x.ARW", b"raw bytes")
    assert Image.open(make_thumbnail(media, "large")).size == (450, 300)


def test_existing_thumbnail_is_not_redone(library, monkeypatch):
    media = _media(library, "a.jpg", jpeg_bytes())
    first = make_thumbnail(media, "small")
    monkeypatch.setattr(thumbnails, "open_image", lambda *a: pytest.fail("re-rendered"))
    assert make_thumbnail(media, "small") == first


@pytest.mark.skipif(os.name == "nt", reason="POSIX permissions")
def test_thumbnails_are_readable_by_the_web_server(library):
    # Nginx runs as another user in another container and reads the cache
    # directly; mkstemp's 0600 made every thumbnail a 403.
    media = _media(library, "a.jpg", jpeg_bytes())
    out = make_thumbnail(media, "small")
    assert stat.S_IMODE(out.stat().st_mode) == 0o644


def test_a_vanished_file_is_skipped(library):
    media = _media(library, "a.jpg", jpeg_bytes())
    (library / "a.jpg").unlink()
    assert make_thumbnail(media, "small") is None


def test_identical_copies_share_a_thumbnail(library):
    a = _media(library, "a.jpg", jpeg_bytes(), sha="cd" * 32)
    b = _media(library, "b.jpg", jpeg_bytes(), sha="cd" * 32)
    assert make_thumbnail(a, "small") == cache_path(b.sha256, "small")


def test_job_skips_trashed_files(library):
    from jobs.models import Job

    media = _media(library, "a.jpg", jpeg_bytes())
    media.state = FileState.TRASHED
    media.save()
    thumbnails.thumb_small_job(Job(payload={"file": media.pk}))
    assert not cache_path(media.sha256, "small").exists()


def test_without_a_cache_folder(settings):
    settings.CACHE_ROOT = None
    with pytest.raises(CacheUnavailable):
        cache_path("ab" * 32, "small")


def test_missing_ffmpeg_is_explained(library, settings):
    settings.FFPROBE_PATH = "definitely-not-ffprobe-xyz"
    settings.FFMPEG_PATH = "definitely-not-ffmpeg-xyz"
    media = _media(library, "clip.mp4", b"video")
    with pytest.raises(thumbnails.ToolUnavailable, match="ffprobe"):
        make_thumbnail(media, "small")
