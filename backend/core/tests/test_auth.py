"""Password-only sign-in for the single owner account.

What these tests pin down is the HTTP contract the front end is built against:
paths, bodies, status codes, cookie names and the error codes it branches on.
"""

from __future__ import annotations

from unittest import mock

import pytest
from django.contrib.auth import get_user_model

from core.services import auth as auth_service
from core.tests.conftest import OWNER_PASSWORD

pytestmark = pytest.mark.django_db

LOGIN = "/api/auth/login"
LOGOUT = "/api/auth/logout"
ME = "/api/auth/me"
SESSION = "/api/auth/session"


def _login(client, password=OWNER_PASSWORD, **extra):
    return client.post(LOGIN, data={"password": password}, content_type="application/json", **extra)


# ---------------------------------------------------------------------------
# Session probe and CSRF bootstrap
# ---------------------------------------------------------------------------


def test_session_reports_anonymous_and_sets_the_csrf_cookie(client, settings):
    response = client.get(SESSION)

    assert response.status_code == 200
    assert response.json() == {"authenticated": False, "username": None}
    assert settings.CSRF_COOKIE_NAME == "gallery_csrftoken"
    assert "gallery_csrftoken" in response.cookies


def test_session_reports_the_signed_in_owner(client, owner):
    client.force_login(owner)

    assert client.get(SESSION).json() == {"authenticated": True, "username": "owner"}


def test_me_also_sets_the_csrf_cookie_even_when_refusing(client):
    response = client.get(ME)

    assert response.status_code == 403
    assert "gallery_csrftoken" in response.cookies


# ---------------------------------------------------------------------------
# Login
# ---------------------------------------------------------------------------


def test_login_with_the_right_password_opens_a_session(client, owner, settings):
    response = _login(client)

    assert response.status_code == 200
    assert response.json() == {"authenticated": True, "username": "owner"}
    assert settings.SESSION_COOKIE_NAME in response.cookies
    assert client.get(ME).json() == {"username": "owner"}


def test_login_uses_the_configured_username(client, settings):
    settings.GALLERY_USERNAME = "ivan"
    get_user_model().objects.create_user(username="ivan", password=OWNER_PASSWORD)

    response = _login(client)

    assert response.status_code == 200
    assert response.json()["username"] == "ivan"


def test_login_with_the_wrong_password_is_refused(client, owner):
    response = _login(client, password="not-the-password")

    assert response.status_code == 400
    error = response.json()["error"]
    assert error["code"] == "invalid_credentials"
    assert error["non_field_errors"][0]["code"] == "invalid_credentials"
    assert client.get(ME).status_code == 403


def test_login_with_no_owner_account_gives_the_same_answer(client):
    with mock.patch.object(auth_service.logger, "warning") as warning:
        response = _login(client)

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "invalid_credentials"
    # The server log, not the screen, says how to fix it.
    assert "reset_password" in warning.call_args.args[0]


def test_login_refuses_an_inactive_owner(client, owner):
    owner.is_active = False
    owner.save()

    assert _login(client).status_code == 400


def test_login_without_a_password_is_a_field_error(client, owner):
    response = client.post(LOGIN, data={}, content_type="application/json")

    assert response.status_code == 400
    error = response.json()["error"]
    assert error["code"] == "validation_failed"
    assert "password" in error["field_errors"]


def test_a_username_in_the_body_is_ignored(client, owner):
    """The sign-in screen has no username field; sending one changes nothing."""
    response = client.post(
        LOGIN,
        data={"username": "someone-else", "password": OWNER_PASSWORD},
        content_type="application/json",
    )

    assert response.status_code == 200
    assert response.json()["username"] == "owner"


def test_login_is_post_only(client):
    assert client.get(LOGIN).status_code == 405


# ---------------------------------------------------------------------------
# Logout and me
# ---------------------------------------------------------------------------


def test_logout_closes_the_session(client, owner):
    client.force_login(owner)

    response = client.post(LOGOUT)

    assert response.status_code == 204
    assert client.get(ME).status_code == 403


def test_logout_when_not_signed_in_is_not_an_error(client):
    assert client.post(LOGOUT).status_code == 204


def test_me_is_refused_when_signed_out(client):
    response = client.get(ME)

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "not_authenticated"


def test_me_reports_the_owner(client, owner):
    client.force_login(owner)

    response = client.get(ME)

    assert response.status_code == 200
    assert response.json() == {"username": "owner"}


def test_session_lifetime_is_thirty_days_and_does_not_slide(settings):
    assert settings.SESSION_COOKIE_AGE == 30 * 24 * 60 * 60
    assert settings.SESSION_SAVE_EVERY_REQUEST is False
    assert settings.SESSION_COOKIE_HTTPONLY is True
    assert settings.SESSION_COOKIE_SAMESITE == "Lax"
    assert settings.SESSION_COOKIE_NAME == "gallery_session"
    # The front end must read this one to echo the token back as a header.
    assert settings.CSRF_COOKIE_HTTPONLY is False


# ---------------------------------------------------------------------------
# CSRF enforcement
# ---------------------------------------------------------------------------


def _csrf_token(client) -> str:
    client.get(SESSION)
    return client.cookies["gallery_csrftoken"].value


def test_login_without_a_csrf_token_is_refused(csrf_client, owner):
    response = _login(csrf_client)

    assert response.status_code == 403
    assert response["Content-Type"].startswith("application/json")
    assert response.json()["error"]["code"] == "csrf_failed"
    assert csrf_client.get(ME).status_code == 403


def test_login_with_a_wrong_csrf_token_is_refused(csrf_client, owner):
    _csrf_token(csrf_client)

    response = _login(csrf_client, HTTP_X_CSRFTOKEN="x" * 32)

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "csrf_failed"


def test_login_with_the_csrf_token_header_succeeds(csrf_client, owner):
    token = _csrf_token(csrf_client)

    response = _login(csrf_client, HTTP_X_CSRFTOKEN=token)

    assert response.status_code == 200
    assert csrf_client.get(ME).status_code == 200


def test_login_rotates_the_csrf_token(csrf_client, owner):
    """The front end must re-read the cookie after signing in."""
    token = _csrf_token(csrf_client)

    response = _login(csrf_client, HTTP_X_CSRFTOKEN=token)

    assert response.cookies["gallery_csrftoken"].value != token


def test_logout_without_a_csrf_token_is_refused(csrf_client, owner):
    token = _csrf_token(csrf_client)
    _login(csrf_client, HTTP_X_CSRFTOKEN=token)

    response = csrf_client.post(LOGOUT)

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "csrf_failed"
    assert csrf_client.get(ME).status_code == 200


def test_logout_with_the_csrf_token_header_succeeds(csrf_client, owner):
    _login(csrf_client, HTTP_X_CSRFTOKEN=_csrf_token(csrf_client))
    fresh = csrf_client.cookies["gallery_csrftoken"].value

    response = csrf_client.post(LOGOUT, HTTP_X_CSRFTOKEN=fresh)

    assert response.status_code == 204
    assert csrf_client.get(ME).status_code == 403


def test_drf_csrf_refusal_carries_the_same_code(rf, owner):
    """Authenticated endpoints are checked by DRF, not csrf_protect."""
    from rest_framework.exceptions import PermissionDenied

    from core.api.authentication import SessionAuthentication

    request = rf.post("/api/anything", data={}, content_type="application/json")
    request.user = owner
    request._dont_enforce_csrf_checks = False

    with pytest.raises(PermissionDenied) as raised:
        SessionAuthentication().enforce_csrf(request)

    assert raised.value.get_codes() == "csrf_failed"
