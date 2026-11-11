"""A stand-in ExifTool that keeps written tags inside the file itself.

Tags are appended to the file's bytes as a JSON trailer. So a write really
changes the file (its hash, its size), survives the copy-verify-replace dance
in fileops.xmp, travels with the file when it is moved, and is read back by the
scanner - everything the real ExifTool does that these tests depend on.
`test_xmp_exiftool.py` runs the same writes against the real one.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from fileops.services import xmp
from library.services import metadata
from library.tests.conftest import drain, jpeg_bytes, library, tags, write  # noqa: F401 - fixtures

MARK = b"\n#GALLERY-TAGS#"


def trailer(path: Path) -> dict:
    data = path.read_bytes()
    at = data.rfind(MARK)
    return json.loads(data[at + len(MARK):]) if at >= 0 else {}


def _set_trailer(path: Path, values: dict) -> None:
    data = path.read_bytes()
    at = data.rfind(MARK)
    body = data[:at] if at >= 0 else data
    path.write_bytes(body + MARK + json.dumps(values).encode())


@pytest.fixture
def exif(tags, monkeypatch):
    """`tags` plus writing. Returns the table of 'camera' tags by file name."""
    fake_read = metadata.read

    def read(path: Path) -> dict:
        raw = dict(fake_read(path)) if path.suffix.lower() != ".xmp" else {}
        for tag, value in trailer(path).items():
            raw[xmp._read_key(tag)] = value
        return raw

    def write_tags(path: Path, args: list[str]) -> None:
        values = trailer(path)
        lists: dict[str, list] = {}
        for arg in args:
            if not arg.startswith("-") or "=" not in arg:
                continue
            tag, _, value = arg[1:].partition("=")
            if value == "":
                values.pop(tag, None)
            elif tag == "XMP-dc:Subject":
                lists.setdefault(tag, []).append(value)
            elif tag == "XMP-xmp:Rating":
                values[tag] = int(value)
            else:
                values[tag] = value
        for tag, items in lists.items():
            values[tag] = items if len(items) > 1 else items[0]
        _set_trailer(path, values)

    monkeypatch.setattr(metadata, "read", read)
    monkeypatch.setattr(metadata, "write_tags", write_tags)
    return tags
