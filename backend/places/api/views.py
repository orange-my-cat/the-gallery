"""The map's data (MAP-01). Thin, as everywhere."""

from __future__ import annotations

from rest_framework.response import Response
from rest_framework.views import APIView

from places.services import mapping


class MapPointsView(APIView):
    """``GET /api/map/points`` - every geotagged item, compact (HLD "Map view")."""

    def get(self, request):
        return Response(mapping.points())
