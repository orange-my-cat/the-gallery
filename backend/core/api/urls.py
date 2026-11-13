"""The API root.

No version prefix. Both ends ship in one image and change together.

Paths follow the HLD's API outline: no trailing slash, except `health/`, which
keeps financial-hub's spelling because the image HEALTHCHECK and the smoke test
poll it. Each later app (library, organise, fileops, duplicates, places, jobs)
is included here as it lands.
"""

from __future__ import annotations

from django.urls import include, path

from core.api.views import HealthView, LoginView, LogoutView, MeView, SessionView

app_name = "api"

urlpatterns = [
    path("health/", HealthView.as_view(), name="health"),
    path("auth/session", SessionView.as_view(), name="auth-session"),
    path("auth/login", LoginView.as_view(), name="auth-login"),
    path("auth/logout", LogoutView.as_view(), name="auth-logout"),
    path("auth/me", MeView.as_view(), name="auth-me"),
    path("", include("library.api.urls")),
    path("", include("fileops.api.urls")),
    path("", include("places.api.urls")),
]
