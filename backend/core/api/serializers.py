"""Serializers validate shape and type at the boundary, and nothing else.

Rules live in services.
"""

from __future__ import annotations

from rest_framework import serializers


class LoginSerializer(serializers.Serializer):
    # Password only. The username is fixed by GALLERY_USERNAME and never typed.
    password = serializers.CharField(max_length=256, trim_whitespace=False)
