from __future__ import annotations

from io import StringIO

import pytest
from django.contrib.sessions.models import Session
from django.core.management import CommandError, call_command
from django.test import Client


def _key(settings) -> str:
    out = StringIO()
    call_command("dev_session", stdout=out)
    return out.getvalue().strip()


def test_refused_outside_debug(owner, settings):
    settings.DEBUG = False
    with pytest.raises(CommandError, match="DEBUG"):
        call_command("dev_session")


def test_needs_an_owner(db, settings):
    settings.DEBUG = True
    with pytest.raises(CommandError, match="reset_password"):
        call_command("dev_session")


def test_the_session_signs_in_and_can_be_revoked(owner, settings):
    settings.DEBUG = True
    key = _key(settings)
    client = Client()
    client.cookies[settings.SESSION_COOKIE_NAME] = key
    assert client.get("/api/auth/me").status_code == 200

    call_command("dev_session", "--revoke", key, stdout=StringIO())
    assert not Session.objects.filter(session_key=key).exists()
    assert client.get("/api/auth/me").status_code == 403
