"""Changes (ORG, FOP, MET-02) and the operation log. Thin, as everywhere.

Every change answers with an operation: `{"operationId", "state"}`. A state of
`pending` means the worker is still on it - poll `GET /api/operations/{id}`.
"""

from __future__ import annotations

from rest_framework import status as http
from rest_framework.response import Response
from rest_framework.views import APIView

from fileops.models import Operation
from fileops.services import ops
from library.services import browse
from organise import services as organise


def present(op: Operation) -> dict:
    return {
        "id": str(op.pk),
        "operationId": str(op.pk),
        "kind": op.kind,
        "state": op.state,
        "summary": op.summary,
        "error": op.error or None,
        "undoable": op.undoable and op.state == "applied",
        "step": len(op.steps),
        "createdAt": op.created_at.isoformat(),
        "finishedAt": op.finished_at.isoformat() if op.finished_at else None,
    }


def _accepted(op: Operation) -> Response:
    op.refresh_from_db()
    return Response(present(op), status=http.HTTP_202_ACCEPTED if op.state in ("pending", "undoing") else 200)


def _data(request) -> dict:
    if not isinstance(request.data, dict):
        raise ops.InvalidRequest("Send a JSON object.")
    return request.data


class BulkEditView(APIView):
    """``POST /api/assets/bulk`` - ORG-02, ORG-03, ORG-04 for one or many items.

    `{ids, favourite?, rating?, title?, description?, place?, capturedAt?,
    addTags?, removeTags?}`. A favourite alone is one change; any other field
    is an edit that is also written into the files.
    """

    def post(self, request):
        data = _data(request)
        ids = data.get("ids") or []
        if "favourite" in data:
            if not isinstance(data["favourite"], bool):
                raise ops.InvalidRequest("On or off.", field="favourite")
            others = set(data) - {"ids", "favourite"}
            if others:
                raise ops.InvalidRequest("Change favourites on their own.")
            return _accepted(ops.set_favourite(ids, data["favourite"]))
        fields = {k: data[k] for k in ("title", "description", "place", "rating", "capturedAt") if k in data}
        add, remove = data.get("addTags") or [], data.get("removeTags") or []
        if not isinstance(add, list) or not isinstance(remove, list):
            raise ops.InvalidRequest("Tags are a list.", field="addTags")
        return _accepted(ops.edit(ids, fields=fields, add_tags=add, remove_tags=remove))


class OperationsView(APIView):
    """``GET /api/operations`` - the activity log. ``POST`` - a file operation:
    `{kind: trash|restore|purge|move|rename, ids, folder?, name?}`."""

    def get(self, request):
        try:
            limit = max(1, min(int(request.query_params.get("limit", 50)), 200))
        except ValueError:
            limit = 50
        return Response([present(op) for op in Operation.objects.order_by("-created_at", "-pk")[:limit]])

    def post(self, request):
        data = _data(request)
        kind, ids = data.get("kind"), data.get("ids") or []
        if kind == "trash":
            return _accepted(ops.trash(ids))
        if kind == "restore":
            return _accepted(ops.restore(ids))
        if kind == "purge":
            return _accepted(ops.purge(ids))
        if kind == "move":
            return _accepted(ops.move(ids, data.get("folder")))
        if kind == "rename":
            if len(ids) != 1:
                raise ops.InvalidRequest("Rename one item at a time.", field="ids")
            return _accepted(ops.rename(ids[0], data.get("name")))
        raise ops.InvalidRequest("Unknown kind of operation.", field="kind")


class OperationView(APIView):
    def get(self, request, op_id: int):  # noqa: ARG002
        return Response(present(ops.get(op_id)))


class UndoView(APIView):
    def post(self, request, op_id: int):  # noqa: ARG002
        return _accepted(ops.undo(op_id))


class TrashView(APIView):
    """``GET /api/trash`` - what is in it. ``POST /api/trash/empty`` - all of it, for good."""

    def get(self, request):  # noqa: ARG002
        return Response(browse.trash_listing())


class EmptyTrashView(APIView):
    def post(self, request):  # noqa: ARG002
        op = ops.purge()
        if op is None:
            raise ops.InvalidRequest("The trash is already empty.")
        return _accepted(op)


class TrashCountView(APIView):
    def get(self, request):  # noqa: ARG002
        return Response({"count": browse.trash_count()})


class AlbumsView(APIView):
    """``GET /api/albums``; ``POST {name}`` creates one."""

    def get(self, request):  # noqa: ARG002
        return Response(browse.albums_listing())

    def post(self, request):
        return _accepted(ops.create_album(_data(request).get("name")))


class AlbumView(APIView):
    """``PATCH {name}`` renames; ``DELETE`` deletes the album, never its photos."""

    def patch(self, request, album_id: int):
        return _accepted(ops.rename_album(album_id, _data(request).get("name")))

    def delete(self, request, album_id: int):  # noqa: ARG002
        return _accepted(ops.delete_album(album_id))


class AlbumAddView(APIView):
    """``POST /api/albums/add {name, ids}`` - into the album of that name, made if new."""

    def post(self, request):
        data = _data(request)
        return _accepted(ops.add_to_album(data.get("ids") or [], data.get("name")))


class AlbumRemoveView(APIView):
    def post(self, request, album_id: int):
        return _accepted(ops.remove_from_album(album_id, _data(request).get("ids") or []))


class TagsView(APIView):
    def get(self, request):  # noqa: ARG002
        return Response(organise.suggestions())


class MoveTargetsView(APIView):
    def get(self, request):  # noqa: ARG002
        return Response(browse.move_targets())
