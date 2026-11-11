"""A throwaway library per test, and a stand-in for ExifTool.

Every test that touches files gets its own `tmp_path` library and cache - the
HLD's rule that file tests never run against anything that matters. ExifTool
is replaced by a table of tags keyed by file name, so the indexing rules are
tested without the binary; `test_exiftool.py` exercises the real one when it is
installed.
"""

from __future__ import annotations

import io
import os
from pathlib import Path

import pytest
from PIL import Image

from jobs.models import Job, JobState
from jobs.services import claim, run
from library.services import metadata


def jpeg_bytes(size=(640, 480), color=(200, 120, 40), exif_orientation: int | None = None) -> bytes:
    image = Image.new("RGB", size, color)
    # A second colour in one corner, so orientation is observable.
    image.paste((10, 10, 10), (0, 0, size[0] // 4, size[1] // 4))
    out = io.BytesIO()
    if exif_orientation:
        exif = Image.Exif()
        exif[0x0112] = exif_orientation
        image.save(out, "JPEG", exif=exif)
    else:
        image.save(out, "JPEG")
    return out.getvalue()


def write(path: Path, data: bytes, mtime_ns: int | None = None) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    if mtime_ns is not None:
        os.utime(path, ns=(mtime_ns, mtime_ns))
    return path


@pytest.fixture
def library(tmp_path, settings):
    root = tmp_path / "library"
    root.mkdir()
    settings.LIBRARY_ROOT = root
    settings.CACHE_ROOT = tmp_path / "cache"
    return root


@pytest.fixture
def tags(monkeypatch):
    """`tags["IMG_1.jpg"] = {...}` sets what ExifTool will 'read' from that file."""
    table: dict[str, dict] = {}

    def fake_read(path: Path) -> dict:
        return dict(table.get(path.name, {"File:MIMEType": "image/jpeg"}))

    monkeypatch.setattr(metadata, "read", fake_read)
    monkeypatch.setattr(metadata, "embedded_preview", lambda path: jpeg_bytes((1200, 800), (90, 160, 90)))
    return table


def drain(max_jobs: int = 10_000) -> int:
    """Run every queued job, ignoring backoff and polling delays."""
    from django.utils import timezone

    ran = 0
    while ran < max_jobs:
        Job.objects.filter(state=JobState.QUEUED).update(run_after=timezone.now())
        jobs = claim("test")
        if not jobs:
            return ran
        for job in jobs:
            run(job)
            ran += 1
    raise AssertionError("the queue did not drain")
