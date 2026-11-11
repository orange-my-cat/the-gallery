"""Path rules (HLD): NTFS-valid names, and nothing is ever overwritten."""

from __future__ import annotations

import pytest
from hypothesis import given, settings as hsettings, strategies as st

from fileops.services.names import InvalidName, check_folder, check_name, free_target


@pytest.mark.parametrize("name", ["IMG_0001", "Trip 2024", "été", "a.b.c", "(1)"])
def test_good_names(name):
    assert check_name(f"  {name} ") == name


@pytest.mark.parametrize(
    "name",
    ["", " ", ".", "..", "a/b", "a\\b", "a:b", "what?", "star*", 'q"uote', "pipe|", "<x>", "dot.", "dot. ",
     "CON", "con.txt", "LPT1", "nul.jpg", ".gallery-trash", "x" * 300, "tab\there"],
)
def test_bad_names(name):
    with pytest.raises(InvalidName):
        check_name(name)


def test_folder_paths():
    assert check_folder("\\2024\\Trip/") == "2024/Trip"
    assert check_folder("") == ""
    with pytest.raises(InvalidName):
        check_folder("2024/../x")
    with pytest.raises(InvalidName):
        check_folder("2024/CON/x")


def test_free_target_never_overwrites(tmp_path):
    first = tmp_path / "a.jpg"
    assert free_target(first) == first
    first.write_bytes(b"1")
    assert free_target(first).name == "a (1).jpg"
    (tmp_path / "a (1).jpg").write_bytes(b"2")
    assert free_target(first).name == "a (2).jpg"
    assert free_target(tmp_path / "a (1).jpg").name == "a (2).jpg", "no 'a (1) (1).jpg'"


@hsettings(max_examples=200)
@given(st.text(max_size=40))
def test_an_accepted_name_has_no_separator_or_traversal(name):
    try:
        clean = check_name(name)
    except InvalidName:
        return
    assert "/" not in clean and "\\" not in clean and clean not in (".", "..")
