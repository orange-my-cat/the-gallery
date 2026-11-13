"""Load (or reload) the GeoNames place list bundled in places/data.

The worker does this by itself when the table is empty; this is for a reload
after the bundled files are updated.
"""

from django.core.management.base import BaseCommand

from places.services.geocode import load


class Command(BaseCommand):
    help = "Load the bundled GeoNames place list into Place (MET-03)."

    def handle(self, *args, **options):
        self.stdout.write(f"Loaded {load():,} places. Items are named again by the worker.")
