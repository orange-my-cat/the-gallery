"""ASGI entry point.

Unused. Background work runs in the `worker` management command, not in an
async server. The file exists because Django expects it.
"""

import os

from django.core.asgi import get_asgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.prod")

application = get_asgi_application()
