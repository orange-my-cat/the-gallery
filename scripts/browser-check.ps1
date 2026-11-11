# Run the browser check.
#
#     .\scripts\browser-check.ps1
#
# Needs the Django dev server running on 8001 against data-center-test, and
# Docker. Starts a throwaway Vite instance, drives it with Chromium in a
# container, and writes a screenshot per screen to scripts/screenshots/.
#
# Signs in with a session from `manage.py dev_session` (development only),
# revoked at the end, so the owner's password is never typed into anything.
#
# Why a second Vite instance: the everyday dev server binds to localhost, which
# a container cannot reach. The check's instance binds 0.0.0.0 on 5174 - a
# deliberate, narrow and brief exposure, stopped when the run ends - and the
# everyday server is left alone. (As in financial-hub.)

param(
    [int]$Port = 5174,
    [string]$Search = 'harbour'
)

$ErrorActionPreference = 'Stop'

$root = Split-Path $PSScriptRoot -Parent
$screenshots = Join-Path $PSScriptRoot 'screenshots'
$python = Join-Path $root '.venv\Scripts\python.exe'
$image = 'mcr.microsoft.com/playwright:v1.55.0-noble'

New-Item -ItemType Directory -Force -Path $screenshots | Out-Null

# Refuse an occupied port rather than silently checking a stale server.
$occupied = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
if ($occupied) {
    throw "Port $Port is already in use by pid $($occupied[0].OwningProcess). Stop it, or pass -Port."
}

Push-Location (Join-Path $root 'backend')
try {
    $session = (& $python manage.py dev_session 2>$null | Select-Object -Last 1).Trim()
} finally { Pop-Location }
if (-not $session) { throw 'manage.py dev_session did not return a session key.' }

Write-Host "Starting the check server on 0.0.0.0:$Port ..."
$env:VITE_CHECK = '1'
$vite = Start-Process -FilePath 'npm.cmd' -ArgumentList 'run', 'dev' `
    -WorkingDirectory (Join-Path $root 'frontend') -PassThru -WindowStyle Hidden
Remove-Item Env:VITE_CHECK

$checkExit = 1
try {
    $ready = $false
    foreach ($attempt in 1..40) {
        Start-Sleep -Milliseconds 500
        try {
            Invoke-WebRequest -Uri "http://localhost:$Port/" -UseBasicParsing -TimeoutSec 3 | Out-Null
            $ready = $true
            break
        } catch { }
    }
    if (-not $ready) { throw "The check server did not start on port $Port." }

    Write-Host 'Driving Chromium ...'
    docker run --rm `
        --add-host=host.docker.internal:host-gateway `
        -v "${root}\scripts:/scripts:ro" `
        -v "${screenshots}:/work/screenshots" `
        -e BASE_URL="http://host.docker.internal:$Port" `
        -e SESSION_KEY=$session `
        -e SEARCH=$Search `
        -e OUT_DIR=/work/screenshots `
        $image `
        sh -c 'set -e; mkdir -p /tmp/pw; cd /tmp/pw; npm install playwright@1.55.0 --no-audit --no-fund >/dev/null 2>&1; cp /scripts/browser-check.mjs .; node browser-check.mjs'
    $checkExit = $LASTEXITCODE
} finally {
    # npm.cmd spawns node as a child: kill the tree, then whatever holds the port.
    if ($vite) { & taskkill /PID $vite.Id /T /F 2>&1 | Out-Null }
    foreach ($c in (Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue)) {
        & taskkill /PID $c.OwningProcess /T /F 2>&1 | Out-Null
    }
    Push-Location (Join-Path $root 'backend')
    try { & $python manage.py dev_session --revoke $session 2>$null | Out-Null } finally { Pop-Location }
}

Write-Host "Screenshots in $screenshots"
exit $checkExit
