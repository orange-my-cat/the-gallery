#!/usr/bin/env bash
#
# Production container entrypoint for `gallery`.
#
#     wait for the database  ->  dump  ->  prune to 30  ->  migrate  ->  Gunicorn + worker
#
# The ordering is the whole point. Every schema change is preceded by a
# restorable snapshot taken seconds earlier, so a failed migration leaves the
# application down with the data intact and a fresh dump beside it.
#
# `data-center` holds other tenants' databases too, and nothing in this
# application can prevent someone else's `docker compose down -v`. The dump is
# the only backstop, so a failure here stops the container rather than being
# logged and shrugged off.
#
# With the argument `worker`, it runs the worker alone - the development
# worker container (compose.dev.yaml), beside a native Django:
#
#     wait for the database  ->  wait for migrations to be current  ->  worker
#
# That mode never dumps and never migrates - whoever runs Django owns the
# schema. Anything after `worker` is passed on to `manage.py worker`.

set -euo pipefail

log() { printf '%s  entrypoint  %s\n' "$(date -u '+%Y-%m-%dT%H:%M:%SZ')" "$*"; }
die() { log "FATAL: $*" >&2; exit 1; }

: "${POSTGRES_HOST:?POSTGRES_HOST is not set}"
: "${POSTGRES_PORT:?POSTGRES_PORT is not set}"
: "${POSTGRES_DB:?POSTGRES_DB is not set}"
: "${POSTGRES_USER:?POSTGRES_USER is not set}"
: "${POSTGRES_PASSWORD:?POSTGRES_PASSWORD is not set}"

BACKUP_DIR="${BACKUP_DIR:-/backups}"
BACKUP_RETAIN="${BACKUP_RETAIN:-30}"
GUNICORN_WORKERS="${GUNICORN_WORKERS:-2}"
GUNICORN_BIND="${GUNICORN_BIND:-0.0.0.0:8000}"

export PGPASSWORD="$POSTGRES_PASSWORD"

# ---------------------------------------------------------------------------
# 1. Wait for the database
# ---------------------------------------------------------------------------
# `data-center` is a shared container this application does not own. On a cold
# Docker Desktop start it is reliably slower to accept connections than this
# container is to start.

log "waiting for ${POSTGRES_HOST}:${POSTGRES_PORT} ..."
for attempt in $(seq 1 60); do
    if pg_isready -h "$POSTGRES_HOST" -p "$POSTGRES_PORT" -U "$POSTGRES_USER" -q; then
        log "database is accepting connections (attempt ${attempt})"
        break
    fi
    if [ "$attempt" -eq 60 ]; then
        die "database not reachable at ${POSTGRES_HOST}:${POSTGRES_PORT} after 60 attempts"
    fi
    sleep 2
done

# ---------------------------------------------------------------------------
# Worker mode - wait for the web container's migrations, then work
# ---------------------------------------------------------------------------

if [ "${1:-}" = "worker" ]; then
    shift
    log "worker: waiting for migrations to be current ..."
    until python manage.py migrate --check >/dev/null 2>&1; do
        sleep 5
    done
    log "worker: migrations current, starting"
    exec python manage.py worker "$@"
fi

# ---------------------------------------------------------------------------
# 2. Dump - before anything touches the schema
# ---------------------------------------------------------------------------
# Custom format, compressed, restorable selectively. pg_dump targets the
# `gallery` database alone, never the cluster - the other databases on this
# instance belong to other tenants.

[ -d "$BACKUP_DIR" ] || die "backup directory ${BACKUP_DIR} does not exist - is BACKUP_HOST_DIR bind-mounted?"
[ -w "$BACKUP_DIR" ] || die "backup directory ${BACKUP_DIR} is not writable"

STAMP="$(date -u '+%Y%m%dT%H%M%SZ')"
DUMP_TMP="${BACKUP_DIR}/.${POSTGRES_DB}-${STAMP}.dump.partial"
DUMP_FILE="${BACKUP_DIR}/${POSTGRES_DB}-${STAMP}.dump"

log "dumping ${POSTGRES_DB} -> $(basename "$DUMP_FILE")"

# Written under a temporary name and renamed on success, so a dump interrupted
# half-written can never be mistaken for a complete one.
if ! pg_dump \
        --host="$POSTGRES_HOST" \
        --port="$POSTGRES_PORT" \
        --username="$POSTGRES_USER" \
        --dbname="$POSTGRES_DB" \
        --format=custom \
        --compress=9 \
        --no-owner \
        --no-privileges \
        --file="$DUMP_TMP"; then
    rm -f "$DUMP_TMP"
    die "pg_dump failed - refusing to migrate without a fresh snapshot"
fi

mv "$DUMP_TMP" "$DUMP_FILE"
log "dump complete: $(du -h "$DUMP_FILE" | cut -f1)"

# ---------------------------------------------------------------------------
# 3. Prune to the newest 30
# ---------------------------------------------------------------------------
# Sorted newest-first by name, which is chronological because the timestamp is
# a sortable UTC stamp. Only this database's complete dumps are considered -
# `.partial` files are left for inspection.

mapfile -t OLD_DUMPS < <(
    find "$BACKUP_DIR" -maxdepth 1 -type f -name "${POSTGRES_DB}-*.dump" -printf '%f\n' \
        | sort -r \
        | tail -n "+$((BACKUP_RETAIN + 1))"
)

if [ "${#OLD_DUMPS[@]}" -gt 0 ]; then
    for old in "${OLD_DUMPS[@]}"; do
        log "pruning ${old}"
        rm -f "${BACKUP_DIR}/${old}"
    done
    log "pruned ${#OLD_DUMPS[@]}, retaining ${BACKUP_RETAIN}"
else
    log "nothing to prune"
fi

# ---------------------------------------------------------------------------
# 4. Migrate
# ---------------------------------------------------------------------------
# A failure here stops the container. The snapshot taken moments ago is the
# rollback: stop, restore it, revert the pinned image tag, start.

log "applying migrations"
python manage.py migrate --noinput

# ---------------------------------------------------------------------------
# 5. Serve, and work
# ---------------------------------------------------------------------------
# One container runs both, as financial-hub is one container (owner's
# decision, 2026-11-08): Gunicorn for the web, and the job worker for scans and
# thumbnails. Two Gunicorn workers by default - media bytes never pass through
# Gunicorn in production, Nginx sends them via X-Accel-Redirect.
#
# There is no process supervisor. If either process exits, the other is
# stopped and the container exits with it; `--restart unless-stopped` brings
# both back together. A half-alive container - a site that answers but never
# scans - is the failure this avoids.

WORKER_PROCESSES="${WORKER_PROCESSES:-4}"

log "starting Gunicorn on ${GUNICORN_BIND} with ${GUNICORN_WORKERS} workers"
gunicorn config.wsgi:application \
    --bind "$GUNICORN_BIND" \
    --workers "$GUNICORN_WORKERS" \
    --access-logfile - \
    --error-logfile - \
    --capture-output \
    --timeout 60 &
GUNICORN_PID=$!

log "starting the worker with ${WORKER_PROCESSES} processes"
python manage.py worker --processes "$WORKER_PROCESSES" &
WORKER_PID=$!

stop_both() {
    log "stopping"
    kill -TERM "$GUNICORN_PID" "$WORKER_PID" 2>/dev/null || true
    wait "$GUNICORN_PID" "$WORKER_PID" 2>/dev/null || true
    exit 0
}
# `docker stop`: the worker finishes the job in hand, Gunicorn its requests.
trap stop_both TERM INT

set +e
wait -n "$GUNICORN_PID" "$WORKER_PID"
status=$?
log "a process exited with status ${status}; stopping the other so the container restarts whole"
kill -TERM "$GUNICORN_PID" "$WORKER_PID" 2>/dev/null
wait
exit $(( status == 0 ? 1 : status ))
