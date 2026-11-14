"""The duplicates endpoints, over HTTP."""

from __future__ import annotations

import pytest
from django.contrib.auth import get_user_model
from django.test import Client

from library.models import MediaFile
from library.services.scan import start_scan

from .conftest import camera, drain, picture, write

pytestmark = pytest.mark.django_db


@pytest.fixture
def signed_in(settings) -> Client:
    user = get_user_model().objects.create_user(username=settings.GALLERY_USERNAME, password="x" * 20)
    client = Client()
    client.force_login(user)
    return client


@pytest.fixture
def two_copies(library, exif):
    data = picture(1)
    write(library / "2024" / "IMG_0412.jpg", data)
    write(library / "Downloads" / "IMG_0412 (1).jpg", data)
    exif["IMG_0412.jpg"] = exif["IMG_0412 (1).jpg"] = camera()
    start_scan()
    drain()
    return library


@pytest.mark.parametrize("url", ["/api/duplicates", "/api/duplicates/1"])
def test_reads_need_a_session(client, url):
    assert client.get(url).status_code == 403


def test_queue_detail_merge_and_undo(signed_in, two_copies):
    queue = signed_in.get("/api/duplicates").json()
    assert queue["total"] == 1 and queue["counts"]["exact"] == 1 and queue["exactReady"] == 1
    (card,) = queue["groups"]
    assert card["title"] == "2 copies of IMG_0412" and card["count"] == 2 and len(card["thumbs"]) == 2

    detail = signed_in.get(f"/api/duplicates/{card['id']}").json()
    assert detail["position"] == {"index": 0, "total": 1, "prev": None, "next": None}
    assert detail["keeperReason"] == "Has the original name, not a copy’s"
    other = next(c["assetId"] for c in detail["copies"] if c["assetId"] != detail["keeper"])
    chosen = signed_in.get(f"/api/duplicates/{card['id']}", {"keeper": other, "choose": f"title:{other}"})
    assert chosen.json()["keeper"] == other

    response = signed_in.post(f"/api/duplicates/{card['id']}/merge", {"keeper": detail["keeper"]}, content_type="application/json")
    assert response.status_code == 202
    op_id = response.json()["operationId"]
    drain()
    assert signed_in.get(f"/api/operations/{op_id}").json()["state"] == "applied"
    assert signed_in.get("/api/duplicates").json()["total"] == 0

    signed_in.post(f"/api/operations/{op_id}/undo")
    drain()
    assert signed_in.get("/api/duplicates").json()["total"] == 1
    assert MediaFile.objects.filter(state="present").count() == 2


def test_bad_requests_say_what_is_wrong(signed_in, two_copies):
    (card,) = signed_in.get("/api/duplicates").json()["groups"]
    bad = signed_in.get(f"/api/duplicates/{card['id']}", {"choose": "colour:1"})
    assert bad.status_code == 400 and "choices" in bad.json()["error"]["field_errors"]
    assert signed_in.get("/api/duplicates", {"kind": "nope"}).status_code == 400
    assert signed_in.get("/api/duplicates/999999").status_code == 404


def test_dismiss_and_bulk_merge(signed_in, two_copies):
    (card,) = signed_in.get("/api/duplicates").json()["groups"]
    response = signed_in.post(f"/api/duplicates/{card['id']}/dismiss")
    assert response.status_code == 200 and response.json()["state"] == "applied"
    assert signed_in.get("/api/duplicates").json()["total"] == 0
    signed_in.post(f"/api/operations/{response.json()['operationId']}/undo")

    bulk = signed_in.post("/api/duplicates/merge-exact").json()
    assert bulk["started"] == 1
    drain()
    assert signed_in.get("/api/duplicates").json()["total"] == 0


def test_merge_ready_by_kind(signed_in, two_copies):
    queue = signed_in.get("/api/duplicates").json()
    assert queue["ready"] == {"exact": 1, "pixels": 0} and queue["groups"][0]["ready"] is True
    assert signed_in.post("/api/duplicates/merge-ready", {"kind": "nope"}, content_type="application/json").status_code == 400
    none = signed_in.post("/api/duplicates/merge-ready", {"kind": "pixels"}, content_type="application/json").json()
    assert none["started"] == 0
    bulk = signed_in.post("/api/duplicates/merge-ready", {}, content_type="application/json").json()
    assert bulk["started"] == 1
    drain()
    assert signed_in.get("/api/duplicates").json()["total"] == 0
