from __future__ import annotations

from django.urls import path

from library.api.views import (
    AssetListView,
    BrowseFoldersView,
    FacetsView,
    FolderListView,
    LibraryView,
    RetryFailedView,
    ScanDetailsView,
    ScanStatusView,
    ScanView,
    SettingsView,
)

urlpatterns = [
    path("assets", AssetListView.as_view(), name="assets"),
    path("assets/facets", FacetsView.as_view(), name="asset-facets"),
    path("folders", FolderListView.as_view(), name="folders"),
    path("library", LibraryView.as_view(), name="library"),
    path("settings", SettingsView.as_view(), name="settings"),
    path("settings/browse", BrowseFoldersView.as_view(), name="settings-browse"),
    path("scan/status", ScanStatusView.as_view(), name="scan-status"),
    path("scan/details", ScanDetailsView.as_view(), name="scan-details"),
    path("scan/retry", RetryFailedView.as_view(), name="scan-retry"),
    path("scan", ScanView.as_view(), name="scan"),
]
