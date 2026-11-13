"""The merge rules (BRD, "Duplicate merge rules") as a pure function.

`merge(copies, keeper, choices)` says, field by field, what the kept copy ends
up with and where each value came from, and which fields the copies genuinely
disagree on. Nothing here reads the database or a file: the review screen
calls it to preview, the merge calls it again to decide what to write, and the
property tests call it with whatever Hypothesis invents.

Copies are always considered in rank order (keeper selection's order), never
in the order given, so the result does not depend on how they were listed.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

# BRD: dates within 2 seconds agree; positions within 100 m agree.
DATE_TOLERANCE = timedelta(seconds=2)
GPS_TOLERANCE_M = 100.0
# The largest real UTC offset is 14 hours.
_MAX_OFFSET = timedelta(hours=14)

FIELDS: tuple[tuple[str, str], ...] = (
    ("date", "Capture date"),
    ("gps", "GPS"),
    ("camera", "Camera"),
    ("lens", "Lens"),
    ("keywords", "Keywords"),
    ("rating", "Rating"),
    ("title", "Title"),
    ("description", "Description"),
    ("place", "Location"),
)
FIELD_NAMES = frozenset(name for name, _ in FIELDS)


@dataclass(frozen=True)
class Copy:
    """One copy's side of a merge: what it holds, and what ranks it."""

    id: int
    pixels: int = 0
    size: int = 0
    # Make, Model and DateTimeOriginal all present: not re-exported (BRD).
    camera_original: bool = False
    # Named like a copy: "IMG_1 (1).jpg", "IMG_1 - Copy.jpg".
    copy_name: bool = False
    # The file's modification time; the oldest file wins the last tie.
    first_seen: datetime | None = None
    # A capture date the file states (or the owner set), as an instant, the
    # wall-clock time it was taken at, and the UTC offset in minutes if known.
    # None when the date was only guessed from a name, a folder or the clock.
    capture_at: datetime | None = None
    capture_local: datetime | None = None
    capture_offset: int | None = None
    lat: float | None = None
    lon: float | None = None
    camera_make: str = ""
    camera_model: str = ""
    lens: str = ""
    keywords: tuple[str, ...] = ()
    rating: int = 0
    title: str = ""
    description: str = ""
    place: str = ""
    favourite: bool = False
    albums: frozenset[int] = frozenset()


@dataclass(frozen=True)
class Row:
    field: str
    label: str
    # Per copy, in the result's copy order: the value shown, None if it has none.
    texts: tuple[str | None, ...]
    # 'single' (one copy's value), 'union' (combined), 'none' (no copy has a
    # value) or 'unresolved' (a conflict the owner has not settled).
    mode: str
    picked: tuple[int, ...]  # copy ids whose value is used
    value: Any  # the merged value, in the field's own form
    text: str | None  # the merged value as shown
    source: str  # 'all copies agree', 'camera original', 'your choice', ...
    conflict: bool  # the copies disagree (whether or not it is settled)

    @property
    def unresolved(self) -> bool:
        return self.mode == "unresolved"

    @property
    def resolved(self) -> bool:
        return self.conflict and self.mode != "unresolved"


@dataclass(frozen=True)
class Merge:
    copies: tuple[Copy, ...]  # rank order: the order the screen letters them
    keeper: int
    rows: tuple[Row, ...]
    favourite: bool
    albums: frozenset[int] = field(default_factory=frozenset)

    @property
    def unresolved(self) -> int:
        return sum(1 for r in self.rows if r.unresolved)

    def row(self, name: str) -> Row:
        return next(r for r in self.rows if r.field == name)


# ---------------------------------------------------------------------------
# Keeper selection
# ---------------------------------------------------------------------------

_LATEST = datetime.max


def rank_key(c: Copy) -> tuple:
    """BRD keeper rules in order: most pixels, camera original, then -
    standing in for "inside the organised folder pattern" until FOP-04 sets
    one - a name that is not a copy's, then the oldest file.

    The BRD's "larger file, less compression" rule is left out: duplicates are
    identical copies now (owner's rule), so a size difference is only metadata.
    """
    seen = c.first_seen.replace(tzinfo=None) if c.first_seen is not None else _LATEST
    return (-c.pixels, not c.camera_original, c.copy_name, seen, c.id)


def ranked(copies: list[Copy] | tuple[Copy, ...]) -> tuple[Copy, ...]:
    return tuple(sorted(copies, key=rank_key))


def propose_keeper(copies, allowed: set[int] | None = None) -> tuple[int, str]:
    """(keeper id, why) - the best-ranked copy among those allowed to keep."""
    order = ranked(copies)
    candidates = [c for c in order if allowed is None or c.id in allowed]
    if not candidates:
        raise ValueError("no copy can be kept")
    best = candidates[0]
    rest = [c for c in order if c.id != best.id]
    if not rest:
        return best.id, "The only copy"
    if allowed is not None and best.id != order[0].id:
        return best.id, "The only copy that keeps every file of the others"
    if best.pixels > max(c.pixels for c in rest):
        return best.id, "Highest resolution"
    same = [c for c in rest if c.pixels == best.pixels]
    if best.camera_original and any(not c.camera_original for c in same):
        return best.id, "Still has the camera’s original details"
    same = [c for c in same if c.camera_original == best.camera_original]
    if not best.copy_name and any(c.copy_name for c in same):
        return best.id, "Has the original name, not a copy’s"
    return best.id, "The oldest file"


# ---------------------------------------------------------------------------
# Field rules
# ---------------------------------------------------------------------------


def _clean(text: str) -> str:
    return " ".join(text.split())


def camera_text(make: str, model: str) -> str:
    """'Canon' + 'Canon EOS R5' -> 'Canon EOS R5'; 'FUJIFILM' + 'X-T5' -> 'FUJIFILM X-T5'."""
    make, model = _clean(make), _clean(model)
    if not model:
        return make
    first = make.split(" ")[0].lower() if make else ""
    return model if not first or model.lower().startswith(first) else f"{make} {model}"


def _offset_text(minutes: int | None) -> str:
    if minutes is None:
        return ""
    sign = "-" if minutes < 0 else "+"
    return f" {sign}{abs(minutes) // 60:02d}:{abs(minutes) % 60:02d}"


def date_text(c: Copy) -> str | None:
    if c.capture_local is None:
        return None
    return c.capture_local.strftime("%Y-%m-%d %H:%M:%S") + _offset_text(c.capture_offset)


def dates_agree(a: Copy, b: Copy) -> bool:
    """BRD: within 2 seconds - or a whole-hour difference where only one copy
    has an offset (a copy that lost its zone, usually written in UTC)."""
    if a.capture_local is None or b.capture_local is None or a.capture_at is None or b.capture_at is None:
        return True
    if a.capture_offset is not None and b.capture_offset is not None:
        return abs(a.capture_at - b.capture_at) <= DATE_TOLERANCE
    gap = abs(a.capture_local - b.capture_local)
    if gap <= DATE_TOLERANCE:
        return True
    if (a.capture_offset is None) == (b.capture_offset is None):
        return False  # neither has an offset: their clocks simply differ
    hours = round(gap / timedelta(hours=1))
    return 0 < hours and timedelta(hours=hours) <= _MAX_OFFSET and abs(gap - timedelta(hours=hours)) <= DATE_TOLERANCE


def _metres(a: Copy, b: Copy) -> float:
    assert a.lat is not None and a.lon is not None and b.lat is not None and b.lon is not None
    p1, p2 = math.radians(a.lat), math.radians(b.lat)
    dp, dl = p2 - p1, math.radians(b.lon - a.lon)
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * 6_371_000 * math.asin(min(1.0, math.sqrt(h)))


def gps_text(c: Copy) -> str | None:
    if c.lat is None or c.lon is None:
        return None
    return f"{c.lat:.4f}°, {c.lon:.4f}°".replace("-", "−")


def _stars(n: int) -> str:
    return "★" * n + "☆" * (5 - n)


def _keywords(c: Copy) -> tuple[str, ...]:
    seen: dict[str, str] = {}
    for word in c.keywords:
        clean = _clean(word)
        if clean and clean.lower() not in seen:
            seen[clean.lower()] = clean
    return tuple(seen.values())


def _text_of(name: str, c: Copy) -> str | None:
    if name == "date":
        return date_text(c)
    if name == "gps":
        return gps_text(c)
    if name == "camera":
        return camera_text(c.camera_make, c.camera_model) or None
    if name == "lens":
        return _clean(c.lens) or None
    if name == "keywords":
        words = _keywords(c)
        return ", ".join(words) if words else None
    if name == "rating":
        return _stars(c.rating) if 0 < c.rating <= 5 else None
    value = _clean(getattr(c, name))
    return value or None


def _value_of(name: str, c: Copy) -> Any:
    if name == "date":
        return (c.capture_at, c.capture_local, c.capture_offset)
    if name == "gps":
        return (c.lat, c.lon)
    if name == "camera":
        return (_clean(c.camera_make), _clean(c.camera_model))
    if name == "keywords":
        return _keywords(c)
    if name == "rating":
        return c.rating
    return _clean(getattr(c, name))


def _row(name: str, label: str, copies: tuple[Copy, ...], keeper: Copy, choice: int | None) -> Row:
    texts = tuple(_text_of(name, c) for c in copies)
    having = [c for c, t in zip(copies, texts, strict=True) if t is not None]
    # Ties go to the keeper, then by rank.
    preferred = sorted(having, key=lambda c: (c.id != keeper.id, rank_key(c)))
    distinct = {(_text_of(name, c) or "").lower() for c in having}

    def single(c: Copy, source: str, conflict: bool) -> Row:
        return Row(name, label, texts, "single", (c.id,), _value_of(name, c), _text_of(name, c), source, conflict)

    # Do the copies genuinely disagree?
    if name == "date":
        conflict = any(not dates_agree(a, b) for i, a in enumerate(having) for b in having[i + 1:])
    elif name == "gps":
        conflict = any(_metres(a, b) > GPS_TOLERANCE_M for i, a in enumerate(having) for b in having[i + 1:])
    elif name in ("camera", "lens", "title", "description"):
        conflict = len(distinct) > 1
    else:  # keywords, rating, place: combined or chosen by rule, never asked
        conflict = False

    chosen = next((c for c in having if c.id == choice), None)
    if chosen is not None:
        return single(chosen, "your choice" if conflict else "chosen by you", conflict)
    if not having:
        return Row(name, label, texts, "none", (), None, None, "No copy has a value", False)
    if conflict:
        return Row(name, label, texts, "unresolved", (), None, None, "Needs your choice", True)

    all_agree = len(having) == len(copies) and len(distinct) == 1
    if name == "keywords" and len(distinct) > 1:
        merged: dict[str, str] = {}
        for c in preferred:
            for word in _keywords(c):
                merged.setdefault(word.lower(), word)
        words = tuple(merged.values())
        return Row(name, label, texts, "union", tuple(c.id for c in having), words, ", ".join(words), "combined", False)
    if name == "rating":
        top = max(c.rating for c in having)
        best = next(c for c in preferred if c.rating == top)
        return single(best, "all copies agree" if all_agree else "highest", False)
    if name == "date" and len(distinct) > 1:
        # Agreeing within the rules but not to the second: the camera's own,
        # then one that knows its time zone, then the earliest.
        def by_rule(c: Copy) -> tuple:
            assert c.capture_at is not None
            return (not c.camera_original, c.capture_offset is None, c.capture_at, rank_key(c))

        best = min(having, key=by_rule)
        return single(best, "camera original" if best.camera_original else "earliest", False)
    if name == "gps" and len(distinct) > 1:
        best = min(preferred, key=lambda c: not c.camera_original)
        return single(best, "camera original" if best.camera_original else "closest match", False)
    if all_agree:
        return single(preferred[0], "all copies agree", False)
    if len(having) == 1:
        return single(having[0], "only copy with a value", False)
    return single(preferred[0], "kept copy’s value" if preferred[0].id == keeper.id else "copies agree", False)


def merge(copies, keeper: int, choices: dict[str, int] | None = None) -> Merge:
    """The merged result of `copies` kept as `keeper`, with the owner's
    `choices` ({field: copy id}) settling conflicts or overriding a rule."""
    order = ranked(copies)
    by_id = {c.id: c for c in order}
    if keeper not in by_id:
        raise ValueError("the keeper is not one of the copies")
    choices = choices or {}
    rows = tuple(_row(name, label, order, by_id[keeper], choices.get(name)) for name, label in FIELDS)
    return Merge(
        copies=order,
        keeper=keeper,
        rows=rows,
        favourite=any(c.favourite for c in order),
        albums=frozenset().union(*(c.albums for c in order)),
    )
