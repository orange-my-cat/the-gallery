"""Views are thin: authenticate, deserialise, call one service, serialise, return.

No view in this system computes anything.
"""

from __future__ import annotations

import logging
from pathlib import Path

from django.conf import settings
from django.contrib.auth import login, logout
from django.http import Http404, HttpResponse, JsonResponse
from django.utils.decorators import method_decorator
from django.views.decorators.csrf import csrf_protect, ensure_csrf_cookie
from rest_framework import status as http
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from core.api.errors import error_payload, new_correlation_id
from core.api.serializers import LoginSerializer
from core.services import auth as auth_service

logger = logging.getLogger("gallery")


class HealthView(APIView):
    """Liveness. Deliberately open.

    It reports that the process is answering and nothing else. It does not
    touch the database on purpose: this is what the image's HEALTHCHECK polls,
    and a database blip should not restart an application that is otherwise
    perfectly able to explain the problem. Depth belongs to the smoke test.
    """

    permission_classes = [AllowAny]
    authentication_classes: list = []

    def get(self, request):  # noqa: ARG002
        return Response({"status": "ok"})


def _session_state(request) -> dict:
    user = request.user
    return {
        "authenticated": user.is_authenticated,
        "username": user.get_username() if user.is_authenticated else None,
    }


@method_decorator(ensure_csrf_cookie, name="dispatch")
class SessionView(APIView):
    """``GET`` - who is signed in, and set the CSRF cookie.

    The front end calls this (or ``me``) once at start-up so that the
    ``gallery_csrftoken`` cookie exists before its first ``POST``. Never an
    error: an anonymous visitor gets ``authenticated: false``.
    """

    permission_classes = [AllowAny]

    def get(self, request):
        return Response(_session_state(request))


@method_decorator(csrf_protect, name="dispatch")
class LoginView(APIView):
    """``POST {"password": "..."}`` - sign the owner in.

    ``csrf_protect`` is applied explicitly. DRF's ``APIView`` is exempt from
    Django's CSRF middleware and ``SessionAuthentication`` only re-enforces the
    check once a session exists - which would leave the sign-in POST itself, the
    one unauthenticated write in the system, unprotected.
    """

    permission_classes = [AllowAny]

    def post(self, request):
        serializer = LoginSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        user = auth_service.authenticate_owner(
            request, serializer.validated_data["password"]
        )

        login(request, user)
        logger.info("session opened for %s", user.get_username())
        return Response({"authenticated": True, "username": user.get_username()})


@method_decorator(csrf_protect, name="dispatch")
class LogoutView(APIView):
    """``POST`` - sign out. Signing out when not signed in is not an error."""

    permission_classes = [AllowAny]

    def post(self, request):
        if request.user.is_authenticated:
            username = request.user.get_username()
            logout(request)
            logger.info("session closed for %s", username)
        return Response(status=http.HTTP_204_NO_CONTENT)


@method_decorator(ensure_csrf_cookie, name="dispatch")
class MeView(APIView):
    """The canonical "am I still signed in" probe for a 30-day session.

    403 with code ``not_authenticated`` when signed out. It also sets the CSRF
    cookie - on the 403 too - so a front end that boots by asking ``me`` is
    ready to post the sign-in form without a second request.
    """

    permission_classes = [IsAuthenticated]

    def get(self, request):
        return Response({"username": request.user.get_username()})


def csrf_failure(request, reason: str = ""):  # noqa: ARG001
    """``CSRF_FAILURE_VIEW``: a CSRF refusal in the API's error shape.

    Django's default is an HTML page, which a JSON client can only show as
    "something went wrong".
    """
    message = "The request was refused because its CSRF token was missing or wrong. Reload and try again."
    item = {"code": "csrf_failed", "message": message}
    return JsonResponse(
        error_payload(
            code="csrf_failed",
            message=message,
            correlation_id=new_correlation_id(),
            non_field_errors=[item],
        ),
        status=http.HTTP_403_FORBIDDEN,
    )


# ---------------------------------------------------------------------------
# The SPA entry point
# ---------------------------------------------------------------------------

_index_cache: bytes | None = None


def _index_html() -> bytes | None:
    """The built bundle's index.html, or None if there is no bundle.

    Cached outside DEBUG. In production the file cannot change without a new
    image, so re-reading it on every request would buy nothing.
    """
    global _index_cache
    if _index_cache is not None and not settings.DEBUG:
        return _index_cache

    for candidate in (
        Path(settings.STATIC_ROOT) / "index.html",
        Path(settings.FRONTEND_DIST) / "index.html",
    ):
        if candidate.is_file():
            content = candidate.read_bytes()
            _index_cache = content
            return content
    return None


def spa_index(request):  # noqa: ARG001
    """Serve the React application shell for every client-side route.

    In development this is not the path taken: the Vite dev server serves the
    application and proxies /api and /media here, so the browser sees one
    origin and the session cookie behaves exactly as it will in production.
    """
    content = _index_html()
    if content is None:
        if settings.DEBUG:
            return HttpResponse(
                "<h1>No front end bundle</h1>"
                "<p>This is the Django development server on port 8001. The "
                "application is served by Vite &mdash; run <code>npm run dev</code> "
                "in <code>frontend/</code> and open that instead. Vite proxies "
                "<code>/api</code> and <code>/media</code> back here.</p>",
                content_type="text/html",
                status=http.HTTP_501_NOT_IMPLEMENTED,
            )
        raise Http404("No front end bundle was built into this image.")
    return HttpResponse(content, content_type="text/html")
