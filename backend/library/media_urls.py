from __future__ import annotations

from django.urls import path

from library import media

urlpatterns = [
    path("thumb/<int:file_id>/400", media.thumb, name="media-thumb"),
    path("preview/<int:file_id>", media.preview, name="media-preview"),
    path("original/<int:file_id>", media.original, name="media-original"),
]
