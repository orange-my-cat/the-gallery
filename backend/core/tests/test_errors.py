"""One error shape, from every endpoint.

The shape is load-bearing rather than cosmetic: the front end renders
`field_errors` inline against the offending input and `non_field_errors` as a
banner. An endpoint that answers in a different shape is an endpoint whose
errors are invisible.
"""

from __future__ import annotations

import pytest
from django.test import override_settings
from rest_framework.exceptions import NotAuthenticated, ValidationError

from core.api.errors import exception_handler
from core.services.exceptions import BusinessRuleError, ConflictError, NotFoundError

CONTEXT: dict = {"view": None, "request": None}


def _error(response):
    return response.data["error"]


def test_business_rule_error_is_a_non_field_error():
    exc = BusinessRuleError("Nothing was changed.", code="already_trashed")

    response = exception_handler(exc, CONTEXT)

    assert response.status_code == 400
    error = _error(response)
    assert error["code"] == "already_trashed"
    assert error["field_errors"] == {}
    assert error["non_field_errors"] == [
        {"code": "already_trashed", "message": "Nothing was changed."}
    ]
    assert len(error["correlation_id"]) == 12


def test_business_rule_error_with_a_field_renders_inline():
    exc = BusinessRuleError("Enter a name.", code="blank", field="name")

    error = _error(exception_handler(exc, CONTEXT))

    assert error["non_field_errors"] == []
    assert error["field_errors"] == {"name": [{"code": "blank", "message": "Enter a name."}]}


def test_conflict_error_answers_409():
    response = exception_handler(ConflictError("An album with that name exists."), CONTEXT)
    assert response.status_code == 409
    assert _error(response)["code"] == "conflict"


def test_not_found_error_answers_404():
    response = exception_handler(NotFoundError("No such asset."), CONTEXT)
    assert response.status_code == 404
    assert _error(response)["code"] == "not_found"


def test_validation_error_splits_field_from_non_field():
    exc = ValidationError(
        {"rating": ["Enter a number."], "non_field_errors": ["The range is backwards."]}
    )

    response = exception_handler(exc, CONTEXT)

    assert response.status_code == 400
    error = _error(response)
    assert error["code"] == "validation_failed"
    assert error["field_errors"]["rating"][0]["message"] == "Enter a number."
    assert error["non_field_errors"][0]["message"] == "The range is backwards."


def test_validation_error_from_a_bare_list_is_non_field():
    error = _error(exception_handler(ValidationError(["Something is wrong."]), CONTEXT))

    assert error["field_errors"] == {}
    assert error["non_field_errors"][0]["message"] == "Something is wrong."


def test_nested_serializer_errors_are_flattened_with_their_key():
    exc = ValidationError({"filters": {"rating": ["Enter a number."]}})

    error = _error(exception_handler(exc, CONTEXT))

    assert error["field_errors"]["filters"][0]["message"] == "rating: Enter a number."


def test_other_api_exceptions_keep_the_shape():
    response = exception_handler(NotAuthenticated(), CONTEXT)

    assert response.status_code in (401, 403)
    error = _error(response)
    assert error["code"] == "not_authenticated"
    assert error["field_errors"] == {}
    assert error["non_field_errors"]
    assert error["correlation_id"]


@override_settings(DEBUG=False)
def test_unhandled_exception_never_leaks_internals():
    response = exception_handler(RuntimeError("psycopg: password authentication failed"), CONTEXT)

    assert response.status_code == 500
    error = _error(response)
    assert error["code"] == "internal_error"
    assert "psycopg" not in error["message"]
    assert "nothing was changed" in error["message"]


@override_settings(DEBUG=True)
def test_unhandled_exception_is_re_raised_in_development():
    """So the development server can render its own debug page."""
    assert exception_handler(RuntimeError("boom"), CONTEXT) is None


@pytest.mark.django_db
def test_shape_arrives_over_http(client):
    """The handler is wired in, not merely importable."""
    response = client.post("/api/auth/login", data={}, content_type="application/json")

    assert response.status_code == 400
    body = response.json()["error"]
    assert body["code"] == "validation_failed"
    assert "password" in body["field_errors"]
