# Build the image and (re)create `gallery` as one standalone container.
#
# One container, like financial-hub: Gunicorn and the job worker run side by
# side inside it (docker/entrypoint.sh), and if either stops the container
# restarts whole. Standalone rather than a compose stack, matching every other
# tenant of this platform: `vibe-city` is a Docker network and a naming theme,
# not a compose project. This script exists so the run arguments live in
# version control instead of only inside the Docker daemon.
#
#     .\scripts\deploy.ps1              build, recreate, wait for healthy, smoke test
#     .\scripts\deploy.ps1 -NoBuild     recreate from the image already built
#
# Configuration comes from .env.production (git-ignored), not .env: the
# development profile in .env stays untouched, so local development keeps
# working while production runs.
#
# Recreating is safe. The container holds no state: the database is in
# data-center, dumps are a bind mount, the library is the host's own drives,
# and the thumbnail cache is the `gallery-cache` named volume.
#
# ASCII only, deliberately. Windows PowerShell 5.1 reads a .ps1 with no BOM as
# ANSI, which turns a UTF-8 em dash into a smart quote and breaks parsing.

[CmdletBinding()]
param(
    [switch]$NoBuild
)

$ErrorActionPreference = 'Stop'

$repo     = Split-Path -Parent $PSScriptRoot
$envFile  = Join-Path $repo '.env.production'
$image    = 'gallery:0.1.0'
$name     = 'gallery'
$cacheVol = 'gallery-cache'

if (-not (Test-Path $envFile)) {
    throw "Missing $envFile - copy the production profile from .env.example and fill it in."
}

# ---------------------------------------------------------------------------
# Read .env.production, and refuse to deploy the wrong profile
# ---------------------------------------------------------------------------

$settings = @{}
foreach ($line in Get-Content $envFile) {
    if ($line -match '^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=(.*)$') {
        $settings[$Matches[1]] = $Matches[2].Trim()
    }
}

# Enforced rather than trusted. `data-center` (production) and
# `data-center-test` (development) differ by one digit and both hold a
# database called `gallery`, and this container runs migrations against
# whatever it is pointed at.
if ($settings['DJANGO_SETTINGS_MODULE'] -ne 'config.settings.prod') {
    throw "DJANGO_SETTINGS_MODULE is '$($settings['DJANGO_SETTINGS_MODULE'])' in .env.production, not 'config.settings.prod'."
}
if ($settings['POSTGRES_HOST'] -ne 'data-center' -or $settings['POSTGRES_PORT'] -ne '5432') {
    throw "Production must reach data-center:5432 over the vibe-city network, not '$($settings['POSTGRES_HOST']):$($settings['POSTGRES_PORT'])'."
}
if ($settings['POSTGRES_DB'] -ne 'gallery') {
    throw "POSTGRES_DB is '$($settings['POSTGRES_DB'])', not 'gallery'. Refusing to migrate the wrong database."
}
if ($settings['MEDIA_DELIVERY'] -and $settings['MEDIA_DELIVERY'] -ne 'accel') {
    throw "MEDIA_DELIVERY is '$($settings['MEDIA_DELIVERY'])'. Production streams media through Nginx: set it to 'accel' (or remove it)."
}
if ($settings['LIBRARY_ROOT'] -and $settings['LIBRARY_ROOT'] -ne '/library') {
    throw "LIBRARY_ROOT must be /library in production, where the drives are mounted."
}

$backupHostDir = $settings['BACKUP_HOST_DIR']
if (-not $backupHostDir) {
    throw "BACKUP_HOST_DIR is not set. The dump is the only backstop against another tenant's teardown; it is not optional."
}
if (-not (Test-Path $backupHostDir)) {
    throw "BACKUP_HOST_DIR '$backupHostDir' does not exist. Create it before deploying - Docker would otherwise invent it, owned by no one."
}

# ---------------------------------------------------------------------------
# The library: whole drives, read-write
# ---------------------------------------------------------------------------
# The owner chooses the library folder in Settings, so the container sees
# whole drives: D:\ at /library/D. Read-write since Phase 1b (owner's go-ahead,
# 2026-11-08): edits are written into files, and trash, move and rename change
# the library. Only the worker writes, only inside the chosen library folder,
# and every change goes through the operation log and can be undone.
# central-station mounts the same drives read-only to stream the bytes
# (d:\Repositories\vibe-city\start.ps1) - keep the two lists the same.

$drives = @($settings['LIBRARY_DRIVES'] -split ',' | ForEach-Object { $_.Trim().TrimEnd(':').ToUpper() } | Where-Object { $_ })
if ($drives.Count -eq 0) {
    throw "LIBRARY_DRIVES is not set in .env.production, e.g. LIBRARY_DRIVES=D."
}
$libraryMounts = @()
foreach ($drive in $drives) {
    if ($drive -notmatch '^[A-Z]$') { throw "LIBRARY_DRIVES entry '$drive' is not a drive letter." }
    if (-not (Test-Path "${drive}:\")) { throw "Drive ${drive}: does not exist on this PC." }
    $libraryMounts += '--mount'
    $libraryMounts += "type=bind,source=${drive}:/,target=/library/${drive}"
}

# ---------------------------------------------------------------------------
# Platform preconditions
# ---------------------------------------------------------------------------
# The network predates this repository and is never created from here.

if (-not (docker network ls -q -f name='^vibe-city$')) {
    throw "Docker network 'vibe-city' does not exist. Create it deliberately, not from this script."
}

# data-center sits on the default bridge network, where Docker's embedded DNS
# does not resolve container names. Attaching it is non-destructive, but does
# not survive the container being recreated by whoever owns it - so it is
# checked on every deploy rather than assumed.
$attached = docker network inspect vibe-city --format '{{range .Containers}}{{.Name}} {{end}}'
if ($attached -notmatch '\bdata-center\b') {
    Write-Host "Attaching data-center to vibe-city (non-destructive; no downtime)."
    docker network connect vibe-city data-center
}

# The thumbnail cache. Idempotent: an existing volume is left as it is.
if (-not (docker volume ls -q -f name="^$cacheVol$")) {
    Write-Host "Creating volume $cacheVol ..."
    docker volume create $cacheVol | Out-Null
}

# ---------------------------------------------------------------------------
# Build
# ---------------------------------------------------------------------------

if (-not $NoBuild) {
    Write-Host "Building $image ..."
    docker build --file (Join-Path $repo 'docker\Dockerfile') --tag $image $repo
    if ($LASTEXITCODE -ne 0) { throw "docker build failed." }
}

# ---------------------------------------------------------------------------
# Recreate
# ---------------------------------------------------------------------------
# No -p, deliberately. The application is unreachable except through
# central-station at http://gallery.localhost.
#
# --mount rather than -v: host paths carry a drive letter, and -v is split on
# colons. --env-file passes values through literally, with no interpolation,
# so a '$' inside a secret key survives.
#
# --stop-timeout 60: on `docker stop` the worker finishes the job in hand (a
# large video's thumbnail can take a while) instead of being killed mid-write.

# Stop first, then remove. `docker rm -f` is a SIGKILL that skips the
# --stop-timeout grace below: every job in hand is killed mid-run and left
# `running`. `docker stop` honours the grace the container was created with,
# so the worker finishes what it holds (it then recovers anything left over
# by heartbeat, but finishing cleanly loses no work).

if (docker ps -aq -f name="^$name$") {
    Write-Host "Stopping the existing $name (up to 60s while the worker finishes its jobs) ..."
    docker stop $name | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "docker stop $name failed." }
    docker rm $name | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "docker rm $name failed." }
}

Write-Host "Starting $name (library: $($drives -join ', ') read-write) ..."
$runArgs = @(
    'run', '-d',
    '--name', $name,
    '--restart', 'unless-stopped',
    '--network', 'vibe-city',
    '--env-file', $envFile,
    '--stop-timeout', '60',
    '--mount', "type=bind,source=$backupHostDir,target=/backups"
) + $libraryMounts + @(
    '--mount', "type=volume,source=$cacheVol,target=/cache",
    '--log-driver', 'json-file',
    '--log-opt', 'max-size=10m',
    '--log-opt', 'max-file=3',
    $image
)
& docker @runArgs | Out-Null
if ($LASTEXITCODE -ne 0) { throw "docker run failed." }

# ---------------------------------------------------------------------------
# Wait for the entrypoint: dump -> prune -> migrate -> Gunicorn + worker
# ---------------------------------------------------------------------------
# The healthcheck is baked into the image, so the daemon is already polling.

Write-Host -NoNewline "Waiting for $name to report healthy "
$deadline = (Get-Date).AddMinutes(3)
do {
    Start-Sleep -Seconds 3
    Write-Host -NoNewline '.'
    $state = docker inspect --format '{{.State.Health.Status}}' $name 2>$null
    if ($state -eq 'healthy') {
        Write-Host " healthy."
        Write-Host ""
        docker exec $name python manage.py smoke_test
        exit $LASTEXITCODE
    }
    $running = docker inspect --format '{{.State.Running}}' $name 2>$null
    if ($running -ne 'true') {
        Write-Host ""
        docker logs --tail 40 $name
        throw "Container stopped before becoming healthy. A failed dump or migration stops it deliberately - the log above says which."
    }
} while ((Get-Date) -lt $deadline)

Write-Host ""
docker logs --tail 40 $name
throw "Timed out waiting for health. The container is still running; the log above is the place to start."
