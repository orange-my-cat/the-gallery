"""Rule failures, raised by services and rendered by the API layer.

These deliberately do not inherit from anything in DRF. Services must be
callable without HTTP, which means they cannot know what a status code is. The
API layer translates; the service states what went wrong.

Every error carries a stable machine-readable code, because the front end
special-cases a handful of them.
"""

from __future__ import annotations


class BusinessRuleError(Exception):
    """A rule was violated. The request is refused and nothing was changed."""

    code = "business_rule_violated"

    def __init__(
        self,
        message: str,
        *,
        code: str | None = None,
        field: str | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.code = code or type(self).code
        # When set, the API layer renders the error inline against this input
        # rather than as a banner.
        self.field = field


class ConflictError(BusinessRuleError):
    """The request contradicts state that already exists. Answers 409."""

    code = "conflict"


class NotFoundError(BusinessRuleError):
    """The addressed thing does not exist. Answers 404."""

    code = "not_found"
