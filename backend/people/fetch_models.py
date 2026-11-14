"""Fetch the face models (PPL-01): OpenCV Zoo's YuNet and SFace.

Standard library only, so the image build can run it before the application
is copied in (and cache the layer). Each file is checked against its SHA-256:
a model is code the worker runs, so nothing unverified is ever used.

    python people/fetch_models.py <dir>

Licences: YuNet is MIT, SFace is Apache-2.0 (opencv/opencv_zoo).
"""

from __future__ import annotations

import hashlib
import sys
import urllib.request
from pathlib import Path

ZOO = "https://github.com/opencv/opencv_zoo/raw/main/models"
MODELS = {
    "yunet.onnx": (
        f"{ZOO}/face_detection_yunet/face_detection_yunet_2023mar.onnx",
        "8f2383e4dd3cfbb4553ea8718107fc0423210dc964f9f4280604804ed2552fa4",
    ),
    "sface.onnx": (
        f"{ZOO}/face_recognition_sface/face_recognition_sface_2021dec.onnx",
        "0ba9fbfa01b5270c96627c4ef784da859931e02f04419c829e83484087c34e79",
    ),
}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def fetch(dest: Path) -> list[str]:
    """Download what is missing or wrong. Returns the names downloaded."""
    dest.mkdir(parents=True, exist_ok=True)
    fetched = []
    for name, (url, expected) in MODELS.items():
        target = dest / name
        if target.is_file() and _sha256(target) == expected:
            continue
        part = target.with_suffix(".part")
        with urllib.request.urlopen(url, timeout=120) as response, part.open("wb") as out:  # noqa: S310 - fixed https URLs
            while chunk := response.read(1 << 20):
                out.write(chunk)
        got = _sha256(part)
        if got != expected:
            part.unlink(missing_ok=True)
            raise SystemExit(f"{name}: checksum mismatch ({got}); refusing to use it")
        part.replace(target)
        fetched.append(name)
    return fetched


def present(dest: Path) -> bool:
    return all((dest / name).is_file() for name in MODELS)


if __name__ == "__main__":
    folder = Path(sys.argv[1] if len(sys.argv) > 1 else "face-models")
    print("fetched:", fetch(folder) or "nothing (all present)")
