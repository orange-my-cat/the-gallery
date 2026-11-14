"""Finding and describing faces (PPL-01, HLD "People").

Each photo is searched once, on its 2,048 px preview - so RAW and HEIC need no
extra decoding, and the faces are found in the picture as it is shown. YuNet
finds faces and their five landmarks; SFace aligns each and describes it as
128 numbers. A small crop of each face is kept in the cache for the screens.

A file is searched again only if its pixels change (`pixel_hash`): writing a
title into it does not move anyone's face.
"""

from __future__ import annotations

import io
import logging
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from django.conf import settings
from django.db import transaction
from django.db.models import F

from core.services.exceptions import BusinessRuleError
from jobs.models import Job, JobState
from jobs.services import enqueue
from library.models import FileRole, FileState, MediaFile
from library.services.paths import content_type
from library.services.thumbnails import cache_path, cache_root, make_thumbnail
from people.fetch_models import present
from people.models import Face, FaceScan

logger = logging.getLogger("gallery")

SCORE = 0.8  # YuNet's confidence, as in the spike
MIN_FACE = 40  # px on the preview: smaller faces describe poorly
CROP = 160  # px, the crop the screens show
BACKFILL_BATCH = 1000

_models: tuple | None = None


class ModelsMissing(BusinessRuleError):
    code = "face_models_missing"


def _load():
    global _models
    if _models is None:
        import cv2

        folder = Path(settings.FACE_MODELS_DIR)
        if not present(folder):
            raise ModelsMissing(
                f"The face models are not in {folder}. Run `manage.py fetch_face_models` (the image ships with them)."
            )
        detector = cv2.FaceDetectorYN.create(str(folder / "yunet.onnx"), "", (320, 320), SCORE, 0.3, 5000)
        recognizer = cv2.FaceRecognizerSF.create(str(folder / "sface.onnx"), "")
        _models = (cv2, detector, recognizer)
    return _models


@dataclass
class Found:
    box: tuple[float, float, float, float]  # x, y, w, h as fractions of the picture
    score: float
    embedding: np.ndarray  # 128 float32, length 1
    crop: bytes  # WebP


def _crop(cv2, img: np.ndarray, x: float, y: float, w: float, h: float) -> bytes:
    """A square around the face with some room, for the screens."""
    from PIL import Image

    side = max(w, h) * 1.6
    cx, cy = x + w / 2, y + h / 2
    height, width = img.shape[:2]
    left, top = int(max(cx - side / 2, 0)), int(max(cy - side / 2, 0))
    right, bottom = int(min(cx + side / 2, width)), int(min(cy + side / 2, height))
    patch = cv2.cvtColor(img[top:bottom, left:right], cv2.COLOR_BGR2RGB)
    image = Image.fromarray(patch).resize((CROP, CROP), Image.Resampling.LANCZOS)
    out = io.BytesIO()
    image.save(out, "WEBP", quality=82)
    return out.getvalue()


def detect(path: Path) -> list[Found]:
    """Every face worth keeping in one picture."""
    from PIL import Image

    cv2, detector, recognizer = _load()
    with Image.open(path) as im:
        img = cv2.cvtColor(np.asarray(im.convert("RGB")), cv2.COLOR_RGB2BGR)
    height, width = img.shape[:2]
    detector.setInputSize((width, height))
    _, rows = detector.detect(img)
    found: list[Found] = []
    for row in rows if rows is not None else []:
        x, y, w, h = (float(v) for v in row[:4])
        if min(w, h) < MIN_FACE:
            continue
        aligned = recognizer.alignCrop(img, row)
        vec = recognizer.feature(aligned).flatten().astype(np.float32)
        vec /= np.linalg.norm(vec) or 1.0
        found.append(
            Found(
                box=(max(x, 0) / width, max(y, 0) / height, w / width, h / height),
                score=float(row[14]),
                embedding=vec,
                crop=_crop(cv2, img, x, y, w, h),
            )
        )
    return found


def crop_path(face_id: int) -> Path:
    return cache_root() / "faces" / f"{face_id % 1000:03d}" / f"{face_id}.webp"


def _write_crop(face_id: int, data: bytes) -> None:
    target = crop_path(face_id)
    target.parent.mkdir(parents=True, exist_ok=True)
    part = target.with_suffix(".part")
    part.write_bytes(data)
    part.replace(target)


# ---------------------------------------------------------------------------
# Which files, and the jobs
# ---------------------------------------------------------------------------


def searchable(media: MediaFile) -> bool:
    """The picture an item shows: a photo or a RAW, its displayed file only.
    Video, a Live Photo's clip and the RAW half of a pair are not searched."""
    if media.state != FileState.PRESENT or media.role in (FileRole.LIVE_VIDEO, FileRole.RAW):
        return False
    return content_type(media.rel_path, media.mime) in ("photo", "raw")


def scan_file(media: MediaFile) -> int | None:
    """Search one file. Returns the faces found, or None if it is gone."""
    preview = cache_path(media.sha256, "large")
    if not preview.is_file() and make_thumbnail(media, "large") is None:
        return None
    found = detect(preview)
    with transaction.atomic():
        # Its pixels changed (or it was never searched): start afresh.
        old = list(Face.objects.filter(file=media).values_list("pk", flat=True))
        Face.objects.filter(pk__in=old).delete()
        for pk in old:
            crop_path(pk).unlink(missing_ok=True)
        for f in found:
            face = Face.objects.create(
                file=media,
                x=f.box[0], y=f.box[1], w=f.box[2], h=f.box[3],
                score=f.score,
                embedding=f.embedding.tobytes(),
            )
            _write_crop(face.pk, f.crop)
        FaceScan.objects.update_or_create(file=media, defaults={"pixel_hash": media.pixel_hash, "found": len(found)})
    return len(found)


def faces_job(job: Job) -> None:
    from people.services.cluster import request_cluster

    media = MediaFile.objects.select_related("metadata").filter(pk=job.payload["file"]).first()
    if media is None or not searchable(media) or not media.pixel_hash:
        return
    scan = FaceScan.objects.filter(file=media).first()
    if scan is not None and scan.pixel_hash == media.pixel_hash:
        return
    found = scan_file(media)
    if found:
        request_cluster()


def _busy() -> bool:
    return Job.objects.filter(kind="faces", state__in=[JobState.QUEUED, JobState.RUNNING]).exists()


def schedule_due(now=None) -> int:
    """Worker scheduler: search what has not been searched (or has new pixels).

    Waits for fingerprints - they say whether the pixels changed - and does
    nothing until the models are there.
    """
    if _busy() or not present(Path(settings.FACE_MODELS_DIR)):
        return 0
    candidates = (
        MediaFile.objects.filter(state=FileState.PRESENT)
        .exclude(pixel_hash="")
        .exclude(role__in=[FileRole.LIVE_VIDEO, FileRole.RAW])
        .exclude(face_scan__isnull=False, face_scan__pixel_hash=F("pixel_hash"))
        .order_by("pk")
    )
    queued = 0
    for media in candidates[: BACKFILL_BATCH * 2]:
        if searchable(media):
            enqueue("faces", {"file": media.pk})
            queued += 1
            if queued >= BACKFILL_BATCH:
                break
    return queued



def rescan() -> dict[str, int]:
    """The owner asked (People screen): search what has not been searched yet
    without waiting for the scheduler, and regroup everyone now rather than at
    the daily run. Faces already found, and what the owner decided about them,
    stay as they are; the regrouping waits for the searches to finish."""
    from people.services.cluster import request_cluster

    queued = schedule_due()
    if Face.objects.exists() or queued:
        request_cluster()
    return {"queued": queued}
