"""Live Photos: a still and its motion clip, one item in the library.

An iPhone writes a Live Photo as two files - the still (`IMG_1234.HEIC` or
`.JPG`) and a clip of a second or three (`IMG_1234.MOV`) - and stamps both with
one UUID: the clip's QuickTime ContentIdentifier, the still's Apple maker note.
The asset is the photo, shown through the still; the clip joins it with the
role `live_video`, plays over it in the viewer, and is never a tile of its own.

Halves are matched within one folder, either way round, whichever the queue
reaches first:

1. By identifier, which survives renaming (`IMG_4570.JPG` with
   `IMG_4570_a.MOV`, as an export tool left them).
2. Failing that - one half has lost its tags - by name, the RAW+JPEG way, and
   only for a clip short enough to be a Live Photo's. A camera that numbers its
   photos and videos in one sequence never gives a photo and a video one name.

The identifier always wins over the name. A folder that gathers several
phones' imports holds unrelated `IMG_0904`s, and export tools have been seen
to number a pair's halves one apart (`IMG_2911.JPG` with `IMG_2912.MOV`), so a
half whose identifier is on another half in the folder never pairs by name -
and a clip paired by name before its identified still was indexed moves over.

A clip already the owner's own item - in an album, rated, tagged, edited - is
left as it is rather than folded away.
"""

from __future__ import annotations

from django.db.models import Q

from library.models import Asset, FileRole, FileState, MediaFile
from library.services.paths import content_type, pair_key

# Apple's clips run 1.5 to 3 seconds. Matching by name alone, anything longer
# is a video that happens to share a photo's name.
LIVE_MAX_SECONDS = 6.0


def folder_of(rel_path: str) -> str:
    return rel_path.rpartition("/")[0]


def is_clip(rel_path: str, mime: str) -> bool:
    return content_type(rel_path, mime) == "video"


def is_still(rel_path: str, mime: str) -> bool:
    return content_type(rel_path, mime) in ("photo", "raw")


def belong_together(
    *, still_id: str, still_key: str, clip_id: str, clip_key: str, clip_seconds: float | None
) -> bool:
    if still_id and clip_id:
        return still_id == clip_id
    return still_key == clip_key and clip_seconds is not None and 0 < clip_seconds <= LIVE_MAX_SECONDS


def _has_live(asset: Asset) -> bool:
    return asset.files.filter(role=FileRole.LIVE_VIDEO).exclude(state=FileState.TRASHED).exists()


def _untouched(asset: Asset) -> bool:
    """Nothing the owner (or the file) has given this item that a merge would lose."""
    return not (
        asset.favourite
        or asset.rating
        or asset.date_edited
        or asset.title
        or asset.description
        or asset.asset_tags.exists()
        or asset.album_items.exists()
        or asset.files.exclude(state=FileState.TRASHED).count() != 1
    )


def _best_first(key: str, live_id: str):
    """Same identifier, then same name, then the oldest."""
    return lambda f: (not (live_id and f.live_id == live_id), f.pair_key != key, f.pk)


def _own_half_is_here(live_id: str, others: list[MediaFile]) -> list[MediaFile]:
    """Only the halves carrying `live_id`, when there are any: a stranger with
    the same name never stands in for the file the identifier points at."""
    if live_id and any(f.live_id == live_id for f in others):
        return [f for f in others if f.live_id == live_id]
    return others


def _paired_by_name(clip: MediaFile) -> bool:
    """A clip whose still does not carry the clip's identifier: joined by name."""
    return bool(clip.live_id) and not (
        clip.asset.files.exclude(role=FileRole.LIVE_VIDEO)
        .exclude(state=FileState.TRASHED)
        .filter(live_id=clip.live_id)
        .exists()
    )


def _candidates(rel: str, live_id: str, key: str):
    match = Q(pair_key=key)
    if live_id:
        match |= Q(live_id=live_id)
    folder = folder_of(rel)
    found = (
        MediaFile.objects.select_for_update()
        .filter(match)
        .exclude(state=FileState.TRASHED)
        .exclude(rel_path=rel)
        .select_related("asset")
    )
    return [f for f in found if folder_of(f.rel_path) == folder]


def still_for_clip(rel: str, mime: str, live_id: str, seconds: float | None) -> MediaFile | None:
    """The still a newly found clip belongs to, if it is indexed and has no clip yet."""
    if not is_clip(rel, mime):
        return None
    key = pair_key(rel)
    stills = [
        f for f in _candidates(rel, live_id, key) if f.role != FileRole.LIVE_VIDEO and is_still(f.rel_path, f.mime)
    ]
    stills = [
        f
        for f in _own_half_is_here(live_id, stills)
        if belong_together(
            still_id=f.live_id, still_key=f.pair_key, clip_id=live_id, clip_key=key, clip_seconds=seconds
        )
        and not _has_live(f.asset)
    ]
    return min(stills, key=_best_first(key, live_id), default=None)


def clip_for_still(still: MediaFile) -> MediaFile | None:
    """An indexed clip that belongs to this still: an item of its own, or the
    motion of another photo it joined only by name before this still came."""
    if not is_still(still.rel_path, still.mime) or _has_live(still.asset):
        return None
    clips = [
        f
        for f in _candidates(still.rel_path, still.live_id, still.pair_key)
        if f.role in (FileRole.PRIMARY, FileRole.LIVE_VIDEO) and is_clip(f.rel_path, f.mime)
    ]
    clips = [
        f
        for f in _own_half_is_here(still.live_id, clips)
        if (
            f.role == FileRole.PRIMARY
            and belong_together(
                still_id=still.live_id,
                still_key=still.pair_key,
                clip_id=f.live_id,
                clip_key=f.pair_key,
                clip_seconds=f.asset.duration,
            )
            and _untouched(f.asset)
        )
        or (f.role == FileRole.LIVE_VIDEO and still.live_id and f.live_id == still.live_id and _paired_by_name(f))
    ]
    return min(clips, key=_best_first(still.pair_key, still.live_id), default=None)


def attach(clip: MediaFile, asset: Asset) -> None:
    """Make `clip` the motion of `asset`, dropping the item it was on its own."""
    from jobs.models import Job, JobState

    old = clip.asset if clip.asset_id != asset.pk else None
    clip.asset = asset
    clip.role = FileRole.LIVE_VIDEO
    clip.save(update_fields=["asset", "role", "updated_at"])
    # It is never shown as a tile, so it needs no thumbnails of its own.
    Job.objects.filter(
        kind__in=["thumb_small", "thumb_large"],
        state__in=[JobState.QUEUED, JobState.FAILED],
        payload__file=clip.pk,
    ).delete()
    if old is not None and not old.files.exists():
        old.delete()
