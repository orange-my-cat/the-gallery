"""Test - derived from development, and therefore inheriting its guard.

Django creates and drops `test_gallery` on `data-center-test` per run, so
development data survives every test run untouched. The guard in `dev` is what
makes it structurally impossible for a test run to address production: the
suite cannot even import its settings while pointed at 5432.
"""

from __future__ import annotations

from .dev import *  # noqa: F401,F403
from .dev import EXPECTED_DATABASE_NAMES as _DEV_DATABASE_NAMES

DEBUG = False

# Django prefixes the test database with `test_`. Only this settings module
# tolerates that prefix - dev and prod stay strict, so the smoke test's identity
# check keeps its teeth everywhere it is actually run in anger.
EXPECTED_DATABASE_NAMES = _DEV_DATABASE_NAMES | {
    f"test_{name}" for name in _DEV_DATABASE_NAMES
}

# Fast and deterministic.
PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]

# No test may read a real library, cache or backup folder. Tests that need one
# supply a tmp_path through `override_settings`.
LIBRARY_ROOT = None
LIBRARY_HOST_DIR = ""
LIBRARY_DRIVES: list[str] = []
CACHE_ROOT = None
MEDIA_DELIVERY = "django"
BACKUP_DIR = ""
GALLERY_USERNAME = "owner"
