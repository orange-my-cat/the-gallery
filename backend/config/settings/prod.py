"""Production - the `gallery-web` container on the vibe-city network.

Publishes no port. Reached only through `central-station` at
http://gallery.localhost. `gallery-worker` runs the same image with the same
settings.
"""

from __future__ import annotations

from django.core.exceptions import ImproperlyConfigured

from .base import *  # noqa: F401,F403
from .base import env, env_bool, env_list, env_path
from .guards import ConfigurationRefused, assert_media_delivery

# Off, so a stack trace is never rendered in the browser. Overridable only to
# make the intent explicit in .env; it should never be true here.
DEBUG = env_bool("DJANGO_DEBUG", False)

ALLOWED_HOSTS = env_list("DJANGO_ALLOWED_HOSTS", "gallery.localhost")
CSRF_TRUSTED_ORIGINS = env_list(
    "DJANGO_CSRF_TRUSTED_ORIGINS", "http://gallery.localhost"
)

# The database this container is permitted to address. Asserted against
# `SELECT current_database()` by the smoke test, so the assertion is about the
# connection actually established rather than about what was configured.
EXPECTED_DATABASE_NAMES = {"gallery"}

# gallery-worker runs beside gallery-web; the smoke test fails without it.
SMOKE_EXPECT_WORKER = True

# The container's mount points (scripts/deploy.ps1): the library bind mount and
# the `gallery-cache` named volume.
LIBRARY_ROOT = env_path("LIBRARY_ROOT", "/library")
CACHE_ROOT = env_path("CACHE_ROOT", "/cache")
MEDIA_DELIVERY = env("MEDIA_DELIVERY", "accel").strip().lower()

try:
    assert_media_delivery(MEDIA_DELIVERY)
except ConfigurationRefused as exc:
    raise ImproperlyConfigured(str(exc)) from exc

# nginx terminates the connection and forwards X-Forwarded-Proto, but that
# connection is plain HTTP on a loopback vhost - there is no TLS anywhere in
# this topology. Trusting the header while the scheme is still http would mark
# cookies secure and lock the owner out.
SECURE_PROXY_SSL_HEADER = None

X_FRAME_OPTIONS = "DENY"
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = "same-origin"
