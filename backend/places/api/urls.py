from __future__ import annotations

from django.urls import path

from places.api import views

urlpatterns = [
    path("map/points", views.MapPointsView.as_view(), name="map-points"),
]
