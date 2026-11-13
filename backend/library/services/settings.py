"""The settings screen (LIB-01): read, validate and save AppSettings.

Changing what the library *is* - the folder, or the exclusions - starts a scan
straight away, so the timeline matches the new choice without anyone having to
remember the Rescan button. The other settings take effect on their own.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

from django.db import transaction

from core.models import AppSettings, WriteBackMode
from core.services.exceptions import BusinessRuleError
from library.models import ScanTrigger
from library.services.browse import display_path, display_root, library_available
from library.services.paths import (
    TRASH_DIR,
    LibraryUnavailable,
    UnsafePath,
    library_root,
    must_choose_folder,
    normalise_rel,
    to_abs,
)
from library.services.scan import start_scan

# 0 turns the schedule off; otherwise no busier than every 5 minutes, and at
# least daily.
MIN_INTERVAL, MAX_INTERVAL = 5, 24 * 60


class InvalidSetting(BusinessRuleError):
    code = "validation_failed"


def root_available() -> bool:
    try:
        library_root()
    except LibraryUnavailable:
        return False
    return True


def present(row: AppSettings | None = None) -> dict[str, Any]:
    row = row or AppSettings.load()
    sub = normalise_rel(row.library_subfolder)
    return {
        # What can be browsed: LIBRARY_ROOT, fixed by .env and the container's
        # mounts. Shown, not edited, here.
        "libraryRoot": display_root() if root_available() else None,
        # The library folder itself, or None when there is none (yet).
        "libraryPath": display_path(sub) if library_available() else None,
        # True where whole drives are mounted: a folder must be chosen.
        "mustChoose": must_choose_folder(),
        "librarySubfolder": sub,
        "exclusions": list(row.exclusions or []),
        "scanIntervalMinutes": row.scan_interval_minutes,
        "trashRetentionDays": row.trash_retention_days,
        "writeBackMode": row.write_back_mode,
        "autoMergeExact": row.auto_merge_exact,
    }


@dataclass(frozen=True)
class Saved:
    settings: dict[str, Any]
    scan_started: bool


def _folder(value: str, field: str) -> str:
    rel = normalise_rel(value)
    if any(part == ".." for part in rel.split("/")):
        raise InvalidSetting("A folder inside the library, without '..'.", field=field)
    if rel.lower() == TRASH_DIR or rel.lower().startswith(TRASH_DIR + "/"):
        raise InvalidSetting("The trash folder is managed by The Gallery.", field=field)
    try:
        to_abs(rel, library_root())
    except UnsafePath as exc:
        raise InvalidSetting(exc.message, field=field) from exc
    return rel


def save(data: dict[str, Any]) -> Saved:
    """Validate everything first, then save; a refused request changes nothing."""
    row = AppSettings.load()
    before = (normalise_rel(row.library_subfolder), list(row.exclusions or []))
    library_ok = root_available()

    if "librarySubfolder" in data:
        if not library_ok:
            raise InvalidSetting(
                "There is no library to choose a folder in: LIBRARY_ROOT in .env is missing or unreadable.",
                field="librarySubfolder",
            )
        sub = _folder(str(data["librarySubfolder"] or ""), "librarySubfolder")
        if not sub and must_choose_folder():
            raise InvalidSetting("Choose a folder for the library.", field="librarySubfolder")
        if sub and not to_abs(sub, library_root()).is_dir():
            raise InvalidSetting(f"There is no folder at {display_path(sub)}.", field="librarySubfolder")
        row.library_subfolder = sub

    if "exclusions" in data:
        raw = data["exclusions"]
        if not isinstance(raw, list) or not all(isinstance(x, str) for x in raw):
            raise InvalidSetting("A list of folder names.", field="exclusions")
        # Relative to the library folder, as the owner names them.
        cleaned: list[str] = []
        for item in raw:
            if not item.strip():
                continue
            rel = _relative(item, "exclusions")
            if rel and rel.lower() not in {c.lower() for c in cleaned}:
                cleaned.append(rel)
        row.exclusions = cleaned

    if "scanIntervalMinutes" in data:
        n = _int(data["scanIntervalMinutes"], "scanIntervalMinutes")
        if n != 0 and not MIN_INTERVAL <= n <= MAX_INTERVAL:
            raise InvalidSetting(
                f"Every {MIN_INTERVAL} minutes at most often, once a day at least - or off.",
                field="scanIntervalMinutes",
            )
        row.scan_interval_minutes = n

    if "trashRetentionDays" in data:
        n = _int(data["trashRetentionDays"], "trashRetentionDays")
        if not 1 <= n <= 365:
            raise InvalidSetting("Between 1 and 365 days.", field="trashRetentionDays")
        row.trash_retention_days = n

    if "writeBackMode" in data:
        if data["writeBackMode"] not in WriteBackMode.values:
            raise InvalidSetting("Choose embedded or sidecar.", field="writeBackMode")
        row.write_back_mode = data["writeBackMode"]

    if "autoMergeExact" in data:
        if not isinstance(data["autoMergeExact"], bool):
            raise InvalidSetting("On or off.", field="autoMergeExact")
        row.auto_merge_exact = data["autoMergeExact"]

    with transaction.atomic():
        row.save()
    after = (normalise_rel(row.library_subfolder), list(row.exclusions or []))
    scan_started = False
    if after != before and library_available():
        start_scan(ScanTrigger.MANUAL)
        scan_started = True
    return Saved(settings=present(row), scan_started=scan_started)


# Names a folder browser never offers: system folders, the trash, dot-folders.
_HIDDEN = frozenset({"system volume information", "recovery", "config.msi", "windowsapps", TRASH_DIR})


def _hidden(name: str) -> bool:
    return name.startswith((".", "$")) or name.lower() in _HIDDEN


def browse(path: str = "") -> dict[str, Any]:
    """One level of the folders that could become the library (LIB-01).

    Directory names only - nothing about files is read - and only under
    LIBRARY_ROOT, through the same path guard as everything else.
    """
    try:
        root = library_root()
    except LibraryUnavailable as exc:
        raise InvalidSetting(exc.message, field="path") from exc
    rel = _relative(path, "path")
    folder = to_abs(rel, root) if rel else root
    if not folder.is_dir():
        raise InvalidSetting(f"There is no folder at {display_path(rel)} any more.", field="path")

    names: list[str] = []
    try:
        with os.scandir(folder) as entries:
            for entry in entries:
                try:
                    if entry.is_dir(follow_symlinks=False) and not _hidden(entry.name):
                        names.append(entry.name)
                except OSError:
                    continue
    except OSError as exc:
        raise InvalidSetting(f"{display_path(rel)} cannot be opened: {exc.strerror or exc}.", field="path") from exc
    names.sort(key=str.lower)

    drives = must_choose_folder() and not rel
    return {
        "path": rel,
        "display": display_path(rel),
        "parent": None if not rel else rel.rpartition("/")[0],
        "folders": [
            {"name": f"{n}:" if drives else n, "path": f"{rel}/{n}" if rel else n} for n in names
        ],
        # The top of a set of drives is not itself a folder to scan.
        "canChoose": bool(rel) or not must_choose_folder(),
    }


def _relative(value: str, field: str) -> str:
    rel = normalise_rel(value)
    if any(part == ".." for part in rel.split("/")) or "\x00" in rel:
        raise InvalidSetting("A folder inside the library, without '..'.", field=field)
    if TRASH_DIR in rel.lower().split("/"):
        raise InvalidSetting("The trash folder is managed by The Gallery.", field=field)
    return rel


def _int(value: Any, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise InvalidSetting("A whole number.", field=field)
    return value
