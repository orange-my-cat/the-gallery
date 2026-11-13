"""The duplicate review API (DUP-01, DUP-03 to DUP-06). Thin, as everywhere.

Changes answer with an operation, as in fileops: a merge is `pending` until
the worker has carried it out - poll `GET /api/operations/{id}`.
"""

from __future__ import annotations

from rest_framework.response import Response
from rest_framework.views import APIView

from duplicates.models import GroupKind
from duplicates.services import review
from fileops.api.views import _accepted, _data, present


class QueueView(APIView):
    """``GET /api/duplicates?kind=`` - open groups, in review order."""

    def get(self, request):
        kind = request.query_params.get("kind") or None
        if kind and kind not in GroupKind.values:
            raise review.InvalidRequest("kind is exact or pixels.", field="kind")
        return Response(review.queue(kind))


class GroupView(APIView):
    """``GET /api/duplicates/{id}?keeper=&choose=date:12,gps:15`` - one group
    with the merge preview for that keeper and those choices."""

    def get(self, request, group_id: int):
        choose = review.parse_choose(request.query_params.get("choose", ""))
        return Response(review.detail(group_id, request.query_params.get("keeper"), choose))


class MergeView(APIView):
    """``POST /api/duplicates/{id}/merge`` - `{keeper?, choices?: {field: id}}`."""

    def post(self, request, group_id: int):
        data = _data(request)
        return _accepted(review.merge(group_id, data.get("keeper"), data.get("choices")))


class DismissView(APIView):
    """``POST /api/duplicates/{id}/dismiss`` - not duplicates / keep all."""

    def post(self, request, group_id: int):
        return _accepted(review.dismiss(group_id))


class MergeExactView(APIView):
    """``POST /api/duplicates/merge-exact`` - every conflict-free exact group (DUP-06)."""

    def post(self, request):
        started = review.merge_exact()
        return Response({"started": len(started), "operations": [present(op) for op in started]})
