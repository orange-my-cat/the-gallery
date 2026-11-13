"""Settings common to every environment.

This module is never used on its own. `dev` and `prod` import it and then state
the handful of things that genuinely differ - DEBUG, the hosts, the database,
the library and cache locations, and how media bytes are delivered.
"""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

# backend/
BASE_DIR = Path(__file__).resolve().parent.parent.parent
# The repository root. Absent inside the container, where configuration arrives
# through `docker run --env-file` instead.
REPO_ROOT = BASE_DIR.parent

load_dotenv(REPO_ROOT / ".env")


# ---------------------------------------------------------------------------
# Environment helpers
# ---------------------------------------------------------------------------
# Deliberately small. Everything environment-specific comes from .env, and
# anything absent should fail loudly at start rather than fall back to a
# default that happens to work on one machine.


def env(key: str, default: str | None = None) -> str:
    value = os.environ.get(key, default)
    if value is None:
        raise RuntimeError(
            f"{key} is not set. Every key is documented in .env.example."
        )
    return value


def env_bool(key: str, default: bool) -> bool:
    raw = os.environ.get(key)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def env_list(key: str, default: str = "") -> list[str]:
    return [item.strip() for item in os.environ.get(key, default).split(",") if item.strip()]


def env_path(key: str, default: str | Path | None = None) -> Path | None:
    raw = os.environ.get(key, "").strip()
    if raw:
        return Path(raw)
    return Path(default) if default is not None else None


# ---------------------------------------------------------------------------
# Core
# ---------------------------------------------------------------------------

SECRET_KEY = env("DJANGO_SECRET_KEY")
DEBUG = False
ALLOWED_HOSTS: list[str] = []

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "rest_framework",
    # The HLD's duplicates, places and (Phase 2) people apps arrive with the
    # features that need them.
    "core",
    "jobs",
    "library",
    "organise",
    "fileops",
    "places",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    # Immediately after SecurityMiddleware, per WhiteNoise's documented order.
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"
WSGI_APPLICATION = "config.wsgi.application"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"


# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------
# PostgreSQL only. The job queue depends on `FOR UPDATE SKIP LOCKED` and the
# raw ExifTool copy on `jsonb`, neither of which is portable.

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": env("POSTGRES_DB"),
        "USER": env("POSTGRES_USER"),
        "PASSWORD": env("POSTGRES_PASSWORD"),
        "HOST": env("POSTGRES_HOST"),
        "PORT": env("POSTGRES_PORT"),
        "CONN_MAX_AGE": 60,
    }
}


# ---------------------------------------------------------------------------
# Authentication and sessions
# ---------------------------------------------------------------------------
# One account, one password, no roles. The sign-in screen has only a password
# field; the username is fixed here and never typed. A forgotten password is
# recovered on the PC with `manage.py reset_password`.

GALLERY_USERNAME = os.environ.get("GALLERY_USERNAME", "owner").strip() or "owner"

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

# 30 days from login, and no idle timeout - SESSION_SAVE_EVERY_REQUEST stays
# False so the window is fixed rather than sliding.
SESSION_COOKIE_AGE = 30 * 24 * 60 * 60
SESSION_SAVE_EVERY_REQUEST = False
SESSION_EXPIRE_AT_BROWSER_CLOSE = False
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"
SESSION_COOKIE_NAME = "gallery_session"

# The one cookie the front end must read, to echo the token back as the
# X-CSRFToken header. Named rather than left as Django's `csrftoken`: cookies
# are not isolated by port, so in development this app and financial-hub both
# live on `localhost` and would otherwise overwrite each other's token.
CSRF_COOKIE_NAME = "gallery_csrftoken"
CSRF_COOKIE_HTTPONLY = False
CSRF_COOKIE_SAMESITE = "Lax"
# A CSRF refusal answers in the API's error shape rather than Django's HTML page.
CSRF_FAILURE_VIEW = "core.api.views.csrf_failure"

# A break-glass route at a non-obvious path. Any routine use of it indicates a
# missing feature.
ADMIN_PATH = env("DJANGO_ADMIN_PATH", "ops-gallery").strip("/")


# ---------------------------------------------------------------------------
# REST framework
# ---------------------------------------------------------------------------

REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": [
        # DRF's SessionAuthentication, differing only in the error code a CSRF
        # refusal carries (`csrf_failed`), so the front end sees one code for it
        # whichever layer refused.
        "core.api.authentication.SessionAuthentication",
    ],
    # The default, not a per-view decoration: a new endpoint is protected unless
    # it is deliberately opened, rather than exposed unless someone remembers to
    # close it.
    "DEFAULT_PERMISSION_CLASSES": [
        "rest_framework.permissions.IsAuthenticated",
    ],
    # JSON only. The browsable API would render private data into an HTML page.
    "DEFAULT_RENDERER_CLASSES": [
        "rest_framework.renderers.JSONRenderer",
    ],
    "DEFAULT_PARSER_CLASSES": [
        "rest_framework.parsers.JSONParser",
    ],
    # One error shape from every endpoint.
    "EXCEPTION_HANDLER": "core.api.errors.exception_handler",
}


# ---------------------------------------------------------------------------
# Time and locale
# ---------------------------------------------------------------------------
# Capture times are stored with their own offset (Asset.capture_offset); this
# zone only decides what "today" and "now" mean for the application itself.

LANGUAGE_CODE = "en-au"
TIME_ZONE = env("DJANGO_TIME_ZONE", "Asia/Kuala_Lumpur")
USE_I18N = False
USE_TZ = True


# ---------------------------------------------------------------------------
# Static files
# ---------------------------------------------------------------------------
# The Vite bundle is built into the image and served by WhiteNoise from the same
# process as the API, so the two can never drift apart.

STATIC_URL = "/static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
FRONTEND_DIST = BASE_DIR / "frontend_dist"

STATICFILES_DIRS = [FRONTEND_DIST] if FRONTEND_DIST.is_dir() else []

STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {
        # Compressed but not manifest-hashed: Vite already content-hashes every
        # asset filename.
        "BACKEND": "whitenoise.storage.CompressedStaticFilesStorage",
    },
}


# ---------------------------------------------------------------------------
# Library, cache and media delivery
# ---------------------------------------------------------------------------
# `dev` and `prod` supply the defaults that differ: a sample copy and the
# repo's `.cache/` in development, the `/library` bind mount and the `/cache`
# named volume in production.
#
# MEDIA_DELIVERY decides who sends media bytes once Django has authorised the
# request: `django` streams them through FileResponse (development, no Range
# support), `accel` hands Nginx an internal path through X-Accel-Redirect
# (production, Range and sendfile).

# The internal Nginx locations X-Accel-Redirect points at. They must match the
# two `internal` locations in docker/nginx/gallery.conf.
# How the library folder is shown to the owner. Empty in development, where
# LIBRARY_ROOT is itself the Windows path.
LIBRARY_HOST_DIR = os.environ.get("LIBRARY_HOST_DIR", "").strip()

# Production mounts whole drives rather than one folder, so the library folder
# can be chosen - and changed - on the settings screen (owner's decision,
# 2026-11-08). Each drive is a folder under LIBRARY_ROOT named by its letter
# (`/library/D` is D:\), and the library folder is then required: scanning a
# whole drive by default would be wrong. Empty in development.
LIBRARY_DRIVES = [d.strip().upper().rstrip(":") for d in env_list("LIBRARY_DRIVES") if d.strip()]

MEDIA_ACCEL_LIBRARY_PREFIX = "/_protected/library/"
MEDIA_ACCEL_CACHE_PREFIX = "/_protected/cache/"

# The media tools the worker runs. On PATH in the image (Debian packages); on
# Windows, either put them on PATH or give full paths to the .exe files.
EXIFTOOL_PATH = os.environ.get("EXIFTOOL_PATH", "").strip() or "exiftool"
FFMPEG_PATH = os.environ.get("FFMPEG_PATH", "").strip() or "ffmpeg"
FFPROBE_PATH = os.environ.get("FFPROBE_PATH", "").strip() or "ffprobe"


# ---------------------------------------------------------------------------
# Backups
# ---------------------------------------------------------------------------
# The application never performs a backup - that is the container entrypoint's
# job. These are read only by the smoke test.

BACKUP_DIR = os.environ.get("BACKUP_DIR", "").strip()
BACKUP_RETAIN = int(os.environ.get("BACKUP_RETAIN", "30"))
BACKUP_MAX_AGE_HOURS = int(os.environ.get("BACKUP_MAX_AGE_HOURS", "24"))


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
# stdout, captured by Docker's json-file driver and capped by the --log-opt
# flags in scripts/deploy.ps1. The `gallery` logger is where significant events
# go at info level: sign-ins, password resets, and later file operations.

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "standard": {
            "format": "{asctime} {levelname:<8} {name} {message}",
            "style": "{",
        },
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "formatter": "standard",
        },
    },
    "root": {"handlers": ["console"], "level": "WARNING"},
    "loggers": {
        "django": {"handlers": ["console"], "level": "INFO", "propagate": False},
        "django.db.backends": {"handlers": ["console"], "level": "WARNING", "propagate": False},
        "gallery": {"handlers": ["console"], "level": "INFO", "propagate": False},
    },
}
