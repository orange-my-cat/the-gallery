"""The map's points (MAP-01, HLD "Map view").

Every geotagged item that is in the library, once, as
`[id, lat, lon, thumbnail]` - compact enough at 100,000 items to send whole
and cluster in the browser, which keeps the map quick to pan and zoom.
"""

from __future__ import annotations

from typing import Any

from library.services.browse import media_version, visible_assets
from places.models import Place


def points() -> dict[str, Any]:
    rows = (
        visible_assets()
        .filter(lat__isnull=False, lon__isnull=False)
        .order_by("-capture_at", "-pk")
        .values_list("pk", "lat", "lon", "primary_file_id", "primary_file__sha256")
    )
    out = [
        [str(pk), round(lat, 6), round(lon, 6), f"/media/thumb/{file_id}/400?v={media_version(sha)}" if file_id else None]
        for pk, lat, lon, file_id, sha in rows
    ]
    return {"points": out, "count": len(out), "placesLoaded": Place.objects.exists()}
