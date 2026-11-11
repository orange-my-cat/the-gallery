"""`manage.py dev_session` - a signed-in session for the browser check. Development only.

    python manage.py dev_session            # prints a session key for the owner
    python manage.py dev_session --revoke KEY

The browser check needs to see the app signed in without anyone typing the
owner's password into a script, a terminal history or a chat. This makes the
session Django's login would make, prints its key for the `gallery_session`
cookie, and deletes it again when asked. Refused unless DEBUG is on: it is the
same trust as `reset_password` (a shell on this PC), but it has no business in
production at all.
"""

from __future__ import annotations

from django.conf import settings
from django.contrib.auth import BACKEND_SESSION_KEY, HASH_SESSION_KEY, SESSION_KEY, get_user_model
from django.contrib.sessions.backends.db import SessionStore
from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = "Development only: create (or revoke) a session for the owner, for the browser check."

    def add_arguments(self, parser) -> None:
        parser.add_argument("--revoke", metavar="KEY", help="Delete the session with this key.")

    def handle(self, *args, **options) -> None:
        if not settings.DEBUG:
            raise CommandError("Refused: dev_session only runs with DEBUG on (development).")

        if options["revoke"]:
            SessionStore(session_key=options["revoke"]).delete()
            self.stdout.write("Session revoked.")
            return

        user = get_user_model().objects.filter(username=settings.GALLERY_USERNAME).first()
        if user is None:
            raise CommandError("There is no owner account yet. Run `manage.py reset_password` first.")

        session = SessionStore()
        session[SESSION_KEY] = str(user.pk)
        session[BACKEND_SESSION_KEY] = "django.contrib.auth.backends.ModelBackend"
        session[HASH_SESSION_KEY] = user.get_session_auth_hash()
        session.set_expiry(60 * 60)
        session.create()
        self.stdout.write(session.session_key or "")
