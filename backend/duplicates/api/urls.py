from __future__ import annotations

from django.urls import path

from duplicates.api import views

urlpatterns = [
    path("duplicates", views.QueueView.as_view(), name="duplicates"),
    path("duplicates/merge-exact", views.MergeExactView.as_view(), name="duplicates-merge-exact"),
    path("duplicates/merge-ready", views.MergeReadyView.as_view(), name="duplicates-merge-ready"),
    path("duplicates/<int:group_id>", views.GroupView.as_view(), name="duplicate"),
    path("duplicates/<int:group_id>/merge", views.MergeView.as_view(), name="duplicate-merge"),
    path("duplicates/<int:group_id>/dismiss", views.DismissView.as_view(), name="duplicate-dismiss"),
]
