"""The timeline, library and scan endpoints, and media serving."""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.contrib.auth import get_user_model
from django.test import Client
from django.utils import timezone

from jobs.models import Job, JobState
from library.models import Asset, FileState, MediaFile, ScanRun, ScanState
from library.services import browse
from library.services.scan import start_scan

from .conftest import drain, jpeg_bytes, write

pytestmark = pytest.mark.django_db


@pytest.fixture
def signed_in(settings) -> Client:
    user = get_user_model().objects.create_user(username=settings.GALLERY_USERNAME, password="x" * 20)
    client = Client()
    client.force_login(user)
    return client


def scanned(library, tags, files: dict[str, dict]) -> None:
    for i, (rel, meta) in enumerate(files.items()):
        write(library / rel, jpeg_bytes(color=(i, i, i)))
        tags[rel.rsplit("/", 1)[-1]] = {"File:MIMEType": "image/jpeg", **meta}
    start_scan()
    drain()


# ---------------------------------------------------------------------------
# Authentication
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("url", ["/api/assets", "/api/library", "/api/scan/status"])
def test_reads_need_a_session(client, url):
    response = client.get(url)
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "not_authenticated"


@pytest.mark.parametrize("url", ["/media/thumb/1/400", "/media/preview/1", "/media/original/1"])
def test_media_needs_a_session(client, url):
    assert client.get(url).status_code == 403


# ---------------------------------------------------------------------------
# Timeline
# ---------------------------------------------------------------------------


def test_assets_newest_first_in_the_frontend_shape(signed_in, library, tags):
    scanned(
        library,
        tags,
        {
            "2023/old.jpg": {"EXIF:DateTimeOriginal": "2023:01:01 08:00:00", "EXIF:OffsetTimeOriginal": "+10:00"},
            "2024/new.jpg": {
                "EXIF:DateTimeOriginal": "2024:06:01 18:30:05",
                "EXIF:OffsetTimeOriginal": "+08:00",
                "EXIF:Make": "Canon",
                "EXIF:Model": "Canon EOS R5",
                "EXIF:ExposureTime": 0.004,
                "EXIF:FNumber": 2.8,
                "EXIF:ISO": 400,
                "EXIF:FocalLength": 35.4,
                "Composite:GPSLatitude": -42.88,
                "Composite:GPSLongitude": 147.33,
                "Composite:ImageSize": "640 480",
            },
        },
    )

    body = signed_in.get("/api/assets").json()

    assert body["next"] is None
    first, second = body["results"]
    assert (first["name"], second["name"]) == ("new.jpg", "old.jpg")
    assert first["capturedAt"] == "2024-06-01T18:30:05"
    assert first["captureOffset"] == "+08:00"
    assert first["kind"] == "jpg" and first["ext"] == "JPG"
    assert first["camera"] == "Canon EOS R5"
    assert (first["shutter"], first["fNumber"], first["iso"], first["focalLength"]) == ("1/250", 2.8, 400, 35)
    assert first["gps"] == [-42.88, 147.33]
    assert (first["width"], first["height"]) == (640, 480)
    assert first["folder"].endswith(("2024\\", "2024/"))
    assert first["thumbReady"] is True
    media = MediaFile.objects.get(rel_path="2024/new.jpg")
    assert first["thumbUrl"] == f"/media/thumb/{media.pk}/400?v={media.sha256[:16]}"
    assert first["videoUrl"] is None
    assert first["liveUrl"] is None


def test_cursor_pages_cover_everything_once(signed_in, library, tags):
    scanned(library, tags, {f"p{i}.jpg": {"EXIF:DateTimeOriginal": "2024:01:01 00:00:00"} for i in range(7)})

    seen, cursor = [], ""
    for _ in range(10):
        body = signed_in.get("/api/assets", {"limit": 3, "cursor": cursor}).json()
        seen += [a["id"] for a in body["results"]]
        cursor = body["next"]
        if not cursor:
            break
    assert len(seen) == len(set(seen)) == 7, "same capture time: the id breaks the tie"


def test_bad_cursor_and_limit(signed_in):
    assert signed_in.get("/api/assets", {"cursor": "!!nonsense"}).json()["error"]["code"] == "bad_cursor"
    assert signed_in.get("/api/assets", {"limit": 0}).status_code == 400


def test_search_matches_every_word(signed_in, library, tags):
    scanned(
        library,
        tags,
        {
            "Hobart/a.jpg": {"EXIF:Model": "X-T5"},
            "Hobart/b.jpg": {"XMP:Title": "Harbour at dusk"},
            "Melbourne/c.jpg": {"XMP:Title": "Harbour"},
        },
    )

    def names(q):
        return sorted(a["name"] for a in signed_in.get("/api/assets", {"q": q}).json()["results"])

    assert names("hobart") == ["a.jpg", "b.jpg"]
    assert names("harbour hobart") == ["b.jpg"]
    assert names("x-t5") == ["a.jpg"]
    assert names("nothing-like-this") == []


def test_a_pair_is_named_by_its_raw(signed_in, library, tags):
    write(library / "IMG_1.CR3", b"raw bytes")
    write(library / "IMG_1.JPG", jpeg_bytes())
    start_scan()
    drain()

    (asset,) = signed_in.get("/api/assets").json()["results"]
    raw = MediaFile.objects.get(rel_path="IMG_1.CR3")
    assert (asset["kind"], asset["name"], asset["ext"], asset["pairName"]) == ("pair", "IMG_1.CR3", "CR3", "IMG_1.JPG")
    assert asset["originalUrl"] == f"/media/original/{raw.pk}"
    assert asset["size"] == len(b"raw bytes")


def test_missing_files_linger_for_one_scan_then_drop_out(signed_in, library, tags):
    scanned(library, tags, {"a.jpg": {}})
    (library / "a.jpg").unlink()

    start_scan()
    drain()
    (asset,) = signed_in.get("/api/assets").json()["results"]
    assert asset["missing"] is True

    start_scan()
    drain()
    assert signed_in.get("/api/assets").json()["results"] == []


# ---------------------------------------------------------------------------
# Library and scan status
# ---------------------------------------------------------------------------


def test_library_summary(signed_in, library, tags):
    scanned(library, tags, {"a.jpg": {}, "b.jpg": {}})
    body = signed_in.get("/api/library").json()
    assert body["itemCount"] == 2
    assert body["totalBytes"] == sum(f.size for f in MediaFile.objects.all())
    assert body["path"] == browse.display_root()


def test_no_library(signed_in, settings):
    settings.LIBRARY_ROOT = None
    assert signed_in.get("/api/library").json()["path"] is None
    assert signed_in.get("/api/scan/status").json()["state"] == "none"
    refused = signed_in.post("/api/scan")
    assert refused.status_code == 400
    assert refused.json()["error"]["code"] == "library_unavailable"


def test_scan_status_through_a_scan(signed_in, library, tags):
    write(library / "a.jpg", jpeg_bytes())
    write(library / "b.jpg", jpeg_bytes(color=(1, 1, 1)))

    started = signed_in.post("/api/scan").json()
    assert started["state"] == "scanning" and started["firstScan"] is True

    from jobs.services import claim, run

    run(claim("t")[0])  # the walk
    mid = signed_in.get("/api/scan/status").json()
    assert (mid["state"], mid["done"], mid["total"]) == ("scanning", 0, 2)

    drain()
    done = signed_in.get("/api/scan/status").json()
    assert done["state"] == "idle"
    assert done["lastScanAt"] is not None


def test_scan_status_reports_failures(signed_in, library, tags):
    scanned(library, tags, {"a.jpg": {}})
    Job.objects.create(kind="index_file", payload={"path": "x.jpg"}, state=JobState.FAILED, run_after=timezone.now())
    body = signed_in.get("/api/scan/status").json()
    assert (body["state"], body["failedFiles"]) == ("errors", 1)


def test_a_failed_scan_explains_itself(signed_in, library):
    ScanRun.objects.create(
        trigger="manual", state=ScanState.FAILED, finished_at=timezone.now(), error="The folder vanished."
    )
    body = signed_in.get("/api/scan/status").json()
    assert (body["state"], body["message"]) == ("errors", "The folder vanished.")


def test_rescan_while_scanning_returns_the_same_scan(signed_in, library, tags):
    signed_in.post("/api/scan")
    signed_in.post("/api/scan")
    assert ScanRun.objects.count() == 1


# ---------------------------------------------------------------------------
# Media
# ---------------------------------------------------------------------------


def _one(library, tags, rel="2024/a b #1 é.jpg") -> MediaFile:
    scanned(library, tags, {rel: {}})
    return MediaFile.objects.get()


def test_thumb_and_preview_stream_in_development(signed_in, library, tags):
    media = _one(library, tags)
    for url in (f"/media/thumb/{media.pk}/400", f"/media/preview/{media.pk}"):
        response = signed_in.get(url)
        assert response.status_code == 200
        assert response["Content-Type"] == "image/webp"
        assert "immutable" in response["Cache-Control"]
        assert b"".join(response.streaming_content)[:4] == b"RIFF"


def test_a_thumbnail_not_made_yet_is_an_uncached_404(signed_in, library, tags):
    media = _one(library, tags)
    from library.services.thumbnails import cache_path

    cache_path(media.sha256, "small").unlink()
    response = signed_in.get(f"/media/thumb/{media.pk}/400")
    assert response.status_code == 404
    assert response["Cache-Control"] == "no-store"


def test_original_inline_for_jpeg_and_attachment_for_raw(signed_in, library, tags):
    media = _one(library, tags)
    response = signed_in.get(f"/media/original/{media.pk}")
    assert response.status_code == 200
    assert not response.get("Content-Disposition", "inline").startswith("attachment")
    assert response["Cache-Control"] == "private, no-cache"

    write(library / "x.NEF", b"raw")
    start_scan()
    drain()
    raw = MediaFile.objects.get(rel_path="x.NEF")
    response = signed_in.get(f"/media/original/{raw.pk}")
    assert response["Content-Disposition"].startswith("attachment;")


def test_accel_hands_nginx_an_encoded_internal_path(signed_in, library, tags, settings):
    settings.MEDIA_DELIVERY = "accel"
    media = _one(library, tags)

    original = signed_in.get(f"/media/original/{media.pk}")
    assert original["X-Accel-Redirect"] == "/_protected/library/2024/a%20b%20%231%20%C3%A9.jpg"
    assert original.content == b""

    thumb = signed_in.get(f"/media/thumb/{media.pk}/400")
    assert thumb["X-Accel-Redirect"] == f"/_protected/cache/thumbs/{media.sha256[:2]}/{media.sha256}_400.webp"


@pytest.mark.parametrize(
    "header,status,content_range,body",
    [
        ("bytes=0-3", 206, "bytes 0-3/{size}", slice(0, 4)),
        ("bytes=10-", 206, "bytes 10-{last}/{size}", slice(10, None)),
        ("bytes=-5", 206, "bytes {tail}-{last}/{size}", slice(-5, None)),
        ("bytes=5-999999999", 206, "bytes 5-{last}/{size}", slice(5, None)),
        ("bytes=999999999-", 416, "bytes */{size}", None),
        ("bytes=0-1,5-6", 200, None, slice(None)),  # multi-range: the whole file
        ("items=0-1", 200, None, slice(None)),
    ],
)
def test_development_answers_byte_ranges_so_video_can_seek(signed_in, library, tags, header, status, content_range, body):
    media = _one(library, tags)
    data = (library / media.rel_path).read_bytes()
    size = len(data)

    response = signed_in.get(f"/media/original/{media.pk}", HTTP_RANGE=header)

    assert response.status_code == status
    if content_range:
        assert response["Content-Range"] == content_range.format(size=size, last=size - 1, tail=size - 5)
    if body is not None:
        assert response["Accept-Ranges"] == "bytes"
        received = b"".join(response.streaming_content)
        assert received == data[body]
        if status == 206:
            assert int(response["Content-Length"]) == len(received)


def test_trashed_and_unknown_files_are_not_served(signed_in, library, tags):
    media = _one(library, tags)
    MediaFile.objects.filter(pk=media.pk).update(state=FileState.TRASHED)
    assert signed_in.get(f"/media/original/{media.pk}").status_code == 404
    assert signed_in.get("/media/original/999999").status_code == 404


def test_a_vanished_original_is_404(signed_in, library, tags):
    media = _one(library, tags)
    (library / media.rel_path).unlink()
    assert signed_in.get(f"/media/original/{media.pk}").status_code == 404


def test_display_paths(settings):
    settings.LIBRARY_HOST_DIR = "D:/Photos"
    assert browse.display_root() == "D:\\Photos"
    assert browse.display_folder("2024/Trip/a.jpg") == "D:\\Photos\\2024\\Trip\\"
    assert browse.display_folder("a.jpg") == "D:\\Photos\\"
    settings.LIBRARY_HOST_DIR = ""
    settings.LIBRARY_ROOT = "/library"
    assert browse.display_folder("2024/a.jpg") == "/library/2024/"


@pytest.mark.parametrize(
    "make,model,expected",
    [
        ("Canon", "Canon EOS R5", "Canon EOS R5"),
        ("FUJIFILM", "X-T5", "FUJIFILM X-T5"),
        ("NIKON CORPORATION", "NIKON D750", "NIKON D750"),
        ("Apple", "", "Apple"),
        ("", "", ""),
    ],
)
def test_camera_name(make, model, expected):
    assert browse.camera_name(make, model) == expected


@pytest.mark.parametrize("seconds,text", [(0.004, "1/250"), (1 / 3, "1/3"), (2.5, "2.5"), (None, ""), (0, "")])
def test_shutter(seconds, text):
    assert browse.shutter(seconds) == text


def test_capture_without_offset_shows_in_the_app_zone(settings):
    asset = Asset(capture_at=timezone.now().replace(microsecond=0), capture_offset=None)
    settings.TIME_ZONE = "Asia/Kuala_Lumpur"
    _, offset = browse.local_capture(asset)
    assert offset == "+08:00"
    asset.capture_offset = -330
    assert browse.local_capture(asset)[1] == "-05:30"
    assert timedelta(minutes=-330)
