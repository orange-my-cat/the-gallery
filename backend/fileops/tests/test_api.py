"""The change endpoints, the activity log, trash and albums over HTTP."""

from __future__ import annotations

import pytest
from django.contrib.auth import get_user_model
from django.test import Client

from library.models import MediaFile
from library.services.scan import start_scan
from organise.models import Album

from .conftest import drain, jpeg_bytes, write

pytestmark = pytest.mark.django_db


@pytest.fixture
def signed_in(settings) -> Client:
    client = Client()
    client.force_login(get_user_model().objects.create_user(username=settings.GALLERY_USERNAME, password="x" * 20))
    return client


def post(client: Client, url: str, body: dict):
    return client.post(url, body, content_type="application/json")


@pytest.fixture
def two(library, exif):
    write(library / "2024" / "a.jpg", jpeg_bytes(color=(1, 1, 1)))
    write(library / "2024" / "b.jpg", jpeg_bytes(color=(2, 2, 2)))
    start_scan()
    drain()
    return [str(MediaFile.objects.get(rel_path=f"2024/{n}.jpg").asset_id) for n in "ab"]


@pytest.mark.parametrize(
    "method,url",
    [("post", "/api/assets/bulk"), ("get", "/api/operations"), ("post", "/api/operations"), ("get", "/api/trash"),
     ("get", "/api/albums"), ("get", "/api/tags"), ("get", "/api/folders/targets"), ("post", "/api/trash/empty")],
)
def test_everything_needs_a_session(client, db, method, url):
    assert getattr(client, method)(url).status_code == 403


def test_bulk_edit_then_poll_then_undo(signed_in, two):
    started = post(signed_in, "/api/assets/bulk", {"ids": two, "rating": 5})
    assert started.status_code == 202
    body = started.json()
    assert (body["state"], body["summary"]) == ("pending", "Rated 2 items ★★★★★")

    drain()
    done = signed_in.get(f"/api/operations/{body['operationId']}").json()
    assert (done["state"], done["undoable"]) == ("applied", True)
    assets = signed_in.get("/api/assets").json()["results"]
    assert {a["rating"] for a in assets} == {5}

    undo = post(signed_in, f"/api/operations/{body['operationId']}/undo", {})
    assert undo.status_code == 202 and undo.json()["state"] == "undoing"
    drain()
    assert signed_in.get(f"/api/operations/{body['operationId']}").json()["state"] == "undone"


def test_favourite_is_answered_at_once(signed_in, two):
    response = post(signed_in, "/api/assets/bulk", {"ids": two[:1], "favourite": True})
    assert response.status_code == 200 and response.json()["state"] == "applied"
    assert [a["favourite"] for a in signed_in.get("/api/assets", {"favourites": "true"}).json()["results"]] == [True]


def test_refusals_are_400s_with_a_field(signed_in, two):
    refused = post(signed_in, "/api/assets/bulk", {"ids": two, "rating": 9})
    assert refused.status_code == 400
    assert "rating" in refused.json()["error"]["field_errors"]
    assert post(signed_in, "/api/assets/bulk", {"ids": ["999999"], "rating": 1}).status_code == 404
    assert post(signed_in, "/api/operations", {"kind": "explode", "ids": two}).status_code == 400
    assert post(signed_in, "/api/operations", {"kind": "rename", "ids": two, "name": "x"}).status_code == 400


def test_tags_and_albums_show_on_assets_and_in_search(signed_in, two):
    post(signed_in, "/api/assets/bulk", {"ids": two[:1], "addTags": ["pier"]})
    drain()
    post(signed_in, "/api/albums/add", {"ids": two, "name": "Coast"})

    (a,) = signed_in.get("/api/assets", {"q": "pier"}).json()["results"]
    assert (a["tags"], a["albums"]) == (["pier"], ["Coast"])
    assert len(signed_in.get("/api/assets", {"q": "coast"}).json()["results"]) == 2
    assert signed_in.get("/api/tags").json() == ["pier"]

    albums = signed_in.get("/api/albums").json()
    assert [(x["name"], x["count"]) for x in albums] == [("Coast", 2)]
    album_id = albums[0]["id"]
    assert len(signed_in.get("/api/assets", {"album": album_id}).json()["results"]) == 2

    assert signed_in.patch(f"/api/albums/{album_id}", {"name": "Coast 2024"}, content_type="application/json").status_code == 200
    assert post(signed_in, "/api/albums", {"name": "coast 2024"}).status_code == 409
    assert post(signed_in, f"/api/albums/{album_id}/remove", {"ids": two[:1]}).status_code == 200
    assert signed_in.delete(f"/api/albums/{album_id}").status_code == 200
    assert not Album.objects.exists()


def test_trash_listing_restore_and_empty(signed_in, two):
    started = post(signed_in, "/api/operations", {"kind": "trash", "ids": two[:1]}).json()
    drain()
    assert signed_in.get("/api/trash/count").json() == {"count": 1}
    listing = signed_in.get("/api/trash").json()
    (item,) = listing["items"]
    assert item["folder"].endswith(("2024\\", "2024/")), "shown where it came from"
    assert item["purgeAt"] and listing["retentionDays"] == 30
    assert len(signed_in.get("/api/assets").json()["results"]) == 1, "gone from the timeline"

    thumb = MediaFile.objects.get(asset_id=two[0])
    assert signed_in.get(f"/media/thumb/{thumb.pk}/400").status_code == 200, "the trash can show it"
    assert signed_in.get(f"/media/original/{thumb.pk}").status_code == 404

    post(signed_in, "/api/operations", {"kind": "restore", "ids": two[:1]})
    drain()
    assert signed_in.get("/api/trash/count").json() == {"count": 0}

    post(signed_in, "/api/operations", {"kind": "trash", "ids": two})
    drain()
    emptied = post(signed_in, "/api/trash/empty", {})
    assert emptied.status_code == 202
    drain()
    assert signed_in.get("/api/trash").json()["items"] == []
    assert post(signed_in, "/api/trash/empty", {}).status_code == 400
    log = signed_in.get("/api/operations").json()
    assert log[0]["kind"] == "purge" and log[0]["undoable"] is False
    assert any(op["id"] == started["id"] for op in log)


def test_move_targets_and_move(signed_in, two):
    assert signed_in.get("/api/folders/targets").json() == ["2024"]
    post(signed_in, "/api/operations", {"kind": "move", "ids": two[:1], "folder": "2025/New"})
    drain()
    assert signed_in.get("/api/folders/targets").json() == ["2024", "2025/New"]
    bad = post(signed_in, "/api/operations", {"kind": "move", "ids": two, "folder": "a/b:c"})
    assert bad.status_code == 400 and "folder" in bad.json()["error"]["field_errors"]


def test_rename(signed_in, two):
    response = post(signed_in, "/api/operations", {"kind": "rename", "ids": two[:1], "name": "Harbour"})
    assert response.status_code == 202
    drain()
    assert MediaFile.objects.get(asset_id=two[0]).rel_path == "2024/Harbour.jpg"


def test_undo_what_cannot_be_undone(signed_in, two):
    op = post(signed_in, "/api/assets/bulk", {"ids": two[:1], "favourite": True}).json()
    post(signed_in, f"/api/operations/{op['id']}/undo", {})
    again = post(signed_in, f"/api/operations/{op['id']}/undo", {})
    assert again.status_code == 409
    assert again.json()["error"]["code"] == "cannot_undo"
    assert post(signed_in, "/api/operations/999999/undo", {}).status_code == 404
