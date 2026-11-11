"""The catch-all that hands unmatched routes to the React router.

The part worth testing is what it must *not* swallow. If the catch-all matched
`/api/`, a mistyped endpoint would render the application shell with a 200 and
the front end would sit waiting for JSON that was never coming.
"""

from __future__ import annotations

import pytest
from django.test import override_settings

from core.api import views


@pytest.fixture(autouse=True)
def clear_index_cache():
    views._index_cache = None
    yield
    views._index_cache = None


def test_a_client_side_route_is_served_the_bundle(client, tmp_path):
    (tmp_path / "index.html").write_text("<!doctype html><div id=root></div>")

    with override_settings(STATIC_ROOT=str(tmp_path)):
        response = client.get("/timeline")

    assert response.status_code == 200
    assert response["Content-Type"].startswith("text/html")
    assert b"id=root" in response.content


def test_a_nested_client_side_route_is_served_the_bundle(client, tmp_path):
    (tmp_path / "index.html").write_text("<!doctype html>ok")

    with override_settings(STATIC_ROOT=str(tmp_path)):
        assert client.get("/albums/17").status_code == 200


def test_the_bundle_is_cached_outside_debug(client, tmp_path):
    index = tmp_path / "index.html"
    index.write_text("<!doctype html>first")

    with override_settings(STATIC_ROOT=str(tmp_path), DEBUG=False):
        client.get("/")
        index.write_text("<!doctype html>second")
        response = client.get("/")

    assert b"first" in response.content


def test_the_bundle_is_found_in_the_frontend_dist_fallback(client, tmp_path):
    (tmp_path / "index.html").write_text("<!doctype html>from dist")

    with override_settings(STATIC_ROOT=str(tmp_path / "empty"), FRONTEND_DIST=str(tmp_path)):
        response = client.get("/map")

    assert b"from dist" in response.content


@override_settings(DEBUG=False)
def test_a_missing_bundle_is_a_404_in_production(client, tmp_path):
    with override_settings(STATIC_ROOT=str(tmp_path), FRONTEND_DIST=str(tmp_path)):
        assert client.get("/timeline").status_code == 404


@override_settings(DEBUG=True)
def test_a_missing_bundle_explains_itself_in_development(client, tmp_path):
    """The likeliest cause is opening 8001 instead of the Vite port."""
    with override_settings(STATIC_ROOT=str(tmp_path), FRONTEND_DIST=str(tmp_path)):
        response = client.get("/timeline")

    assert response.status_code == 501
    assert b"npm run dev" in response.content


@pytest.mark.django_db
@pytest.mark.parametrize("path", ["/api/no-such-endpoint", "/media/no-such-route/1"])
def test_the_catch_all_does_not_swallow_api_or_media(client, tmp_path, path):
    (tmp_path / "index.html").write_text("<!doctype html>shell")

    with override_settings(STATIC_ROOT=str(tmp_path)):
        response = client.get(path)

    assert response.status_code == 404
    assert b"shell" not in response.content


def test_the_catch_all_does_not_swallow_the_admin(client, settings, tmp_path):
    (tmp_path / "index.html").write_text("<!doctype html>shell")

    with override_settings(STATIC_ROOT=str(tmp_path)):
        response = client.get(f"/{settings.ADMIN_PATH}/")

    # Redirected to the admin login, not handed the React shell.
    assert response.status_code in (301, 302)
    assert b"shell" not in response.content
