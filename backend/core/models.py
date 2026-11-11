"""The application's own settings (LIB-01), as one database row.

Not environment configuration: `.env` says where the library and cache live on
this machine, and is edited by hand. These are the owner's choices, edited on
the settings screen, and they travel with the database.
"""

from __future__ import annotations

from django.core.validators import MaxValueValidator
from django.db import models


class WriteBackMode(models.TextChoices):
    # MET-02 as written: embedded XMP where the format is documented, an XMP
    # sidecar for proprietary RAW and video.
    EMBEDDED = "embedded", "Embedded where safe, sidecar otherwise"
    # Never modify a media file's bytes; every edit goes to a sidecar.
    SIDECAR = "sidecar", "Sidecar files only"


class AppSettings(models.Model):
    """Single row, primary key 1. Read it with `AppSettings.load()`."""

    # Relative to LIBRARY_ROOT, forward slashes. Empty means the root itself.
    # A running container cannot see past its mount, so this can only narrow.
    library_subfolder = models.CharField(max_length=1024, blank=True, default="")
    # Folders skipped by the scan, relative to LIBRARY_ROOT, forward slashes.
    exclusions = models.JSONField(default=list, blank=True)
    # LIB-03: hourly by default. 0 turns the schedule off; "Rescan now" still works.
    scan_interval_minutes = models.PositiveIntegerField(default=60)
    # FOP-02.
    trash_retention_days = models.PositiveIntegerField(default=30)
    write_back_mode = models.CharField(
        max_length=16, choices=WriteBackMode.choices, default=WriteBackMode.EMBEDDED
    )
    # DUP-02. Hamming distance between 64-bit perceptual hashes; the pigeonhole
    # index (HLD) only holds up to 7.
    phash_threshold = models.PositiveSmallIntegerField(
        default=4, validators=[MaxValueValidator(7)]
    )
    # DUP-06. Off until the owner has watched a few exact merges go right.
    auto_merge_exact = models.BooleanField(default=False)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "settings"
        verbose_name_plural = "settings"
        constraints = [
            models.CheckConstraint(condition=models.Q(pk=1), name="app_settings_single_row"),
        ]

    def __str__(self) -> str:
        return "Application settings"

    @classmethod
    def load(cls) -> AppSettings:
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj
