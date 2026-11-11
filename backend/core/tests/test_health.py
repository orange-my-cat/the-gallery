"""Liveness, and the default-closed posture around it."""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.django_db


def test_health_is_open_deliberately(client):
    """The one opened endpoint. It is what the image's HEALTHCHECK polls."""
    response = client.get("/api/health/")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_health_needs_no_session_and_no_csrf(csrf_client):
    assert csrf_client.get("/api/health/").status_code == 200


def test_default_permission_class_is_authenticated(settings):
    """A new endpoint is closed unless deliberately opened."""
    assert settings.REST_FRAMEWORK["DEFAULT_PERMISSION_CLASSES"] == [
        "rest_framework.permissions.IsAuthenticated"
    ]


def test_an_unknown_api_path_is_a_404(client):
    assert client.get("/api/no-such-endpoint").status_code == 404
