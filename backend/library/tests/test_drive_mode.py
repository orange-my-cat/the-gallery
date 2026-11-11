"""Production mounts whole drives; the owner picks the library folder in Settings."""

from __future__ import annotations

import pytest
from django.contrib.auth import get_user_model
from django.test import Client

from library.models import MediaFile, ScanRun
from library.services import browse
from library.services.scan import schedule_due

from .conftest import drain, jpeg_bytes, write

pytestmark = pytest.mark.django_db


@pytest.fixture
def drives(tmp_path, settings):
    """/library with D: inside it, as the production container sees it."""
    root = tmp_path / "library"
    (root / "D" / "Media" / "Photos" / "180221 Penang").mkdir(parents=True)
    (root / "D" / "Media" / "Photos" / "Exports").mkdir()
    (root / "D" / "Repositories").mkdir()
    (root / "D" / "$RECYCLE.BIN").mkdir()
    (root / "D" / "System Volume Information").mkdir()
    settings.LIBRARY_ROOT = root
    settings.LIBRARY_DRIVES = ["D"]
    settings.CACHE_ROOT = tmp_path / "cache"
    return root


@pytest.fixture
def signed_in(settings) -> Client:
    client = Client()
    client.force_login(get_user_model().objects.create_user(username=settings.GALLERY_USERNAME, password="x" * 20))
    return client


def test_nothing_is_scanned_until_a_folder_is_chosen(drives, signed_in, tags):
    status = signed_in.get("/api/scan/status").json()
    assert status["state"] == "none"
    assert signed_in.get("/api/library").json()["path"] is None
    refused = signed_in.post("/api/scan")
    assert refused.json()["error"]["code"] == "library_not_chosen"
    assert schedule_due() is None
    assert not ScanRun.objects.exists()

    settings_body = signed_in.get("/api/settings").json()
    assert (settings_body["mustChoose"], settings_body["libraryPath"], settings_body["libraryRoot"]) == (
        True, None, "This PC",
    )


def test_browsing_lists_drives_then_folders_without_system_ones(drives, signed_in):
    top = signed_in.get("/api/settings/browse").json()
    assert top["folders"] == [{"name": "D:", "path": "D"}]
    assert (top["display"], top["parent"], top["canChoose"]) == ("This PC", None, False)

    d = signed_in.get("/api/settings/browse", {"path": "D"}).json()
    assert [f["name"] for f in d["folders"]] == ["Media", "Repositories"], "no $RECYCLE.BIN or System Volume Information"
    assert (d["display"], d["parent"], d["canChoose"]) == ("D:\\", "", True)

    photos = signed_in.get("/api/settings/browse", {"path": "D/Media/Photos"}).json()
    assert photos["display"] == "D:\\Media\\Photos"
    assert [f["path"] for f in photos["folders"]] == ["D/Media/Photos/180221 Penang", "D/Media/Photos/Exports"]

    for bad in ("../x", "D/Nope"):
        response = signed_in.get("/api/settings/browse", {"path": bad})
        assert response.status_code == 400 and "path" in response.json()["error"]["field_errors"]


def test_choosing_a_folder_scans_it_and_paths_show_as_windows_paths(drives, signed_in, tags):
    write(drives / "D/Media/Photos/180221 Penang/a.jpg", jpeg_bytes(color=(1, 1, 1)))
    write(drives / "D/Media/Photos/Exports/b.jpg", jpeg_bytes(color=(2, 2, 2)))
    write(drives / "D/Repositories/not-a-photo-library.jpg", jpeg_bytes(color=(3, 3, 3)))

    refused = signed_in.put("/api/settings", {"librarySubfolder": ""}, content_type="application/json")
    assert refused.status_code == 400

    saved = signed_in.put(
        "/api/settings",
        {"librarySubfolder": "D/Media/Photos", "exclusions": ["Exports"]},
        content_type="application/json",
    ).json()
    assert saved["scanStarted"] is True
    assert saved["settings"]["libraryPath"] == "D:\\Media\\Photos"
    drain()

    assert list(MediaFile.objects.values_list("rel_path", flat=True)) == ["D/Media/Photos/180221 Penang/a.jpg"], (
        "only the chosen folder, and exclusions are relative to it"
    )
    (asset,) = signed_in.get("/api/assets").json()["results"]
    assert asset["folder"] == "D:\\Media\\Photos\\180221 Penang\\"
    assert signed_in.get("/api/library").json()["path"] == "D:\\Media\\Photos"

    folders = signed_in.get("/api/folders").json()
    assert folders["base"] == "D/Media/Photos"
    assert [f["title"] for f in folders["folders"]] == ["Penang"]


def test_display_paths_for_drives(drives):
    assert browse.display_path("D/Media") == "D:\\Media"
    assert browse.display_path("D") == "D:\\"
    assert browse.display_path("") == "This PC"
    assert browse.display_folder("D/a.jpg") == "D:\\"
