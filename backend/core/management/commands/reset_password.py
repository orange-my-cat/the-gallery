"""Set the owner's password, creating the account if it does not exist.

    python manage.py reset_password

The recovery route for a forgotten password, and the way the account is made in
the first place. It runs on the PC - in development from `backend/`, in
production with `docker exec -it gallery-web python manage.py reset_password`.
Physical access to the machine is the credential; there is no e-mail reset.

Interactive only, via getpass, so the password never lands in shell history or
a process listing. Signing in again is required everywhere afterwards: changing
the password invalidates every existing session.
"""

from __future__ import annotations

import getpass

from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError

from core.services import auth as auth_service

MAX_TRIES = 3


class Command(BaseCommand):
    help = "Set (or create) the single owner account's password, interactively."

    def handle(self, *args, **options) -> None:
        username = auth_service.owner_username()
        self.stdout.write(f"Setting the password for '{username}'.")

        for _ in range(MAX_TRIES):
            first = getpass.getpass("New password: ")
            second = getpass.getpass("New password (again): ")

            if first != second:
                self.stderr.write("The two passwords do not match. Try again.")
                continue
            if not first:
                self.stderr.write("The password cannot be empty. Try again.")
                continue
            try:
                auth_service.check_new_password(first)
            except ValidationError as exc:
                for message in exc.messages:
                    self.stderr.write(message)
                continue

            _, created = auth_service.set_owner_password(first)
            verb = "Created" if created else "Updated"
            self.stdout.write(
                self.style.SUCCESS(f"{verb} '{username}'. Existing sessions are signed out.")
            )
            return

        raise CommandError(f"Aborting after {MAX_TRIES} attempts. Nothing was changed.")
