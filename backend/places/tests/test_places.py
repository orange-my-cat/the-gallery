"""Place names from positions (MET-03) and the map's data (MAP-01)."""

from __future__ import annotations

import math
import zipfile

import pytest
from django.contrib.auth import get_user_model
from django.test import Client
from hypothesis import HealthCheck, given, settings as hsettings, strategies as st

from jobs.models import Job
from library.models import Asset
from library.services.scan import start_scan
from library.tests.conftest import drain, jpeg_bytes, library, tags, write  # noqa: F401 - fixtures
from places.models import Place
from places.services import geocode

pytestmark = pytest.mark.django_db

# geonameid, name, ascii, alternates, lat, lon, class, code, country, cc2,
# admin1, admin2, admin3, admin4, population, ...
CITIES = [
    ("1735106", "George Town", "5.41123", "100.33543", "MY", "09", "300000"),
    ("1733026", "Jelutong", "5.38333", "100.31667", "MY", "09", "50000"),
    ("1566083", "Ho Chi Minh City", "10.82302", "106.62965", "VN", "20", "8000000"),
    ("2147714", "Sydney", "-33.86785", "151.20732", "AU", "02", "4600000"),
    ("2155115", "Ngaio", "-41.25", "174.77", "NZ", "G2", "4000"),
]


@pytest.fixture
def geonames(tmp_path):
    data = tmp_path / "geonames"
    data.mkdir()
    with zipfile.ZipFile(data / "cities1000.zip", "w") as z:
        z.writestr(
            "cities1000.txt",
            "".join(
                f"{gid}\t{name}\t{name}\t\t{lat}\t{lon}\tP\tPPL\t{cc}\t\t{a1}\t\t\t\t{pop}\t\t\tAsia/X\t2024-01-01\n"
                for gid, name, lat, lon, cc, a1, pop in CITIES
            ),
        )
    (data / "admin1CodesASCII.txt").write_text(
        "MY.09\tPenang\tPenang\t1\nVN.20\tHo Chi Minh\tHo Chi Minh\t2\nAU.02\tNew South Wales\tNew South Wales\t3\n",
        encoding="utf-8",
    )
    (data / "countryInfo.txt").write_text(
        "# comment\nMY\tMYS\t458\tMY\tMalaysia\nVN\tVNM\t704\tVM\tVietnam\nAU\tAUS\t036\tAS\tAustralia\nNZ\tNZL\t554\tNZ\tNew Zealand\n",
        encoding="utf-8",
    )
    assert geocode.load(data) == len(CITIES)
    return data


def test_the_bundled_list_loads_with_regions_and_countries(geonames):
    town = Place.objects.get(pk=1735106)
    assert (town.name, town.region, town.country, town.population) == ("George Town", "Penang", "Malaysia", 300000)
    assert town.label == "George Town, Penang, Malaysia"
    assert Place.objects.get(pk=2155115).label == "Ngaio, New Zealand", "no region: left out"


def test_the_nearest_town_wins(geonames):
    assert geocode.nearest(5.4140, 100.3300).name == "George Town"
    assert geocode.nearest(5.3800, 100.3100).name == "Jelutong"
    assert geocode.nearest(-33.9, 151.2).name == "Sydney"


def test_far_from_any_town_there_is_no_name(geonames):
    assert geocode.nearest(0.0, -140.0) is None  # the middle of the Pacific


def test_a_town_just_beyond_the_first_box_is_still_found(geonames):
    # 0.9 degrees north of Sydney: outside +/-0.5, inside the widened box.
    assert geocode.nearest(-32.97, 151.2).name == "Sydney"


@hsettings(max_examples=150, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture])
@given(st.floats(-60, 60), st.floats(-179, 179))
def test_nearest_agrees_with_comparing_every_town(geonames, lat, lon):
    found = geocode.nearest(lat, lon)
    squeeze = max(math.cos(math.radians(lat)), 0.01)

    def d2(p):
        return (p.lat - lat) ** 2 + ((p.lon - lon) * squeeze) ** 2

    best = min(Place.objects.all(), key=d2)
    if d2(best) <= geocode.LAST_SPAN**2:
        # Within reach: exactly the brute-force answer (or one as near).
        assert found is not None and abs(d2(found) - d2(best)) < 1e-9
    elif found is not None:
        assert abs(d2(found) - d2(best)) < 1e-9


# ---------------------------------------------------------------------------
# The worker: loading once, naming, renaming after a move
# ---------------------------------------------------------------------------


def _photo(library, tags, name: str, lat: float, lon: float, **more) -> Asset:
    write(library / name, jpeg_bytes())
    tags[name] = {"File:MIMEType": "image/jpeg", "Composite:GPSLatitude": lat, "Composite:GPSLongitude": lon, **more}
    start_scan()
    drain()
    return Asset.objects.get(files__rel_path=name)


def test_the_worker_loads_places_once_then_names_items(library, tags, monkeypatch, geonames):
    Place.objects.all().delete()
    monkeypatch.setattr(geocode, "DATA", geonames)
    asset = _photo(library, tags, "penang.jpg", 5.4140, 100.3300)

    assert geocode.schedule_due() == "load"
    drain()
    assert Place.objects.count() == len(CITIES)
    assert geocode.schedule_due() == "geocode"
    drain()
    asset.refresh_from_db()
    assert asset.geo_place.name == "George Town" and asset.geocoded
    assert geocode.schedule_due() is None, "nothing left to name"


def test_a_new_position_is_named_again(library, tags, geonames):
    asset = _photo(library, tags, "trip.jpg", 5.4140, 100.3300)
    geocode.geocode([asset.pk])
    tags["trip.jpg"]["Composite:GPSLatitude"] = 10.8231
    tags["trip.jpg"]["Composite:GPSLongitude"] = 106.6297
    write(library / "trip.jpg", jpeg_bytes(color=(1, 2, 3)))  # changed on disk: re-read
    start_scan()
    drain()
    asset.refresh_from_db()
    assert asset.geo_place is None and not asset.geocoded
    geocode.geocode([asset.pk])
    asset.refresh_from_db()
    assert asset.geo_place.name == "Ho Chi Minh City"


# ---------------------------------------------------------------------------
# What the screens see
# ---------------------------------------------------------------------------


@pytest.fixture
def signed_in(settings) -> Client:
    user = get_user_model().objects.create_user(username=settings.GALLERY_USERNAME, password="x" * 20)
    client = Client()
    client.force_login(user)
    return client


def test_the_gps_place_shows_unless_the_file_names_its_own(signed_in, library, tags, geonames):
    named = _photo(library, tags, "a.jpg", 5.4140, 100.3300)
    own = _photo(library, tags, "b.jpg", 5.4140, 100.3300, **{"XMP:Location": "Cheong Fatt Tze Mansion"})
    geocode.geocode([named.pk, own.pk])
    results = {a["name"]: a for a in signed_in.get("/api/assets").json()["results"]}
    assert (results["a.jpg"]["place"], results["a.jpg"]["placeFromGps"]) == ("George Town, Penang, Malaysia", True)
    assert (results["b.jpg"]["place"], results["b.jpg"]["placeFromGps"]) == ("Cheong Fatt Tze Mansion", False)
    # Both are in Penang, whatever the second calls its spot.
    found = sorted(a["name"] for a in signed_in.get("/api/assets", {"q": "penang"}).json()["results"])
    assert found == ["a.jpg", "b.jpg"]


def test_map_points_and_the_area_they_open(signed_in, library, tags):
    _photo(library, tags, "penang.jpg", 5.4140, 100.3300)
    _photo(library, tags, "sydney.jpg", -33.8688, 151.2093)
    write(library / "nowhere.jpg", jpeg_bytes())
    start_scan()
    drain()

    body = signed_in.get("/api/map/points").json()
    assert body["count"] == 2
    ident, lat, lon, thumb = next(p for p in body["points"] if p[1] > 0)
    assert (lat, lon) == (5.414, 100.33) and thumb.startswith("/media/thumb/")

    area = signed_in.get("/api/assets", {"area": "5,100,6,101"}).json()["results"]
    assert [a["name"] for a in area] == ["penang.jpg"]
    bad = signed_in.get("/api/assets", {"area": "north"})
    assert bad.status_code == 400


def test_a_box_across_the_date_line_wraps(signed_in, library, tags):
    _photo(library, tags, "fiji.jpg", -17.7, 178.0)
    _photo(library, tags, "samoa.jpg", -13.8, -171.8)
    _photo(library, tags, "perth.jpg", -31.9, 115.9)
    names = sorted(a["name"] for a in signed_in.get("/api/assets", {"area": "-20,170,-10,-170"}).json()["results"])
    assert names == ["fiji.jpg", "samoa.jpg"]


def test_reads_need_a_session(client):
    assert client.get("/api/map/points").status_code == 403


def test_jobs_are_registered():
    from jobs.services import HANDLERS, SCHEDULERS

    assert {"load_places", "geocode"} <= set(HANDLERS)
    assert "places.services.geocode.schedule_due" in SCHEDULERS
    assert not Job.objects.exists()
