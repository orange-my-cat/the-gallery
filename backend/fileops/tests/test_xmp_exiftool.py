"""Write-back against the real ExifTool (always present in the image).

Run on Windows these skip; run in the container (scripts/test-in-container.ps1)
they prove the tag names, the list handling and the verify step are right for
the formats MET-02 names.
"""

from __future__ import annotations

import io
import shutil
from datetime import UTC, datetime

import pytest
from django.conf import settings
from PIL import Image

from fileops.services import xmp
from library.services import metadata
from library.services.indexer import read_with_sidecars

pytestmark = pytest.mark.skipif(
    shutil.which(settings.EXIFTOOL_PATH) is None, reason="ExifTool is not installed on this machine"
)

VALUES = {
    "title": "Harbour at dusk",
    "description": "Line one, é and #",
    "rating": 4,
    "keywords": ["pier", "sunset", "two words"],
    "place": "Hobart",
    "date": {"local": "2021-06-15T09:30:00", "offset": "+10:00"},
}


def _image(path, fmt):
    buffer = io.BytesIO()
    if fmt == "HEIF":
        import pillow_heif

        pillow_heif.from_pillow(Image.new("RGB", (64, 48), (10, 20, 30))).save(buffer)
    else:
        Image.new("RGB", (64, 48), (10, 20, 30)).save(buffer, fmt)
    path.write_bytes(buffer.getvalue())


@pytest.mark.parametrize("name,fmt", [("a.jpg", "JPEG"), ("a.png", "PNG"), ("a.tif", "TIFF"), ("a.heic", "HEIF")])
def test_embedded_write_reads_back_through_the_normaliser(tmp_path, name, fmt):
    path = tmp_path / name
    _image(path, fmt)
    xmp.write(path, xmp.assignments(VALUES, embedded=True), sidecar=False)

    norm = metadata.normalise(metadata.read(path), file_mtime=datetime.now(UTC), name=name)
    assert norm.title == VALUES["title"]
    assert norm.description == VALUES["description"]
    assert norm.rating == 4
    assert list(norm.keywords) == VALUES["keywords"]
    assert norm.place == "Hobart"
    assert norm.capture_offset == 600
    assert norm.capture_at.isoformat() == "2021-06-15T09:30:00+10:00"
    assert not list(tmp_path.glob(".*gallery-tmp*"))


def test_clearing_removes_the_tags(tmp_path):
    path = tmp_path / "a.jpg"
    _image(path, "JPEG")
    xmp.write(path, xmp.assignments(VALUES, embedded=True), sidecar=False)
    empty = {"title": "", "description": "", "rating": 0, "keywords": [], "place": ""}
    xmp.write(path, xmp.assignments(empty, embedded=True), sidecar=False)
    norm = metadata.normalise(metadata.read(path), file_mtime=datetime.now(UTC))
    assert (norm.title, norm.description, norm.rating, norm.keywords, norm.place) == ("", "", 0, (), "")


def test_a_sidecar_is_created_and_outranks_the_raw(tmp_path):
    raw = tmp_path / "DSC_1.NEF"
    raw.write_bytes(b"not really a raw")
    sidecar = tmp_path / "DSC_1.xmp"
    xmp.write(sidecar, xmp.assignments(VALUES, embedded=False), sidecar=True)

    merged = read_with_sidecars(raw)
    norm = metadata.normalise(merged, file_mtime=datetime.now(UTC), name="DSC_1.NEF")
    assert norm.title == VALUES["title"]
    assert list(norm.keywords) == VALUES["keywords"]
    assert norm.capture_at.isoformat() == "2021-06-15T09:30:00+10:00"


def test_before_values_restore_exactly(tmp_path):
    path = tmp_path / "a.jpg"
    _image(path, "JPEG")
    xmp.write(path, xmp.assignments({"title": "First", "keywords": ["a"]}, embedded=True), sidecar=False)
    assign = xmp.assignments({"title": "Second", "keywords": ["b", "c"]}, embedded=True)
    before = xmp.current(path, list(assign))
    xmp.write(path, assign, sidecar=False)
    xmp.write(path, before, sidecar=False)
    norm = metadata.normalise(metadata.read(path), file_mtime=datetime.now(UTC))
    assert (norm.title, norm.keywords) == ("First", ("a",))
