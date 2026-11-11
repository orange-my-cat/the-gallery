#!/usr/bin/env python
"""Django's command-line utility.

No default is supplied for DJANGO_SETTINGS_MODULE. Development and production
differ in which database instance they address - and both call their database
`gallery` - so the settings module is something to state, never something to
inherit by accident.
"""

import os
import sys
from pathlib import Path

from dotenv import load_dotenv


def main() -> None:
    # The repository root, one level above backend/. Absent in the container,
    # where configuration arrives through `docker run --env-file` instead.
    load_dotenv(Path(__file__).resolve().parent.parent / ".env")

    if not os.environ.get("DJANGO_SETTINGS_MODULE"):
        sys.exit(
            "DJANGO_SETTINGS_MODULE is not set.\n"
            "  Development:  config.settings.dev   (gallery on localhost:5433)\n"
            "  Production:   config.settings.prod  (gallery on data-center:5432)\n"
            "Set it in .env, or export it for this shell."
        )

    try:
        from django.core.management import execute_from_command_line
    except ImportError as exc:  # pragma: no cover - import guard
        raise ImportError(
            "Django is not importable. Is the virtual environment activated, "
            "and were requirements installed?"
        ) from exc

    execute_from_command_line(sys.argv)


if __name__ == "__main__":
    main()
