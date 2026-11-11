"""Configuration guards.

Plain functions with no Django imports, so they can be called from a settings
module at import time - before the app registry exists - and tested without
reloading settings.
"""

from __future__ import annotations


class ConfigurationRefused(Exception):
    """The process must not start with this configuration."""


# `data-center`, the shared production instance. It publishes 0.0.0.0:5432 on
# the same host the development server runs on.
PRODUCTION_DATABASE_HOST = "data-center"
PRODUCTION_DATABASE_PORT = "5432"


def assert_development_database(
    name: str, port: str | int, permitted: set[str], host: str = "localhost"
) -> None:
    """Refuse to start if development is pointed at production.

    `data-center` (production, port 5432) and `data-center-test` (development,
    port 5433) differ by one digit, and the development server runs on the same
    host with DEBUG on and migrations pending.

    Unlike financial-hub, the database *name* cannot tell the two apart: both
    environments use a database called `gallery`, one per instance. So the
    port and the host carry the whole check here, and the name is only checked
    against the list development may address at all.
    """
    port = str(port)

    if port == PRODUCTION_DATABASE_PORT:
        raise ConfigurationRefused(
            f"Refusing to start: POSTGRES_PORT is {port}, which is `data-center` - "
            f"the production instance. Development uses port 5433 "
            f"(`data-center-test`). Check POSTGRES_PORT in .env."
        )

    if host.strip().lower() == PRODUCTION_DATABASE_HOST:
        raise ConfigurationRefused(
            f"Refusing to start: POSTGRES_HOST is '{host}', the production "
            f"instance. Development uses localhost:5433. Check POSTGRES_HOST in .env."
        )

    if name not in permitted:
        raise ConfigurationRefused(
            f"Refusing to start: POSTGRES_DB is '{name}', which is not a database "
            f"development may address ({', '.join(sorted(permitted))})."
        )


MEDIA_DELIVERY_MODES = {"django", "accel"}


def assert_media_delivery(mode: str) -> None:
    """`MEDIA_DELIVERY` is one of two words, and a typo must not start."""
    if mode not in MEDIA_DELIVERY_MODES:
        raise ConfigurationRefused(
            f"Refusing to start: MEDIA_DELIVERY is '{mode}'. It must be one of "
            f"{', '.join(sorted(MEDIA_DELIVERY_MODES))}."
        )
