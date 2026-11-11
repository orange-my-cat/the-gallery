"""Names and targets for file operations (HLD, "Path rules").

The library lives on NTFS even though the worker runs on Linux, so names are
held to Windows' rules. And an operation never overwrites anything: a target
that exists gets a numbered suffix, `IMG_0001 (1).jpg`.
"""

from __future__ import annotations

import re
from pathlib import Path

from core.services.exceptions import BusinessRuleError
from library.services.paths import TRASH_DIR, normalise_rel

_FORBIDDEN = set('<>:"|?*/\\') | {chr(c) for c in range(32)}
_RESERVED = {"CON", "PRN", "AUX", "NUL"} | {f"COM{i}" for i in range(1, 10)} | {f"LPT{i}" for i in range(1, 10)}
MAX_NAME = 255


class InvalidName(BusinessRuleError):
    code = "invalid_name"


def check_name(name: str, field: str = "name") -> str:
    """One file or folder name, valid on Windows. Returns it trimmed."""
    name = name.strip()
    if not name or name in (".", ".."):
        raise InvalidName("A name is needed.", field=field)
    bad = sorted({c for c in name if c in _FORBIDDEN})
    if bad:
        shown = " ".join("/" if c == "/" else repr(c)[1:-1] if ord(c) < 32 else c for c in bad)
        raise InvalidName(f"A name cannot contain {shown}.", field=field)
    if name.endswith((".", " ")):
        raise InvalidName("A name cannot end with a dot or a space.", field=field)
    if name.split(".")[0].upper() in _RESERVED:
        raise InvalidName(f"“{name}” is a name Windows reserves.", field=field)
    if len(name.encode("utf-8")) > MAX_NAME:
        raise InvalidName("That name is too long.", field=field)
    if name.lower() == TRASH_DIR:
        raise InvalidName("That name is used by The Gallery's trash.", field=field)
    return name


def check_folder(rel: str, field: str = "folder") -> str:
    """A folder path relative to the library root, every part a valid name."""
    rel = normalise_rel(rel)
    if not rel:
        return ""
    return "/".join(check_name(part, field) for part in rel.split("/"))


_SUFFIXED = re.compile(r"^(?P<stem>.*?)(?: \((?P<n>\d+)\))?$")


def free_target(target: Path) -> Path:
    """`target` if nothing is there; otherwise the first free `name (n).ext`."""
    if not target.exists() and not target.is_symlink():
        return target
    stem, suffix = target.stem, target.suffix
    base = _SUFFIXED.match(stem)
    root = base.group("stem") if base else stem
    for n in range(1, 10_000):
        candidate = target.with_name(f"{root} ({n}){suffix}")
        if not candidate.exists() and not candidate.is_symlink():
            return candidate
    raise BusinessRuleError(f"No free name next to {target.name}.")
