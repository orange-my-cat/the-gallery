"""Which files belong to the library, and that no path escapes it."""

from __future__ import annotations

import pytest
from hypothesis import HealthCheck, given, settings as hsettings, strategies as st

from core.models import AppSettings
from library.services.paths import (
    LibraryUnavailable,
    UnsafePath,
    content_type,
    file_type,
    is_excluded,
    library_root,
    normalise_rel,
    pair_key,
    scan_root,
    to_abs,
)


@pytest.mark.parametrize(
    "name,kind",
    [
        ("a/IMG_1.JPG", "photo"),
        ("a/b.heic", "photo"),
        ("x.Tiff", "photo"),
        ("raw/DSC_1.NEF", "raw"),
        ("r.cr3", "raw"),
        ("r.dng", "raw"),
        ("v/clip.MOV", "video"),
        ("v/clip.3gp", "video"),
        ("notes.txt", None),
        ("noextension", None),
        ("x.xmp", None),
    ],
)
def test_file_type(name, kind):
    assert file_type(name) == kind


@pytest.mark.parametrize(
    ("name", "mime", "kind"),
    [
        ("IMG_1.JPG", "video/quicktime", "video"),  # a Live Photo clip with the still's extension
        ("IMG_1.MOV", "image/jpeg", "photo"),  # and the still with the clip's
        ("IMG_1.JPG", "image/jpeg", "photo"),
        ("clip.mp4", "video/mp4", "video"),
        ("IMG_1.JPG", "", "photo"),  # nothing known: the extension stands
        ("IMG_1.JPG", "application/octet-stream", "photo"),
        ("DSC_1.NEF", "image/x-nikon-nef", "raw"),  # a RAW stays a RAW
        ("notes.txt", "image/jpeg", None),  # the extension still decides what is in the library
    ],
)
def test_content_type_trusts_the_bytes_over_the_name(name, mime, kind):
    assert content_type(name, mime) == kind


def test_pair_key_ignores_extension_and_case():
    assert pair_key("2024/Trip/IMG_0001.CR3") == pair_key("2024/trip/img_0001.jpg") == "2024/trip/img_0001"
    assert pair_key("top.jpg") == "/top"


def test_normalise_rel():
    assert normalise_rel("\\2024\\./Trip//") == "2024/Trip"


@pytest.mark.parametrize(
    "folder,excluded",
    [
        (".gallery-trash", True),
        (".gallery-trash/12/2024", True),
        ("Exports", True),
        ("exports/web", True),
        ("Exports-old", False),
        ("2024", False),
    ],
)
def test_exclusions(folder, excluded):
    assert is_excluded(folder, ["Exports/"]) is excluded


def test_to_abs_stays_inside(tmp_path):
    root = tmp_path / "lib"
    root.mkdir()
    assert to_abs("2024/a.jpg", root) == (root / "2024" / "a.jpg").resolve()
    for bad in ["../outside.jpg", "2024/../../x", "a\x00b"]:
        with pytest.raises(UnsafePath):
            to_abs(bad, root)


@hsettings(max_examples=300, suppress_health_check=[HealthCheck.function_scoped_fixture])
@given(st.text(alphabet=st.sampled_from(list("ab./\\:~ C")), max_size=24))
def test_no_generated_path_resolves_outside_the_root(tmp_path, candidate):
    """HLD property: no generated string ever resolves outside the root."""
    root = (tmp_path / "lib").resolve()
    root.mkdir(exist_ok=True)
    try:
        result = to_abs(candidate, root)
    except UnsafePath:
        return
    assert result == root or result.is_relative_to(root)


def test_library_root_must_be_set_and_exist(settings, tmp_path):
    settings.LIBRARY_ROOT = None
    with pytest.raises(LibraryUnavailable):
        library_root()
    settings.LIBRARY_ROOT = tmp_path / "nope"
    with pytest.raises(LibraryUnavailable):
        library_root()


@pytest.mark.django_db
def test_scan_root_follows_the_subfolder_setting(settings, tmp_path):
    settings.LIBRARY_ROOT = tmp_path
    (tmp_path / "Photos").mkdir()
    assert scan_root() == tmp_path
    AppSettings.objects.update_or_create(pk=1, defaults={"library_subfolder": "Photos"})
    assert scan_root() == (tmp_path / "Photos").resolve()
    AppSettings.objects.filter(pk=1).update(library_subfolder="Missing")
    with pytest.raises(LibraryUnavailable):
        scan_root()
    AppSettings.objects.filter(pk=1).update(library_subfolder="../escape")
    with pytest.raises(UnsafePath):
        scan_root()
