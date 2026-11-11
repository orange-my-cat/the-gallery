"""The owner's folder convention: `YYMMDD Title`, 99 meaning "unknown"."""

from __future__ import annotations

from datetime import UTC, date, datetime

import pytest

from library.models import Asset, CaptureSource
from library.services.folders import approx_label, folder_date, parse_folder_name, span_label
from library.services.metadata import normalise
from library.services.scan import start_scan

from .conftest import drain, jpeg_bytes, write

TODAY = date(2026, 11, 8)


@pytest.mark.parametrize(
    "name,start,precision,title",
    [
        ("180221 Penang", date(2018, 2, 21), "day", "Penang"),
        ("170607 Saigon", date(2017, 6, 7), "day", "Saigon"),
        ("179999 SMKTC", date(2017, 1, 1), "year", "SMKTC"),
        ("180299 Feb things", date(2018, 2, 1), "month", "Feb things"),
        ("180221", date(2018, 2, 21), "day", ""),
        ("991231 Party", date(1999, 12, 31), "day", "Party"),  # not 2099
        ("180221_Penang", date(2018, 2, 21), "day", "Penang"),
    ],
)
def test_parse(name, start, precision, title):
    found = parse_folder_name(name, today=TODAY)
    assert (found.start, found.precision, found.title) == (start, precision, title)


@pytest.mark.parametrize("name", ["Penang", "189921 x", "181321 x", "180231 x", "1802 Penang", "20180221 x", "Exports"])
def test_not_dated(name):
    assert parse_folder_name(name, today=TODAY) is None


def test_nearest_dated_folder_wins():
    assert folder_date("180221 Penang/Day 2/a.jpg").start == date(2018, 2, 21)
    assert folder_date("179999 SMKTC/180301 Sports day/a.jpg").start == date(2018, 3, 1)
    assert folder_date("Inbox/a.jpg") is None
    assert folder_date("a.jpg") is None


def test_labels_say_only_what_is_known():
    assert approx_label(parse_folder_name("180221 Penang")) == "On or after 21 Feb 2018"
    assert approx_label(parse_folder_name("179999 SMKTC")) == "Sometime in 2017"
    assert approx_label(parse_folder_name("180299 X")) == "Sometime in February 2018"


@pytest.mark.parametrize(
    "folder,first,last,label",
    [
        ("180221 Penang", date(2018, 2, 21), date(2018, 2, 25), "21–25 Feb 2018"),
        ("180221 Penang", date(2018, 2, 22), date(2018, 3, 2), "21 Feb – 2 Mar 2018"),
        ("181230 NYE", date(2018, 12, 30), date(2019, 1, 1), "30 Dec 2018 – 1 Jan 2019"),
        ("180221 Penang", None, None, "from 21 Feb 2018"),
        ("180221 Penang", date(2018, 2, 21), date(2018, 2, 21), "21 Feb 2018"),
        ("179999 SMKTC", date(2017, 3, 1), date(2017, 9, 1), "2017"),
        ("180299 X", None, None, "February 2018"),
        ("Inbox", date(2020, 1, 1), date(2020, 1, 5), "1–5 Jan 2020"),
        ("Inbox", None, None, ""),
    ],
)
def test_span(folder, first, last, label):
    assert span_label(parse_folder_name(folder), first, last) == label


def test_folder_date_is_the_last_resort_before_file_time():
    mtime = datetime(2023, 3, 11, tzinfo=UTC)
    zone = "Asia/Kuala_Lumpur"
    undated = normalise({}, file_mtime=mtime, zone=zone, name="180221 Penang/28309909_o.jpg")
    assert undated.capture_source == "folder"
    assert undated.capture_at.date() == date(2018, 2, 21)
    named = normalise({}, file_mtime=mtime, zone=zone, name="180221 Penang/IMG_20180223_101010.jpg")
    assert named.capture_source == "file_name"
    tagged = normalise({"EXIF:DateTimeOriginal": "2018:02:24 09:00:00"}, file_mtime=mtime, zone=zone, name="180221 Penang/a.jpg")
    assert tagged.capture_source == "metadata"
    loose = normalise({}, file_mtime=mtime, zone=zone, name="Inbox/a.jpg")
    assert loose.capture_source == "file_time"


# ---------------------------------------------------------------------------
# Through the API
# ---------------------------------------------------------------------------


@pytest.fixture
def trips(db, library, tags):
    files = {
        "180221 Penang/IMG_20180221_090000.jpg": {},
        "180221 Penang/IMG_20180225_180000.jpg": {},
        "180221 Penang/28309909_o.jpg": {},  # no date anywhere
        "180221 Penang/Day 2/IMG_20180222_100000.jpg": {},
        "179999 SMKTC/fb_123.jpg": {},
        "Inbox/IMG_20200101_120000.jpg": {},
        "loose.jpg": {},
    }
    for i, rel in enumerate(files):
        write(library / rel, jpeg_bytes(color=(i * 20, 0, 0)))
    start_scan()
    drain()
    return library


@pytest.fixture
def signed_in(settings, db):
    from django.contrib.auth import get_user_model
    from django.test import Client

    client = Client()
    client.force_login(get_user_model().objects.create_user(username=settings.GALLERY_USERNAME, password="x" * 20))
    return client


def test_undated_files_carry_an_approximate_date(trips, signed_in):
    results = {a["name"]: a for a in signed_in.get("/api/assets").json()["results"]}
    assert results["28309909_o.jpg"]["dateApprox"] == {"precision": "day", "label": "On or after 21 Feb 2018"}
    assert results["28309909_o.jpg"]["capturedAt"] == "2018-02-21T00:00:00"
    assert results["fb_123.jpg"]["dateApprox"] == {"precision": "year", "label": "Sometime in 2017"}
    assert results["IMG_20180225_180000.jpg"]["dateApprox"] is None
    assert Asset.objects.filter(capture_source=CaptureSource.FOLDER).count() == 2


def test_root_listing(trips, signed_in):
    body = signed_in.get("/api/folders").json()
    assert body["looseCount"] == 1
    names = [f["name"] for f in body["folders"]]
    assert names == ["180221 Penang", "179999 SMKTC", "Inbox"], "newest trip first, undated folders last"
    penang = body["folders"][0]
    assert penang["title"] == "Penang"
    assert penang["span"] == "21–25 Feb 2018"
    assert (penang["count"], penang["subfolders"]) == (4, 1)
    assert penang["coverUrl"].startswith("/media/thumb/")
    smktc = body["folders"][1]
    assert (smktc["span"], smktc["precision"]) == ("2017", "year")
    assert body["folders"][2]["span"] == "1 Jan 2020"


def test_sub_listing(trips, signed_in):
    body = signed_in.get("/api/folders", {"path": "180221 Penang"}).json()
    assert body["title"] == "Penang"
    assert [f["name"] for f in body["folders"]] == ["Day 2"]
    assert body["looseCount"] == 3


def test_timeline_narrowed_to_a_folder(trips, signed_in):
    names = sorted(a["name"] for a in signed_in.get("/api/assets", {"folder": "180221 Penang"}).json()["results"])
    assert names == [
        "28309909_o.jpg",
        "IMG_20180221_090000.jpg",
        "IMG_20180222_100000.jpg",
        "IMG_20180225_180000.jpg",
    ]
    assert signed_in.get("/api/assets", {"folder": "180221 Pen"}).json()["results"] == [], "whole names only"
