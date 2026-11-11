# Run the backend test suite inside the application image.
#
#     .\scripts\test-in-container.ps1                 # everything
#     .\scripts\test-in-container.ps1 fileops         # one app, or any pytest args
#
# Why: ExifTool and ffmpeg are not installed on Windows, so the tests that use
# the real tools skip there. In the image they run - against the current
# backend source mounted over /app, so no rebuild is needed - and the suite
# also gets a run on Linux, which is where production executes it.
#
# The database is data-center-test (localhost:5433 from the host,
# host.docker.internal:5433 from here), where Django creates and drops
# test_gallery as it always does. Never production: the test settings derive
# from config.settings.dev, whose guard refuses port 5432.
#
# ASCII only (Windows PowerShell 5.1 reads a BOM-less .ps1 as ANSI).

param(
    [Parameter(ValueFromRemainingArguments = $true)][string[]]$PytestArgs = @()
)

$ErrorActionPreference = 'Stop'
$repo = Split-Path $PSScriptRoot -Parent
$image = 'gallery:dev'

if (-not (docker images -q $image)) {
    throw "Image $image not found. Build it first: docker compose -f compose.dev.yaml build"
}

$command = 'pip install -q --root-user-action=ignore -r requirements-dev.txt && python -m pytest -q -p no:cacheprovider ' + ($PytestArgs -join ' ')
docker run --rm `
    --env-file (Join-Path $repo '.env') `
    -e DJANGO_SETTINGS_MODULE=config.settings.test `
    -e POSTGRES_HOST=host.docker.internal `
    -e POSTGRES_PORT=5433 `
    -e EXIFTOOL_PATH= -e FFMPEG_PATH= -e FFPROBE_PATH= `
    -e PYTHONDONTWRITEBYTECODE=1 `
    --mount "type=bind,source=$repo\backend,target=/app" `
    --entrypoint sh `
    $image -c $command
exit $LASTEXITCODE
