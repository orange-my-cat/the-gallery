"""Folders as the owner organises them: `YYMMDD Title`, one per trip or period.

The owner's convention (2026-11-08):

- `180221 Penang` - a trip that *began* on 21 Feb 2018. The date is the first
  day, not the date of any particular photo.
- `179999 SMKTC` - 99 means "unknown": photos from across all of 2017.
- By the same rule `180299 X` is "sometime in February 2018".

So a folder date is never a capture time. It is used for two things only: the
Folders view (title, and the trip's span together with real photo dates), and
as the last-resort capture date for a file that states none itself - marked as
approximate wherever it is shown.

The nearest dated folder above a file wins, so a dated trip with undated
subfolders (`180221 Penang/Day 2`) still dates its files.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date

from library.services.paths import normalise_rel

_NAME = re.compile(r"^(\d{2})(\d{2})(\d{2})(?:[ _-]+(.*))?$")
_MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
_MONTHS_LONG = (
    "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
)


@dataclass(frozen=True)
class FolderDate:
    start: date
    # 'day' - a trip that began on `start`; 'month' / 'year' - sometime in it.
    precision: str
    title: str


def parse_folder_name(name: str, today: date | None = None) -> FolderDate | None:
    """`180221 Penang` -> 21 Feb 2018, day. `179999 SMKTC` -> 2017, year."""
    match = _NAME.match(name.strip())
    if not match:
        return None
    yy, mm, dd = (int(match.group(i)) for i in (1, 2, 3))
    today = today or date.today()
    # Two-digit years: this century unless that would be in the future.
    year = 2000 + yy if 2000 + yy <= today.year + 1 else 1900 + yy
    title = (match.group(4) or "").strip()
    if mm == 99:
        if dd != 99:
            return None
        return FolderDate(date(year, 1, 1), "year", title)
    if not 1 <= mm <= 12:
        return None
    if dd == 99:
        return FolderDate(date(year, mm, 1), "month", title)
    try:
        return FolderDate(date(year, mm, dd), "day", title)
    except ValueError:
        return None


def folder_date(rel_path: str) -> FolderDate | None:
    """The nearest dated folder above a file, if any."""
    parts = normalise_rel(rel_path).split("/")[:-1]
    for name in reversed(parts):
        found = parse_folder_name(name)
        if found is not None:
            return found
    return None


def short_date(d: date) -> str:
    return f"{d.day} {_MONTHS[d.month - 1]} {d.year}"


def approx_label(found: FolderDate) -> str:
    """How an undated file's date is shown: what is known, and no more."""
    if found.precision == "year":
        return f"Sometime in {found.start.year}"
    if found.precision == "month":
        return f"Sometime in {_MONTHS_LONG[found.start.month - 1]} {found.start.year}"
    return f"On or after {short_date(found.start)}"


def span_label(found: FolderDate | None, first: date | None, last: date | None) -> str:
    """A folder's dates for its card: '21–25 Feb 2018', '2017', 'from 21 Feb 2018'.

    For a trip, the start is the folder's and the end is the latest real photo
    date in it. Without a folder date, the span of real photo dates.
    """
    if found is not None and found.precision == "year":
        return str(found.start.year)
    if found is not None and found.precision == "month":
        return f"{_MONTHS_LONG[found.start.month - 1]} {found.start.year}"
    start = found.start if found is not None else first
    end = last
    if start is None:
        return ""
    if end is None or end <= start:
        return f"from {short_date(start)}" if found is not None and end is None else short_date(start)
    if start.year != end.year:
        return f"{short_date(start)} – {short_date(end)}"
    if start.month != end.month:
        return f"{start.day} {_MONTHS[start.month - 1]} – {short_date(end)}"
    return f"{start.day}–{short_date(end)}"
