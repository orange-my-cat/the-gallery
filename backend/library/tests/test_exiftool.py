"""Against the real ExifTool, when it is installed (always, inside the image)."""

from __future__ import annotations

import shutil

import pytest
from django.conf import settings
from PIL import Image

from library.services import metadata

pytestmark = pytest.mark.skipif(
    shutil.which(settings.EXIFTOOL_PATH) is None, reason="ExifTool is not installed on this machine"
)


def test_reads_and_normalises_a_real_jpeg(tmp_path):
    exif = Image.Exif()
    exif[0x010F] = "TestMake"  # Make
    exif[0x0110] = "TestModel"  # Model
    exif_ifd = exif.get_ifd(0x8769)
    exif_ifd[0x9003] = "2024:03:01 10:00:00"  # DateTimeOriginal
    exif_ifd[0x9011] = "+08:00"  # OffsetTimeOriginal
    path = tmp_path / "héllo wörld #1.jpg"  # spaces, '#', non-ASCII
    Image.new("RGB", (320, 200)).save(path, "JPEG", exif=exif)

    raw = metadata.read(path)
    from datetime import UTC, datetime

    norm = metadata.normalise(raw, file_mtime=datetime.now(UTC))

    assert raw["EXIF:Make"] == "TestMake"
    assert norm.camera_model == "TestModel"
    assert norm.capture_offset == 480
    assert (norm.width, norm.height) == (320, 200)
    assert norm.mime == "image/jpeg"


def test_a_plain_jpeg_has_no_embedded_preview(tmp_path):
    path = tmp_path / "a.jpg"
    Image.new("RGB", (64, 64)).save(path, "JPEG")
    assert metadata.embedded_preview(path) is None


def test_missing_exiftool_is_explained(monkeypatch):
    monkeypatch.setattr(metadata, "_helper", None)
    monkeypatch.setattr(settings, "EXIFTOOL_PATH", "definitely-not-exiftool-xyz")
    with pytest.raises(metadata.ExifToolUnavailable):
        metadata._exiftool()
