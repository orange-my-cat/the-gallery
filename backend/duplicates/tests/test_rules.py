"""The merge rules (BRD) and keeper selection, including the HLD's properties."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta

from hypothesis import given, settings as hsettings, strategies as st

from duplicates.services.rules import Copy, dates_agree, merge, propose_keeper

T0 = datetime(2024, 6, 14, 10, 42, 7, tzinfo=UTC)


def dated(c: Copy, local: datetime, offset: int | None) -> Copy:
    at = local.replace(tzinfo=UTC) - timedelta(minutes=offset or 0)
    return replace(c, capture_at=at, capture_local=local, capture_offset=offset)


# ---------------------------------------------------------------------------
# Keeper selection
# ---------------------------------------------------------------------------


def test_most_pixels_wins_then_camera_original_then_name_then_oldest():
    big = Copy(1, pixels=24_000_000, size=5)
    small = Copy(2, pixels=6_000_000, size=9, camera_original=True)
    assert propose_keeper([small, big]) == (1, "Highest resolution")

    original = Copy(3, pixels=6_000_000, size=1, camera_original=True)
    export = Copy(4, pixels=6_000_000, size=9)
    assert propose_keeper([export, original]) == (3, "Still has the camera’s original details")

    named = Copy(5, pixels=1, size=2)
    copied = Copy(6, pixels=1, size=9, copy_name=True)  # bigger only by its metadata
    assert propose_keeper([copied, named]) == (5, "Has the original name, not a copy’s")

    old = Copy(7, first_seen=T0)
    new = Copy(8, first_seen=T0 + timedelta(days=1))
    assert propose_keeper([new, old])[0] == 7


def test_the_keeper_is_chosen_only_among_those_allowed():
    best, other = Copy(1, pixels=9), Copy(2, pixels=1)
    keeper, why = propose_keeper([best, other], allowed={2})
    assert keeper == 2 and "keeps every file" in why


# ---------------------------------------------------------------------------
# Field rules
# ---------------------------------------------------------------------------


def test_one_value_fills_the_gap_without_a_conflict():
    a = Copy(1, pixels=9, title="")
    b = Copy(2, title="Last light, Point Addis", lat=-38.392, lon=144.252)
    m = merge([a, b], keeper=1)
    assert m.row("title").value == "Last light, Point Addis"
    assert m.row("title").source == "only copy with a value"
    assert m.row("gps").value == (-38.392, 144.252)
    assert m.unresolved == 0


def test_different_titles_are_a_conflict_until_chosen():
    a, b = Copy(1, title="Cliffs"), Copy(2, title="Coast")
    assert merge([a, b], keeper=1).row("title").unresolved
    chosen = merge([a, b], keeper=1, choices={"title": 2})
    assert chosen.row("title").value == "Coast" and chosen.row("title").resolved
    assert chosen.unresolved == 0


def test_keywords_combine_ignoring_case_and_rating_takes_the_highest():
    a = Copy(1, pixels=9, keywords=("coast",), rating=3)
    b = Copy(2, keywords=("Coast", "sunset"), rating=0)
    c = Copy(3, keywords=("cliffs",), rating=5)
    m = merge([a, b, c], keeper=1)
    assert m.row("keywords").value == ("coast", "sunset", "cliffs")
    assert m.row("keywords").mode == "union"
    assert m.row("rating").value == 5
    assert m.unresolved == 0


def test_gps_within_100_m_agrees_and_beyond_is_a_conflict():
    a = Copy(1, lat=-38.3920, lon=144.2520)
    near = Copy(2, lat=-38.3925, lon=144.2520)  # ~55 m
    far = Copy(3, lat=-38.4000, lon=144.2520)  # ~890 m
    assert not merge([a, near], keeper=1).row("gps").unresolved
    assert merge([a, far], keeper=1).row("gps").unresolved


def test_camera_and_lens_disagreeing_is_a_conflict():
    a = Copy(1, camera_make="FUJIFILM", camera_model="X-T4")
    b = Copy(2, camera_make="Apple", camera_model="iPhone 13 Pro")
    assert merge([a, b], keeper=1).row("camera").unresolved
    same = Copy(3, camera_make="FUJIFILM", camera_model="X-T4")
    assert merge([a, same], keeper=1).row("camera").source == "all copies agree"


def test_dates_within_two_seconds_take_the_camera_original():
    local = datetime(2024, 6, 14, 18, 42, 7)
    original = dated(Copy(1, camera_original=True), local, 480)
    copy = dated(Copy(2, pixels=99), local + timedelta(seconds=1), 480)
    m = merge([original, copy], keeper=2)
    assert m.row("date").picked == (1,) and m.row("date").source == "camera original"


def test_a_whole_hour_gap_where_only_one_copy_knows_its_zone_is_resolved_by_it():
    local = datetime(2024, 6, 14, 18, 42, 7)
    zoned = dated(Copy(1), local, 480)
    # The messaging app wrote it in UTC and dropped the zone.
    lost = dated(Copy(2, pixels=9), local - timedelta(hours=8), None)
    assert dates_agree(zoned, lost)
    m = merge([zoned, lost], keeper=2)
    assert not m.row("date").unresolved and m.row("date").picked == (1,)
    # Not a whole hour: a real disagreement.
    off = dated(Copy(3), local - timedelta(hours=8, minutes=7), None)
    assert merge([zoned, off], keeper=1).row("date").unresolved


def test_albums_and_favourite_combine():
    a = Copy(1, albums=frozenset({1}), favourite=False)
    b = Copy(2, albums=frozenset({2}), favourite=True)
    m = merge([a, b], keeper=1)
    assert m.albums == {1, 2} and m.favourite is True


# ---------------------------------------------------------------------------
# HLD properties
# ---------------------------------------------------------------------------

_words = st.lists(st.sampled_from(["coast", "Coast", "sunset", "cliffs", "fog"]), max_size=3).map(tuple)
_text = st.sampled_from(["", "", "Cliffs", "Coast"])


@st.composite
def copies(draw):
    n = draw(st.integers(2, 4))
    out = []
    for i in range(n):
        c = Copy(
            id=i + 1,
            pixels=draw(st.integers(0, 3)) * 1_000_000,
            size=draw(st.integers(1, 3)),
            camera_original=draw(st.booleans()),
            first_seen=T0 + timedelta(days=draw(st.integers(0, 3))),
            lat=draw(st.sampled_from([None, -38.392, -38.3921, -37.8])),
            lon=144.252,
            camera_make=draw(st.sampled_from(["", "FUJIFILM", "Apple"])),
            camera_model="X",
            lens=draw(st.sampled_from(["", "XF 23mm"])),
            keywords=draw(_words),
            rating=draw(st.integers(0, 5)),
            title=draw(_text),
            description=draw(_text),
            place=draw(st.sampled_from(["", "Point Addis"])),
            favourite=draw(st.booleans()),
            albums=frozenset(draw(st.sets(st.integers(1, 3), max_size=2))),
        )
        if c.lat is None:
            c = replace(c, lon=None)
        if draw(st.booleans()):
            c = dated(c, datetime(2024, 6, 14, 18, 42, 7) + timedelta(seconds=draw(st.sampled_from([0, 1, 3600, 5]))),
                      draw(st.sampled_from([None, 480])))
        out.append(c)
    keeper = draw(st.integers(1, n))
    return out, keeper


@hsettings(max_examples=300)
@given(copies(), st.randoms())
def test_the_result_does_not_depend_on_the_order_of_the_copies(case, rnd):
    group, keeper = case
    shuffled = list(group)
    rnd.shuffle(shuffled)
    assert merge(group, keeper) == merge(shuffled, keeper)


@hsettings(max_examples=300)
@given(copies())
def test_every_value_survives_unless_it_was_a_conflict(case):
    group, keeper = case
    m = merge(group, keeper)
    for row in m.rows:
        if row.unresolved:
            continue
        has_any = any(t is not None for t in row.texts)
        assert (row.text is not None) == has_any, row
    words = {w.lower() for c in group for w in c.keywords}
    if not m.row("keywords").unresolved:
        assert {w.lower() for w in (m.row("keywords").value or ())} == words
    top = max(c.rating for c in group)
    assert m.row("rating").value == (top or None)


@hsettings(max_examples=300)
@given(copies())
def test_merging_twice_changes_nothing(case):
    group, keeper = case
    first = merge(group, keeper)
    if first.unresolved:
        return
    kept = next(c for c in group if c.id == keeper)
    filled = kept
    for row in first.rows:
        if row.mode == "none":
            continue
        if row.field == "date":
            at, local, offset = row.value
            filled = replace(filled, capture_at=at, capture_local=local, capture_offset=offset)
        elif row.field == "gps":
            filled = replace(filled, lat=row.value[0], lon=row.value[1])
        elif row.field == "camera":
            filled = replace(filled, camera_make=row.value[0], camera_model=row.value[1])
        elif row.field == "keywords":
            filled = replace(filled, keywords=tuple(row.value))
        else:
            filled = replace(filled, **{row.field: row.value})
    filled = replace(filled, favourite=first.favourite, albums=first.albums)
    again = merge([filled, *(c for c in group if c.id != keeper)], keeper)
    assert [(r.field, r.text) for r in again.rows] == [(r.field, r.text) for r in first.rows]
    assert (again.favourite, again.albums) == (first.favourite, first.albums)
