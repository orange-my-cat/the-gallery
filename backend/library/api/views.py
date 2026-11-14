"""The library's read endpoints and the rescan button. Thin, as in `core`."""

from __future__ import annotations

from rest_framework import serializers
from rest_framework.response import Response
from rest_framework.views import APIView

from jobs.services import retry_failed
from library.models import ScanTrigger
from library.services import browse
from library.services import filters as timeline_filters
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
    # BRW-06: `from`/`to` are capture days, inclusive; `rating` is a minimum.
    since = serializers.DateField(required=False, default=None, allow_null=True)
    until = serializers.DateField(required=False, default=None, allow_null=True)
    type = serializers.ChoiceField(choices=timeline_filters.MEDIA, required=False, allow_blank=True, default="")
    camera = serializers.CharField(required=False, allow_blank=True, default="", max_length=300)
    tag = serializers.CharField(required=False, allow_blank=True, default="", max_length=100)
    rating = serializers.IntegerField(required=False, min_value=0, max_value=5, default=0)
    located = serializers.ChoiceField(choices=("yes", "no"), required=False, allow_blank=True, default="")
    # PPL-03: "12,15" - items with every one of these people in them.
    people = serializers.CharField(required=False, allow_blank=True, default="", max_length=200)

    def validate_people(self, value: str):
        try:
            return [int(v) for v in value.split(",") if v.strip()]
        except ValueError:
            raise serializers.ValidationError("Person ids, comma-separated.") from None

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
    `area` to a box on the map (MAP-01). The filters (BRW-06): `from`, `to`
    (YYYY-MM-DD), `type` (photo, video, raw), `camera`, `tag`, `rating` (at
    least), `favourites` and `located` (yes, no).
    """

    def get(self, request):
        data = request.query_params.copy()
        # `from` is a Python keyword, so the serializer names them since/until.
        for old, new in (("from", "since"), ("to", "until")):
            if old in data:
                data[new] = data.pop(old)[-1]
        params = AssetPageQuery(data=data)
        params.is_valid(raise_exception=True)
        v = params.validated_data
        located = {"yes": True, "no": False}.get(v["located"])
        page = browse.timeline_page(
            cursor=params.validated_data["cursor"] or None,
            limit=params.validated_data["limit"],
            query=params.validated_data["q"],
            folder=params.validated_data["folder"],
            album=params.validated_data["album"],
            favourites=params.validated_data["favourites"],
            area=params.validated_data["area"],
            people=params.validated_data["people"],
            filters=timeline_filters.Filters(
                since=v["since"], until=v["until"], media=v["type"], camera=v["camera"], tag=v["tag"],
                rating=v["rating"], favourites=v["favourites"], located=located,
            ),
        )
        return Response({"results": page.results, "next": page.next})


class FacetsView(APIView):
    """``GET /api/assets/facets`` - the cameras and tags the filters can choose from (BRW-06)."""

    def get(self, request):  # noqa: ARG002
        return Response(timeline_filters.facets(browse.visible_assets()))


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
