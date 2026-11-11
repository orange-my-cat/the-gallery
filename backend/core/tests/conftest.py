"""Fixtures for the core suite."""

from __future__ import annotations

import pytest
from django.contrib.auth import get_user_model
from django.test import Client

OWNER_PASSWORD = "a-long-enough-password"


@pytest.fixture
def owner(db, settings):
    """The single account. There is one and no roles."""
    return get_user_model().objects.create_user(
        username=settings.GALLERY_USERNAME, password=OWNER_PASSWORD
    )


@pytest.fixture
def csrf_client(db):
    """A client that enforces CSRF, as a browser's requests are checked.

    Django's test client skips CSRF checks by default, which would make every
    CSRF test pass vacuously.
    """
    return Client(enforce_csrf_checks=True)
