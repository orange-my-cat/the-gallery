"""Development - a local hot-reloading process on Windows.

Only the database is containerised. Django runs on port 8001 (8000 on this host
belongs to `control-tower`, Portainer), and the Vite dev server proxies /api
and /media to it so the browser sees a single origin.
"""

from __future__ import annotations

from django.core.exceptions import ImproperlyConfigured

from .base import *  # noqa: F401,F403
from .base import DATABASES, REPO_ROOT, env, env_bool, env_list, env_path
from .guards import (
    ConfigurationRefused,
    assert_development_database,
    assert_media_delivery,
)

DEBUG = env_bool("DJANGO_DEBUG", True)

# `host.docker.internal` is here for a browser check that drives the
# application from a container and therefore reaches the host under that name.
# Development only - production allows exactly `gallery.localhost`.
ALLOWED_HOSTS = env_list(
    "DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1,host.docker.internal"
)
CSRF_TRUSTED_ORIGINS = env_list(
    "DJANGO_CSRF_TRUSTED_ORIGINS",
    "http://localhost:5173,http://localhost:8001,http://host.docker.internal:5174",
)

# Everything development is permitted to address. `gallery_ci` is the
# throwaway tmpfs instance from compose.test.yaml.
EXPECTED_DATABASE_NAMES = {"gallery", "gallery_ci"}

# A sample copy of the library, never the real one. Unset means no library yet,
# which is a legitimate state for the skeleton.
LIBRARY_ROOT = env_path("LIBRARY_ROOT")
CACHE_ROOT = env_path("CACHE_ROOT", REPO_ROOT / ".cache")
MEDIA_DELIVERY = env("MEDIA_DELIVERY", "django").strip().lower()

# See config/settings/guards.py for why these are hard stops rather than
# warnings: a rule enforced only by care is a rule that holds until the evening
# someone is tired.
try:
    assert_development_database(
        DATABASES["default"]["NAME"],
        DATABASES["default"]["PORT"],
        EXPECTED_DATABASE_NAMES,
        host=DATABASES["default"]["HOST"],
    )
    assert_media_delivery(MEDIA_DELIVERY)
except ConfigurationRefused as exc:
    raise ImproperlyConfigured(str(exc)) from exc
