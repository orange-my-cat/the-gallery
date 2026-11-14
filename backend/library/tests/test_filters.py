"""Timeline filters (BRW-06), over HTTP."""

from __future__ import annotations

import pytest
from django.contrib.auth import get_user_model
from django.test import Client

from library.services.scan import start_scan

from .conftest import drain, jpeg_bytes, write

pytestmark = pytest.mark.django_db


@pytest.fixture
def signed_in(settings) -> Client:
    user = get_user_model().objects.create_user(username=settings.GALLERY_USERNAME, password="x" * 20)
    client = Client()
    client.force_login(user)
    return client


@pytest.fixture
def library_of_five(library, tags):
    photo = {"File:MIMEType": "image/jpeg"}
    files = {
        # 08:00 in Hobart is 22:00 the day before in UTC: its day is the 15th.
        "a.jpg": {
            **photo, "EXIF:DateTimeOriginal": "2024:06:15 08:00:00", "EXIF:OffsetTimeOriginal": "+10:00",
            "EXIF:Make": "FUJIFILM", "EXIF:Model": "X-T5", "XMP:Rating": 4, "XMP:Subject": ["Coast"],
            "Composite:GPSLatitude": -42.88, "Composite:GPSLongitude": 147.33,
        },
        "b.jpg": {
            **photo, "EXIF:DateTimeOriginal": "2024:06:14 12:00:00", "EXIF:OffsetTimeOriginal": "+10:00",
            "EXIF:Make": "Canon", "EXIF:Model": "Canon EOS R5", "XMP:Rating": 2, "XMP:Subject": ["coast", "Family"],
        },
        "c.jpg": {**photo, "EXIF:DateTimeOriginal": "2023:01:02 09:00:00", "EXIF:OffsetTimeOriginal": "+11:00"},
        "clip.mp4": {"File:MIMEType": "video/mp4", "QuickTime:CreateDate": "2024:06:20 10:00:00"},
    }
    for i, (name, meta) in enumerate(files.items()):
        write(library / name, jpeg_bytes(color=(i * 40, i, i)))
        tags[name] = meta
    write(library / "IMG_9.CR3", b"raw bytes")  # a RAW on its own
    start_scan()
    drain()
    return library


def names(client: Client, **params) -> list[str]:
    response = client.get("/api/assets", params)
    assert response.status_code == 200, response.json()
    return sorted(a["name"] for a in response.json()["results"])


def test_each_filter_narrows_the_timeline(signed_in, library_of_five):
    assert len(names(signed_in)) == 5
    assert names(signed_in, **{"from": "2024-06-15", "to": "2024-06-15"}) == ["a.jpg"]
    assert names(signed_in, **{"from": "2024-06-14", "to": "2024-12-31"}) == ["a.jpg", "b.jpg", "clip.mp4"]
    assert "IMG_9.CR3" in names(signed_in, **{"from": "2024-06-14"})  # no date of its own: dated by its file
    assert names(signed_in, to="2023-12-31") == ["c.jpg"]
    assert names(signed_in, type="video") == ["clip.mp4"]
    assert names(signed_in, type="raw") == ["IMG_9.CR3"]
    assert "clip.mp4" not in names(signed_in, type="photo")
    assert names(signed_in, camera="Canon EOS R5") == ["b.jpg"]
    assert names(signed_in, camera="fujifilm x-t5") == ["a.jpg"]
    assert names(signed_in, camera="Leica M11") == []
    assert names(signed_in, tag="COAST") == ["a.jpg", "b.jpg"]
    assert names(signed_in, rating=3) == ["a.jpg"]
    assert names(signed_in, located="yes") == ["a.jpg"]
    assert "a.jpg" not in names(signed_in, located="no")


def test_filters_combine_with_each_other_and_with_search(signed_in, library_of_five):
    assert names(signed_in, tag="coast", rating=3) == ["a.jpg"]
    assert names(signed_in, tag="coast", **{"from": "2024-06-14", "to": "2024-06-14"}) == ["b.jpg"]
    assert names(signed_in, q="canon", type="video") == []


@pytest.mark.parametrize(
    "params", [{"from": "June"}, {"type": "gif"}, {"rating": 6}, {"located": "maybe"}],
)
def test_bad_filters_say_so(signed_in, params):
    assert signed_in.get("/api/assets", params).status_code == 400


def test_facets_list_cameras_and_tags_with_counts(signed_in, library_of_five):
    body = signed_in.get("/api/assets/facets").json()
    assert body["cameras"] == [{"name": "Canon EOS R5", "count": 1}, {"name": "FUJIFILM X-T5", "count": 1}]
    assert {t["name"].lower(): t["count"] for t in body["tags"]} == {"coast": 2, "family": 1}
