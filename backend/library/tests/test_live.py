"""Live Photos: a still and its motion clip become one item, either way round."""

from __future__ import annotations

import importlib

import pytest
from django.apps import apps

from jobs.models import Job
from library.models import Asset, FileRole, FileState, MediaFile
from library.services import thumbnails
from library.services.browse import present_asset, with_details
from library.services.scan import start_scan
from organise.models import Album, AlbumItem

from .conftest import drain, jpeg_bytes, write

pytestmark = pytest.mark.django_db

UUID = "8118A98E-4F9E-4911-95A5-CC6F93DEB182"
CLIP = b"\x00\x00\x00\x14ftypqt  a clip"


@pytest.fixture(autouse=True)
def no_ffmpeg(monkeypatch):
    monkeypatch.setattr(thumbnails, "_open_video", lambda path: thumbnails.Image.new("RGB", (64, 48)))


def scan() -> None:
    start_scan()
    drain()


def still_tags(live_id: str | None = UUID) -> dict:
    tags = {"File:MIMEType": "image/heic"}
    if live_id:
        tags["MakerNotes:MediaGroupUUID"] = live_id
    return tags


def clip_tags(live_id: str | None = UUID, seconds: float = 2.9) -> dict:
    tags = {"File:MIMEType": "video/quicktime", "QuickTime:Duration": seconds}
    if live_id:
        tags["QuickTime:ContentIdentifier"] = live_id.lower()  # case differs between the halves
    return tags


def dto(asset: Asset) -> dict:
    found = present_asset(with_details(Asset.objects.filter(pk=asset.pk)).get())
    assert found is not None
    return found


@pytest.mark.parametrize("clip_first", [True, False])
def test_halves_named_apart_become_one_photo_either_way_round(library, tags, clip_first):
    tags["IMG_4570.HEIC"] = still_tags()
    tags["IMG_4570_a.MOV"] = clip_tags()
    still, clip = (library / "trip" / "IMG_4570.HEIC", jpeg_bytes()), (library / "trip" / "IMG_4570_a.MOV", CLIP)
    first, second = (clip, still) if clip_first else (still, clip)
    write(*first)
    scan()
    write(*second)
    scan()

    asset = Asset.objects.get()
    assert asset.kind == "photo"
    assert asset.primary_file.rel_path == "trip/IMG_4570.HEIC"
    roles = dict(asset.files.values_list("rel_path", "role"))
    assert roles == {"trip/IMG_4570.HEIC": FileRole.PRIMARY, "trip/IMG_4570_a.MOV": FileRole.LIVE_VIDEO}
    motion = MediaFile.objects.get(role=FileRole.LIVE_VIDEO)
    assert dto(asset)["liveUrl"] == f"/media/original/{motion.pk}"
    assert not Job.objects.filter(payload__file=motion.pk).exists(), "a clip needs no thumbnails"


def test_same_name_and_short_pairs_without_an_identifier(library, tags):
    tags["IMG_2895.MOV"] = {"File:MIMEType": "image/jpeg"}  # the swapped halves, tags lost
    tags["IMG_2895.JPG"] = clip_tags(live_id=None)
    write(library / "IMG_2895.MOV", jpeg_bytes())
    write(library / "IMG_2895.JPG", CLIP)
    scan()
    assert Asset.objects.count() == 1
    assert MediaFile.objects.get(rel_path="IMG_2895.JPG").role == FileRole.LIVE_VIDEO


def test_a_long_video_with_a_photos_name_stays_a_video(library, tags):
    tags["DSC_0001.JPG"] = still_tags(live_id=None)
    tags["DSC_0001.MOV"] = clip_tags(live_id=None, seconds=45)
    write(library / "DSC_0001.JPG", jpeg_bytes())
    write(library / "DSC_0001.MOV", CLIP)
    scan()
    assert sorted(Asset.objects.values_list("kind", flat=True)) == ["photo", "video"]


def test_different_identifiers_never_pair_even_with_one_name(library, tags):
    tags["IMG_1.HEIC"] = still_tags("AAAA")
    tags["IMG_1.MOV"] = clip_tags("BBBB")
    write(library / "IMG_1.HEIC", jpeg_bytes())
    write(library / "IMG_1.MOV", CLIP)
    scan()
    assert Asset.objects.count() == 2


def test_halves_in_different_folders_do_not_pair(library, tags):
    tags["IMG_1.HEIC"] = still_tags()
    tags["IMG_1_a.MOV"] = clip_tags()
    write(library / "a" / "IMG_1.HEIC", jpeg_bytes())
    write(library / "b" / "IMG_1_a.MOV", CLIP)
    scan()
    assert Asset.objects.count() == 2


def test_a_clip_the_owner_has_put_in_an_album_is_not_folded_away(library, tags):
    tags["IMG_1.MOV"] = clip_tags()
    write(library / "IMG_1.MOV", CLIP)
    scan()
    album = Album.objects.create(name="Clips")
    AlbumItem.objects.create(album=album, asset=Asset.objects.get())

    tags["IMG_1.HEIC"] = still_tags()
    write(library / "IMG_1.HEIC", jpeg_bytes())
    scan()

    assert Asset.objects.count() == 2
    assert not MediaFile.objects.filter(role=FileRole.LIVE_VIDEO).exists()


def test_one_clip_per_still(library, tags):
    tags["IMG_1.HEIC"] = still_tags()
    tags["IMG_1.MOV"] = clip_tags()
    tags["IMG_1_b.MOV"] = clip_tags()  # a copy of the clip
    write(library / "IMG_1.HEIC", jpeg_bytes())
    write(library / "IMG_1.MOV", CLIP)
    write(library / "IMG_1_b.MOV", CLIP + b" copy")
    scan()
    assert MediaFile.objects.filter(role=FileRole.LIVE_VIDEO).count() == 1
    assert Asset.objects.count() == 2  # the photo, and the spare copy as a video


def test_the_photo_is_missing_by_its_still_and_plays_only_a_present_clip(library, tags):
    tags["IMG_1.HEIC"] = still_tags()
    tags["IMG_1.MOV"] = clip_tags()
    write(library / "IMG_1.HEIC", jpeg_bytes())
    write(library / "IMG_1.MOV", CLIP)
    scan()
    asset = Asset.objects.get()

    MediaFile.objects.filter(role=FileRole.LIVE_VIDEO).update(state=FileState.MISSING)
    shown = dto(asset)
    assert (shown["missing"], shown["liveUrl"]) == (False, None)

    MediaFile.objects.update(state=FileState.MISSING)
    MediaFile.objects.filter(role=FileRole.LIVE_VIDEO).update(state=FileState.PRESENT)
    assert dto(asset)["missing"] is True


def test_trashing_the_photo_takes_its_clip(library, tags):
    from fileops.services import ops

    tags["IMG_1.HEIC"] = still_tags()
    tags["IMG_1.MOV"] = clip_tags()
    write(library / "IMG_1.HEIC", jpeg_bytes())
    write(library / "IMG_1.MOV", CLIP)
    scan()

    ops.trash([Asset.objects.get().pk])
    drain()

    assert set(MediaFile.objects.values_list("state", flat=True)) == {FileState.TRASHED}
    assert not (library / "IMG_1.MOV").exists()


def test_migration_links_the_pairs_already_indexed(library, tags):
    migration = importlib.import_module("library.migrations.0007_link_live_photos")
    tags["IMG_1.HEIC"] = still_tags()
    tags["IMG_1_a.MOV"] = clip_tags()
    tags["IMG_2.JPG"] = still_tags(live_id=None)
    tags["IMG_2.MOV"] = clip_tags(live_id=None)
    tags["LONG.JPG"] = still_tags(live_id=None)
    tags["LONG.MOV"] = clip_tags(live_id=None, seconds=30)
    for name, data in [("IMG_1.HEIC", jpeg_bytes(color=(1, 1, 1))), ("IMG_1_a.MOV", CLIP),
                       ("IMG_2.JPG", jpeg_bytes(color=(2, 2, 2))), ("IMG_2.MOV", CLIP + b"2"),
                       ("LONG.JPG", jpeg_bytes(color=(3, 3, 3))), ("LONG.MOV", CLIP + b"3")]:
        write(library / name, data)
    scan()
    # As the indexer left them before Live Photos: every file its own item, no identifiers.
    for media in MediaFile.objects.filter(role=FileRole.LIVE_VIDEO):
        own = Asset.objects.create(kind="video", capture_at=media.asset.capture_at, capture_source="file_time",
                                   duration=2.9, primary_file=media)
        MediaFile.objects.filter(pk=media.pk).update(asset=own, role=FileRole.PRIMARY)
    MediaFile.objects.update(live_id="")
    assert Asset.objects.count() == 6

    migration.forwards(apps, None)

    assert Asset.objects.count() == 4
    live = dict(MediaFile.objects.filter(role=FileRole.LIVE_VIDEO).values_list("rel_path", "asset__primary_file__rel_path"))
    assert live == {"IMG_1_a.MOV": "IMG_1.HEIC", "IMG_2.MOV": "IMG_2.JPG"}
    assert MediaFile.objects.get(rel_path="IMG_1.HEIC").live_id == UUID
