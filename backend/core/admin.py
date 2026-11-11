"""The break-glass admin.

A recovery tool, not an alternative interface. No models are registered yet -
there are none. Any routine use of the admin indicates a missing feature.
"""

from __future__ import annotations

from django.contrib import admin

admin.site.site_header = "The Gallery - recovery"
admin.site.site_title = "The Gallery - recovery"
admin.site.index_title = (
    "Break-glass access. Routine editing belongs in the application, where the "
    "rules are."
)
