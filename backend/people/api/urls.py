from __future__ import annotations

from django.urls import path

from people.api import views

urlpatterns = [
    path("people", views.PeopleView.as_view(), name="people"),
    path("people/merge", views.MergeView.as_view(), name="people-merge"),
    path("people/questions", views.QuestionsView.as_view(), name="people-questions"),
    path("people/delete", views.DeleteView.as_view(), name="people-delete"),
    path("people/not-people", views.NotPeopleView.as_view(), name="people-not-people"),
    path("people/<int:person_id>", views.PersonView.as_view(), name="person"),
    path("people/<int:person_id>/suggestions", views.SuggestionsView.as_view(), name="person-suggestions"),
    path("faces", views.FacesView.as_view(), name="faces"),
    path("faces/assign", views.AssignView.as_view(), name="faces-assign"),
    path("faces/remove", views.RemoveView.as_view(), name="faces-remove"),
    path("faces/ignore", views.IgnoreView.as_view(), name="faces-ignore"),
    path("faces/not-them", views.NotThemView.as_view(), name="faces-not-them"),
]
