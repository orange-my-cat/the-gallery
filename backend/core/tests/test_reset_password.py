"""`manage.py reset_password` - the recovery route for a forgotten password."""

from __future__ import annotations

import io
from collections.abc import Iterator

import pytest
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError

from core.tests.conftest import OWNER_PASSWORD

pytestmark = pytest.mark.django_db

NEW_PASSWORD = "correct-horse-battery-staple"


@pytest.fixture
def typed(monkeypatch):
    """Feed answers to getpass, in order, and fail loudly if more are asked for."""

    def feed(*answers: str) -> None:
        stream: Iterator[str] = iter(answers)
        monkeypatch.setattr(
            "core.management.commands.reset_password.getpass.getpass",
            lambda prompt="": next(stream),
        )

    return feed


def _run() -> tuple[str, str]:
    out, err = io.StringIO(), io.StringIO()
    call_command("reset_password", stdout=out, stderr=err)
    return out.getvalue(), err.getvalue()


def test_it_creates_the_owner_when_none_exists(typed, client):
    typed(NEW_PASSWORD, NEW_PASSWORD)

    out, _ = _run()

    user = get_user_model().objects.get(username="owner")
    assert "Created 'owner'" in out
    assert user.check_password(NEW_PASSWORD)
    assert user.is_active and user.is_superuser
    # And the new password signs in through the API.
    response = client.post(
        "/api/auth/login", data={"password": NEW_PASSWORD}, content_type="application/json"
    )
    assert response.status_code == 200


def test_it_replaces_a_forgotten_password(typed, owner):
    typed(NEW_PASSWORD, NEW_PASSWORD)

    out, _ = _run()

    owner.refresh_from_db()
    assert "Updated 'owner'" in out
    assert owner.check_password(NEW_PASSWORD)
    assert not owner.check_password(OWNER_PASSWORD)
    assert get_user_model().objects.count() == 1


def test_it_signs_out_existing_sessions(typed, owner, client):
    client.force_login(owner)
    assert client.get("/api/auth/me").status_code == 200

    typed(NEW_PASSWORD, NEW_PASSWORD)
    _run()

    assert client.get("/api/auth/me").status_code == 403


def test_it_uses_the_configured_username(typed, settings):
    settings.GALLERY_USERNAME = "ivan"
    typed(NEW_PASSWORD, NEW_PASSWORD)

    _run()

    assert get_user_model().objects.filter(username="ivan").exists()


def test_a_mismatch_asks_again(typed, owner):
    typed(NEW_PASSWORD, "something-else", NEW_PASSWORD, NEW_PASSWORD)

    _, err = _run()

    owner.refresh_from_db()
    assert "do not match" in err
    assert owner.check_password(NEW_PASSWORD)


def test_an_empty_password_asks_again(typed, owner):
    typed("", "", NEW_PASSWORD, NEW_PASSWORD)

    _, err = _run()

    assert "cannot be empty" in err
    owner.refresh_from_db()
    assert owner.check_password(NEW_PASSWORD)


def test_a_weak_password_is_refused_by_the_validators(typed, owner):
    typed("123", "123", NEW_PASSWORD, NEW_PASSWORD)

    _, err = _run()

    assert "too short" in err
    owner.refresh_from_db()
    assert owner.check_password(NEW_PASSWORD)


def test_three_failures_abort_and_change_nothing(typed, owner):
    typed("a", "b", "a", "b", "a", "b")

    with pytest.raises(CommandError, match="Nothing was changed"):
        _run()

    owner.refresh_from_db()
    assert owner.check_password(OWNER_PASSWORD)
