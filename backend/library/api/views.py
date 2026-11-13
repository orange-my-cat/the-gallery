"""The library's read endpoints and the rescan button. Thin, as in `core`."""

from __future__ import annotations

from rest_framework import serializers
from rest_framework.response import Response
from rest_framework.views import APIView

from jobs.services import retry_failed
from library.models import ScanTrigger
from library.services import browse
from library.services import settings as app_settings
from library.services.paths import scan_root
from library.services.scan import start_scan


class AssetPageQuery(serializers.Serializer):
    cursor = serializers.CharField(required=False, allow_blank=True, default="")
    limit = serializers.IntegerField(required=False, min_value=1, max_value=browse.MAX_PAGE, default=browse.DEFAULT_PAGE)
    q = serializers.CharField(required=False, allow_blank=True, default="", max_length=200)
    folder = serializers.CharField(required=False, allow_blank=True, default="", max_length=1024)
    album = serializers.IntegerField(required=False, min_value=1, default=None, allow_null=True)
    favourites = serializers.BooleanField(required=False, default=False)
    # MAP-01: "south,west,north,east" in degrees - a map cluster's items.
    area = serializers.CharField(required=False, allow_blank=True, default="", max_length=100)

    def validate_area(self, value: str):
        if not value:
            return None
        try:
            south, west, north, east = (float(v) for v in value.split(","))
        except ValueError:
            raise serializers.ValidationError("south,west,north,east in degrees.") from None
        if not (-90 <= south <= north <= 90 and -180 <= west <= 180 and -180 <= east <= 180):
            raise serializers.ValidationError("That is not a box on the map.")
        return (south, west, north, east)


class AssetListView(APIView):
    """``GET /api/assets?cursor=&limit=&q=&folder=&area=`` - the timeline, newest first, by cursor.

    `folder` narrows it to one folder and everything below it (BRW-03), and
    `area` to a box on the map (MAP-01).
    """

    def get(self, request):
        params = AssetPageQuery(data=request.query_params)
        params.is_valid(raise_exception=True)
        page = browse.timeline_page(
            cursor=params.validated_data["cursor"] or None,
            limit=params.validated_data["limit"],
            query=params.validated_data["q"],
            folder=params.validated_data["folder"],
            album=params.validated_data["album"],
            favourites=params.validated_data["favourites"],
            area=params.validated_data["area"],
        )
        return Response({"results": page.results, "next": page.next})


class FolderQuery(serializers.Serializer):
    path = serializers.CharField(required=False, allow_blank=True, default="", max_length=1024)


class FolderListView(APIView):
    """``GET /api/folders?path=`` - one level of the folder tree, as cards (BRW-03)."""

    def get(self, request):
        params = FolderQuery(data=request.query_params)
        params.is_valid(raise_exception=True)
        return Response(browse.folder_listing(params.validated_data["path"]))


class LibraryView(APIView):
    """``GET /api/library`` - where the library is and how big it is."""

    def get(self, request):  # noqa: ARG002
        return Response(browse.library_info())


class ScanStatusView(APIView):
    """``GET /api/scan/status`` - progress, failures, last scan (LIB-06)."""

    def get(self, request):  # noqa: ARG002
        return Response(browse.scan_status())


class ScanDetailsView(APIView):
    """``GET /api/scan/details`` - worker, queue, failures, recent scans (LIB-06)."""

    def get(self, request):  # noqa: ARG002
        return Response(browse.scan_details())


class RetryFailedView(APIView):
    """``POST /api/scan/retry`` - give every failed job a fresh set of attempts."""

    def post(self, request):  # noqa: ARG002
        retried = retry_failed()
        return Response({"retried": retried, "status": browse.scan_status()})


class SettingsView(APIView):
    """``GET`` and ``PUT /api/settings`` (LIB-01). PUT takes any subset of fields."""

    def get(self, request):  # noqa: ARG002
        return Response(app_settings.present())

    def put(self, request):
        if not isinstance(request.data, dict):
            raise app_settings.InvalidSetting("Send the settings as a JSON object.")
        saved = app_settings.save(request.data)
        return Response({"settings": saved.settings, "scanStarted": saved.scan_started})


class BrowseFoldersView(APIView):
    """``GET /api/settings/browse?path=`` - folders that could become the library."""

    def get(self, request):
        params = FolderQuery(data=request.query_params)
        params.is_valid(raise_exception=True)
        return Response(app_settings.browse(params.validated_data["path"]))


class ScanView(APIView):
    """``POST /api/scan`` - rescan now (LIB-03). Returns the new status.

    Refused, rather than queued to fail, when there is no library to scan -
    including when no library folder has been chosen yet.
    """

    def post(self, request):  # noqa: ARG002
        scan_root()
        start_scan(ScanTrigger.MANUAL)
        return Response(browse.scan_status())
