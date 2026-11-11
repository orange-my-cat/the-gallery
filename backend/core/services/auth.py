"""The single owner account.

There is exactly one account. Its username comes from settings
(`GALLERY_USERNAME`, default `owner`) and is never typed: the sign-in screen
has a password field and nothing else. Everything that knows which account is
"the owner" lives here, so the login view and the `reset_password` command
cannot disagree about it.
"""

from __future__ import annotations

import logging

from django.conf import settings
from django.contrib.auth import authenticate, get_user_model
from django.contrib.auth.password_validation import validate_password
from django.db import transaction

from core.services.exceptions import BusinessRuleError

logger = logging.getLogger("gallery")

INVALID_PASSWORD_MESSAGE = "That password is not correct."


def owner_username() -> str:
    return settings.GALLERY_USERNAME


def authenticate_owner(request, password: str):
    """Return the owner if ``password`` is theirs; refuse otherwise.

    A missing owner account gets the same answer as a wrong password, so the
    sign-in screen says nothing about whether the account exists. The server
    log says what to do about it.
    """
    username = owner_username()
    user = authenticate(request, username=username, password=password)
    if user is None:
        if not get_user_model().objects.filter(username=username).exists():
            logger.warning(
                "sign-in refused: no account named %r exists. "
                "Create it with `python manage.py reset_password`.",
                username,
            )
        raise BusinessRuleError(INVALID_PASSWORD_MESSAGE, code="invalid_credentials")
    return user


def check_new_password(password: str) -> None:
    """Run Django's password validators against the owner account.

    Raises :class:`django.core.exceptions.ValidationError` listing every
    failure.
    """
    User = get_user_model()
    user = User.objects.filter(username=owner_username()).first()
    validate_password(password, user=user or User(username=owner_username()))


@transaction.atomic
def set_owner_password(password: str):
    """Set the owner's password, creating the account if it does not exist.

    Returns ``(user, created)``. The account is a superuser so the break-glass
    admin is reachable with the same credentials. Every existing session for
    the account is invalidated, because changing the password changes the
    session auth hash Django checks on each request.
    """
    User = get_user_model()
    user, created = User.objects.get_or_create(
        username=owner_username(),
        defaults={"is_staff": True, "is_superuser": True},
    )
    user.set_password(password)
    user.is_active = True
    user.save()
    logger.info(
        "password %s for %s", "set on new account" if created else "reset", user.get_username()
    )
    return user, created
