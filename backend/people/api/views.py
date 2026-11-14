"""People (PPL-02 to PPL-04). Thin, as everywhere; changes answer with an operation."""

from __future__ import annotations

from rest_framework.response import Response
from rest_framework.views import APIView

from fileops.api.views import _accepted, _data
from people.services import browse, ops, questions


class PeopleView(APIView):
    """``GET /api/people`` - everyone found, named first."""

    def get(self, request):
        return Response(browse.people())


class PersonView(APIView):
    """``GET``, ``PATCH {name}``, ``DELETE /api/people/{id}``."""

    def get(self, request, person_id: int):
        return Response(browse.person(person_id))

    def patch(self, request, person_id: int):
        return _accepted(ops.rename(person_id, _data(request).get("name", "")))

    def delete(self, request, person_id: int):
        return _accepted(ops.delete_person(person_id))


class SuggestionsView(APIView):
    """``GET /api/people/{id}/suggestions`` - faces that look like this person."""

    def get(self, request, person_id: int):
        return Response(browse.suggestions(person_id))


class MergeView(APIView):
    """``POST /api/people/merge`` - `{ids, into?}`."""

    def post(self, request):
        data = _data(request)
        return _accepted(ops.merge(data.get("ids") or [], data.get("into")))


class DeleteView(APIView):
    """``POST /api/people/delete`` - `{ids}`: several people in one change."""

    def post(self, request):
        return _accepted(ops.delete_people(_data(request).get("ids") or []))


class NotPeopleView(APIView):
    """``POST /api/people/not-people`` - `{ids}`: groups that are not people."""

    def post(self, request):
        return _accepted(ops.not_people(_data(request).get("ids") or []))


class QuestionsView(APIView):
    """``GET /api/people/questions?skip=1,2`` - "are they the same person?"."""

    def get(self, request):
        skip = {int(v) for v in request.query_params.get("skip", "").split(",") if v.strip().isdigit()}
        return Response(questions.questions(skip=skip))


class NotThemView(APIView):
    """``POST /api/faces/not-them`` - `{face, person}`: the answer "no"."""

    def post(self, request):
        data = _data(request)
        return _accepted(ops.not_them(data.get("face"), data.get("person")))


class FacesView(APIView):
    """``GET /api/faces`` - faces nobody holds, clearest first."""

    def get(self, request):
        return Response(browse.unassigned())


class AssignView(APIView):
    """``POST /api/faces/assign`` - `{faces, person?}` or `{faces, name?}` for a new person."""

    def post(self, request):
        data = _data(request)
        return _accepted(ops.add_faces(data.get("faces") or [], data.get("person"), data.get("name")))


class RemoveView(APIView):
    """``POST /api/faces/remove`` - `{faces}`: away from their person."""

    def post(self, request):
        return _accepted(ops.remove_faces(_data(request).get("faces") or []))


class IgnoreView(APIView):
    """``POST /api/faces/ignore`` - `{faces}`: not a face (PPL-04)."""

    def post(self, request):
        return _accepted(ops.not_faces(_data(request).get("faces") or []))
