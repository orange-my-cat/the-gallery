"""`manage.py renormalise` - re-apply the metadata rules to every indexed asset.

Run after changing how tags become Asset fields (library/services/metadata.py),
for example how a capture date is chosen. It reads the raw ExifTool copy kept
in the database, so it opens no file and needs no ExifTool: a few seconds for
the whole library rather than a rescan that re-reads every file.
"""

from __future__ import annotations

from django.core.management.base import BaseCommand

from library.services.indexer import renormalise


class Command(BaseCommand):
    help = "Re-apply the metadata normalising rules to every asset, from the stored raw tags."

    def handle(self, *args, **options) -> None:
        changed = renormalise()
        self.stdout.write(f"{changed} asset(s) updated.")
