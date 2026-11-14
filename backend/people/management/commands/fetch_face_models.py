"""Download the face models into FACE_MODELS_DIR (the image ships with them)."""

from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand

from people.fetch_models import fetch


class Command(BaseCommand):
    help = "Download and verify the YuNet and SFace face models (PPL-01)."

    def handle(self, *args, **options):
        dest = Path(settings.FACE_MODELS_DIR)
        got = fetch(dest)
        self.stdout.write(f"{dest}: {'downloaded ' + ', '.join(got) if got else 'already present'}")
