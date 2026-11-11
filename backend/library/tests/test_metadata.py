"""Which tag wins, for each field the Asset carries (MET-01, BRW-05)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone

import pytest

from library.services.metadata import normalise, parse_datetime, parse_offset

MTIME = datetime(2020, 1, 2, 3, 4, 5, tzinfo=UTC)
ZONE = "Asia/Kuala_Lumpur"  # UTC+8, no daylight saving


def norm(raw: dict):
    return normalise(raw, file_mtime=MTIME, zone=ZONE)


class TestCaptureTime:
    def test_original_date_with_its_offset_tag(self):
        n = norm({"EXIF:DateTimeOriginal": "2024:07:14 18:30:00", "EXIF:OffsetTimeOriginal": "+02:00"})
        assert n.capture_at == datetime(2024, 7, 14, 16, 30, tzinfo=UTC)
        assert n.capture_offset == 120
        assert n.capture_source == "metadata"

    def test_without_an_offset_the_wall_clock_is_read_in_the_app_zone(self):
        n = norm({"EXIF:DateTimeOriginal": "2024:07:14 18:30:00"})
        assert n.capture_at == datetime(2024, 7, 14, 10, 30, tzinfo=UTC)
        assert n.capture_offset is None

    def test_subsecond_composite_wins_and_keeps_fraction(self):
        n = norm(
            {
                "Composite:SubSecDateTimeOriginal": "2024:07:14 18:30:00.25+08:00",
                "EXIF:DateTimeOriginal": "2001:01:01 00:00:00",
            }
        )
        assert n.capture_at == datetime(2024, 7, 14, 10, 30, 0, 250_000, tzinfo=UTC)
        assert n.capture_offset == 480

    def test_original_beats_create_date(self):
        n = norm({"EXIF:CreateDate": "2010:01:01 00:00:00", "XMP:DateTimeOriginal": "2011:01:01 00:00:00"})
        assert n.capture_at.year == 2011

    def test_apple_creation_date_states_its_offset(self):
        n = norm(
            {"QuickTime:CreationDate": "2023:05:01 09:00:00+10:00", "QuickTime:CreateDate": "2023:04:30 23:00:00"}
        )
        assert n.capture_at == datetime(2023, 4, 30, 23, 0, tzinfo=UTC)
        assert n.capture_offset == 600

    def test_quicktime_create_date_is_utc(self):
        n = norm({"QuickTime:CreateDate": "2023:04:30 23:00:00"})
        assert n.capture_at == datetime(2023, 4, 30, 23, 0, tzinfo=UTC)
        assert n.capture_offset is None

    @pytest.mark.parametrize(
        "value", ["0000:00:00 00:00:00", "1904:01:01 00:00:00", "1970:01:01 00:00:00", "garbage", "", None, 7]
    )
    def test_unset_camera_clocks_fall_back_to_the_file_time(self, value):
        n = norm({"EXIF:DateTimeOriginal": value})
        assert n.capture_at == MTIME
        assert n.capture_source == "file_time"

    @pytest.mark.parametrize(
        "name,expected",
        [
            ("IMG_20170608_084734_01.jpg", datetime(2017, 6, 8, 8, 47, 34)),
            ("PXL_20250302_101500123.jpg", datetime(2025, 3, 2, 10, 15, 0)),  # milliseconds glued on
            ("VID_20190203_101112.mp4", datetime(2019, 2, 3, 10, 11, 12)),
            ("Screenshot_2019-02-03-10-11-12.png", datetime(2019, 2, 3, 10, 11, 12)),
            ("2019-02-03 10.11.12.jpg", datetime(2019, 2, 3, 10, 11, 12)),
            ("IMG-20170608-WA0001.jpg", datetime(2017, 6, 8, 12, 0, 0)),
            ("folder 20170608/x.jpg", None),  # only the file's own name counts
            ("180221 Penang.jpg", None),  # two-digit year: ambiguous
            ("28309909_1813600985369682_1790629136_o.jpg", None),
            ("IMG_20171332_101010.jpg", None),  # month 13
            ("DSC_0001.JPG", None),
        ],
    )
    def test_dates_in_file_names(self, name, expected):
        from library.services.metadata import name_datetime

        assert name_datetime(name) == expected

    def test_file_name_beats_file_time_but_not_metadata(self):
        named = normalise({}, file_mtime=MTIME, zone=ZONE, name="2017/IMG_20170608_084734.jpg")
        assert named.capture_source == "file_name"
        assert named.capture_at == datetime(2017, 6, 8, 0, 47, 34, tzinfo=UTC)
        tagged = normalise(
            {"EXIF:DateTimeOriginal": "2016:01:01 00:00:00"}, file_mtime=MTIME, zone=ZONE, name="IMG_20170608_084734.jpg"
        )
        assert tagged.capture_source == "metadata"

    def test_impossible_date_is_rejected(self):
        assert parse_datetime("2023:02:30 10:00:00") is None

    def test_offset_parsing(self):
        assert parse_offset("+05:30") == 330
        assert parse_offset("-0300") == -180
        assert parse_offset("Z") == 0
        assert parse_offset("+15:00") is None
        assert parse_offset(None) is None


class TestGps:
    def test_composite_is_already_signed(self):
        n = norm({"Composite:GPSLatitude": -33.86, "Composite:GPSLongitude": 151.21})
        assert (n.lat, n.lon) == (-33.86, 151.21)

    def test_bare_exif_takes_its_hemisphere_from_the_ref(self):
        n = norm(
            {
                "EXIF:GPSLatitude": 33.86,
                "EXIF:GPSLatitudeRef": "S",
                "EXIF:GPSLongitude": 70.6,
                "EXIF:GPSLongitudeRef": "W",
            }
        )
        assert (n.lat, n.lon) == (-33.86, -70.6)

    @pytest.mark.parametrize(
        "lat,lon", [(0, 0), (91, 10), (10, 181), ("x", 1)]
    )
    def test_null_island_and_nonsense_are_dropped(self, lat, lon):
        n = norm({"Composite:GPSLatitude": lat, "Composite:GPSLongitude": lon})
        assert (n.lat, n.lon) == (None, None)


class TestDimensions:
    def test_image_size(self):
        n = norm({"Composite:ImageSize": "4032 3024"})
        assert (n.width, n.height) == (4032, 3024)

    def test_portrait_orientation_swaps(self):
        n = norm({"Composite:ImageSize": "4032x3024", "EXIF:Orientation": 6})
        assert (n.width, n.height) == (3024, 4032)

    def test_rotated_video_swaps(self):
        n = norm({"QuickTime:ImageWidth": 1920, "QuickTime:ImageHeight": 1080, "Composite:Rotation": 90})
        assert (n.width, n.height) == (1080, 1920)

    def test_nothing_known(self):
        assert (norm({}).width, norm({}).height) == (None, None)


class TestDescriptiveFields:
    def test_camera_and_exposure(self):
        n = norm(
            {
                "EXIF:Make": "FUJIFILM ",
                "EXIF:Model": "X-T5",
                "EXIF:LensModel": "XF23mmF1.4 R LM WR",
                "EXIF:ExposureTime": 0.004,
                "EXIF:FNumber": 2.8,
                "EXIF:ISO": 400,
                "EXIF:FocalLength": 23,
            }
        )
        assert n.camera_make == "FUJIFILM"
        assert n.camera_model == "X-T5"
        assert n.lens == "XF23mmF1.4 R LM WR"
        assert (n.exposure_time, n.f_number, n.iso, n.focal_length) == (0.004, 2.8, 400, 23.0)

    def test_numeric_lens_id_is_not_a_lens_name(self):
        assert norm({"Composite:LensID": "368"}).lens == ""
        assert norm({"Composite:LensID": "Canon EF 50mm f/1.8"}).lens == "Canon EF 50mm f/1.8"

    def test_camera_boilerplate_is_not_a_description(self):
        assert norm({"EXIF:ImageDescription": "OLYMPUS DIGITAL CAMERA"}).description == ""
        assert norm({"XMP:Description": "Sunset at the pier"}).description == "Sunset at the pier"

    def test_xmp_bag_takes_first_non_empty(self):
        assert norm({"XMP:Title": ["", "Harbour"]}).title == "Harbour"

    @pytest.mark.parametrize("value,expected", [(5, 5), (3, 3), (-1, 0), (0, 0), (9, 0), ("x", 0)])
    def test_rating(self, value, expected):
        assert norm({"XMP:Rating": value}).rating == expected

    def test_video_duration(self):
        assert norm({"QuickTime:Duration": 12.5}).duration == 12.5
        assert norm({"QuickTime:Duration": 0}).duration is None


def test_fixed_offset_zone_round_trips():
    n = norm({"EXIF:DateTimeOriginal": "2024:01:01 00:00:00", "EXIF:OffsetTimeOriginal": "-05:00"})
    assert n.capture_at.utcoffset() == timedelta(hours=-5)
    assert n.capture_at.astimezone(timezone.utc).hour == 5


class TestLivePhotoIdentifier:
    @pytest.mark.parametrize(
        "tag",
        ["QuickTime:ContentIdentifier", "Keys:ContentIdentifier", "MakerNotes:MediaGroupUUID", "MakerNotes:ContentIdentifier"],
    )
    def test_read_from_wherever_the_half_keeps_it(self, tag):
        assert norm({tag: "8118a98e-4f9e-4911-95a5-cc6f93deb182"}).live_id == "8118A98E-4F9E-4911-95A5-CC6F93DEB182"

    def test_absent(self):
        assert norm({}).live_id == ""
