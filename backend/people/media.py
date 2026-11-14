"""`/media/face/{id}` - a face's crop, for the People screens."""

from __future__ import annotations

from django.conf import settings
from django.views.decorators.http import require_GET

from library.media import IMMUTABLE, _forbidden, _not_found, _send
from library.services.thumbnails import cache_root
from people.models import Face
from people.services.faces import crop_path


@require_GET
def face(request, face_id: int):
    if not request.user.is_authenticated:
        return _forbidden()
    if not Face.objects.filter(pk=face_id).exists():
        return _not_found()
    path = crop_path(face_id)
    if not path.is_file():
        return _not_found()
    rel = path.relative_to(cache_root()).as_posix()
    # A face's crop never changes: a new search makes new faces with new ids.
    return _send(request, path, internal=settings.MEDIA_ACCEL_CACHE_PREFIX + rel, content_type="image/webp", cache=IMMUTABLE)
