"""Session authentication with one stable code for a CSRF refusal.

DRF's ``SessionAuthentication`` refuses a missing or wrong CSRF token with a
``PermissionDenied`` whose code is the generic ``permission_denied``. The
pre-login endpoints are protected by Django's own ``csrf_protect`` instead,
whose refusal goes through ``CSRF_FAILURE_VIEW`` and carries ``csrf_failed``.
This subclass makes the authenticated path carry the same code, so the front
end has one thing to recognise whichever layer refused.
"""

from __future__ import annotations

from rest_framework import authentication, exceptions


class SessionAuthentication(authentication.SessionAuthentication):
    def enforce_csrf(self, request):
        try:
            super().enforce_csrf(request)
        except exceptions.PermissionDenied as exc:
            # str(): DRF keeps an existing ErrorDetail's code over a new one.
            raise exceptions.PermissionDenied(str(exc.detail), code="csrf_failed") from exc
