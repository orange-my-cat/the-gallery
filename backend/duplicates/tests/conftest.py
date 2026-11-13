"""Pictures that hash apart, plus the library and ExifTool stand-ins."""

from __future__ import annotations

import io

import numpy as np
from PIL import Image

from fileops.tests.conftest import exif, trailer  # noqa: F401 - fixtures
from library.tests.conftest import drain, library, tags, write  # noqa: F401 - fixtures


def picture(seed: int, size=(640, 480), quality: int = 90) -> bytes:
    """A smooth random scene: each seed is a different picture, and the same
    seed at another size or quality is the same picture."""
    rng = np.random.default_rng(seed)
    base = rng.integers(0, 256, (6, 8, 3), dtype=np.uint8)
    image = Image.fromarray(base, "RGB").resize(size, Image.Resampling.BICUBIC)
    out = io.BytesIO()
    image.save(out, "JPEG", quality=quality)
    return out.getvalue()


def camera(make="FUJIFILM", model="X-T4", when="2024:06:14 18:42:07", size=(640, 480), **more) -> dict:
    """ExifTool's view of a camera original."""
    return {
        "File:MIMEType": "image/jpeg",
        "Composite:ImageSize": f"{size[0]} {size[1]}",
        "EXIF:Make": make,
        "EXIF:Model": model,
        "EXIF:DateTimeOriginal": when,
        **more,
    }


def stripped(size=(640, 480), **more) -> dict:
    """A copy that lost its metadata (a messaging app's)."""
    return {"File:MIMEType": "image/jpeg", "Composite:ImageSize": f"{size[0]} {size[1]}", **more}
