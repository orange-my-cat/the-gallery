# syntax=docker/dockerfile:1
#
# One image, two build stages. Node builds the React bundle; Python runs
# Gunicorn, Django and WhiteNoise serving that bundle, and the job worker. In
# production one container, `gallery`, runs both (docker/entrypoint.sh); in
# development the same image runs the worker alone (`worker`, compose.dev.yaml).
#
# The point of shipping front and back end together is that they can never
# drift out of sync - they are the same artefact.

# ---------------------------------------------------------------------------
# Stage 1 - build the React bundle
# ---------------------------------------------------------------------------
FROM node:22-bookworm-slim AS frontend

WORKDIR /build

COPY frontend/package.json ./
COPY frontend/package-lock.json* ./

# `npm ci` is the reproducible path and requires the lockfile. The fallback
# exists only for the first build, before a lockfile has been generated.
RUN if [ -f package-lock.json ]; then npm ci; else npm install; fi

COPY frontend/ ./
# The production build must use Vite `base: '/static/'`, where WhiteNoise
# serves it.
RUN npm run build
# -> /build/dist

# ---------------------------------------------------------------------------
# Stage 2 - runtime
# ---------------------------------------------------------------------------
# Matches the host interpreter development runs on. A Python minor version is a
# cheap thing to keep aligned.
FROM python:3.14-slim-bookworm AS runtime

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# postgresql-client 18 for pg_dump and pg_restore. It must be at least the
# version of the server it dumps (data-center runs 18.4), and Debian bookworm
# ships 15 - hence the PGDG repository.
#
# libimage-exiftool-perl and ffmpeg are the HLD's media tools: ExifTool reads
# and writes metadata (kept running through PyExifTool), ffprobe reads video
# metadata and ffmpeg takes poster frames. Installed now so the image the
# indexing work lands on is already the right shape.
RUN set -eux; \
    apt-get update; \
    apt-get install -y --no-install-recommends curl ca-certificates gnupg; \
    install -d /usr/share/postgresql-common/pgdg; \
    curl -fsSL https://www.postgresql.org/media/keys/ACCC4CF8.asc \
        -o /usr/share/postgresql-common/pgdg/apt.postgresql.org.asc; \
    echo "deb [signed-by=/usr/share/postgresql-common/pgdg/apt.postgresql.org.asc] https://apt.postgresql.org/pub/repos/apt bookworm-pgdg main" \
        > /etc/apt/sources.list.d/pgdg.list; \
    apt-get update; \
    apt-get install -y --no-install-recommends \
        postgresql-client-18 \
        libimage-exiftool-perl \
        ffmpeg; \
    apt-get purge -y --auto-remove gnupg curl; \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Dependencies before source, so a code change does not reinstall the world.
# requirements.lock.txt is the pinned artefact; requirements.txt is the ranged
# input it is generated from.
COPY backend/requirements.txt backend/requirements.lock.txt* ./
RUN if [ -f requirements.lock.txt ]; then \
        pip install -r requirements.lock.txt; \
    else \
        pip install -r requirements.txt; \
    fi

# PPL-01: the face models (OpenCV Zoo's YuNet, MIT, and SFace, Apache-2.0),
# downloaded once and checked against their SHA-256 by a stdlib-only script -
# its own layer, so a code change does not download them again.
ENV FACE_MODELS_DIR=/opt/face-models
COPY backend/people/fetch_models.py /tmp/fetch_models.py
RUN python /tmp/fetch_models.py "$FACE_MODELS_DIR" && rm /tmp/fetch_models.py

COPY backend/ ./

# The built bundle lands where WhiteNoise will serve it from.
COPY --from=frontend /build/dist ./frontend_dist

# Static files are collected at build time, not at start: the entrypoint's job
# is dump-then-migrate, and adding work to it lengthens the window before the
# application answers. A throwaway key is enough - collectstatic touches no
# database and no secret.
RUN DJANGO_SETTINGS_MODULE=config.settings.prod \
    DJANGO_SECRET_KEY=build-time-only-not-a-secret \
    DJANGO_ALLOWED_HOSTS=localhost \
    POSTGRES_DB=x POSTGRES_USER=x POSTGRES_PASSWORD=x \
    POSTGRES_HOST=x POSTGRES_PORT=5432 \
    python manage.py collectstatic --noinput --clear

COPY docker/entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh

# Runs as root, deliberately, as financial-hub does: the container writes dumps
# and (later) moves library files on Windows folders over Docker Desktop bind
# mounts, whose ownership semantics are not something to leave to chance.
# Worth revisiting once the mounts' permissions have been observed in practice.

EXPOSE 8000

# The healthcheck belongs to the image, not to a stack file. It connects to the
# loopback address but presents the public host name: production ALLOWED_HOSTS
# is exactly `gallery.localhost`, so a request carrying a `127.0.0.1` Host
# header would be refused before it reached a view.
#
# The development worker container serves no HTTP, so compose.dev.yaml turns
# this check off for it. In production the worker's liveness is checked by the
# smoke test's heartbeat check instead.
HEALTHCHECK --interval=30s --timeout=10s --start-period=60s --retries=3 \
    CMD python -c "import urllib.request,sys; r=urllib.request.Request('http://127.0.0.1:8000/api/health/', headers={'Host': 'gallery.localhost'}); sys.exit(0 if urllib.request.urlopen(r, timeout=4).status == 200 else 1)"

ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
