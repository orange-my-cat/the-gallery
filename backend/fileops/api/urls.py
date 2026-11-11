from __future__ import annotations

from django.urls import path

from fileops.api import views

urlpatterns = [
    path("assets/bulk", views.BulkEditView.as_view(), name="assets-bulk"),
    path("operations", views.OperationsView.as_view(), name="operations"),
    path("operations/<int:op_id>", views.OperationView.as_view(), name="operation"),
    path("operations/<int:op_id>/undo", views.UndoView.as_view(), name="operation-undo"),
    path("trash", views.TrashView.as_view(), name="trash"),
    path("trash/count", views.TrashCountView.as_view(), name="trash-count"),
    path("trash/empty", views.EmptyTrashView.as_view(), name="trash-empty"),
    path("albums", views.AlbumsView.as_view(), name="albums"),
    path("albums/add", views.AlbumAddView.as_view(), name="albums-add"),
    path("albums/<int:album_id>", views.AlbumView.as_view(), name="album"),
    path("albums/<int:album_id>/remove", views.AlbumRemoveView.as_view(), name="album-remove"),
    path("tags", views.TagsView.as_view(), name="tags"),
    path("folders/targets", views.MoveTargetsView.as_view(), name="move-targets"),
]
