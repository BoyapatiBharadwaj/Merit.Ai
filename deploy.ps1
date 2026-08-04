<#
.SYNOPSIS
  Rebuild and start the Merit.Ai stack, then verify it actually came up.

.DESCRIPTION
  One command instead of a checklist, because every problem in this project so
  far has been a step that got skipped rather than code that was wrong:

    * the stack was rebuilt from a different folder than the one being edited
    * .env was missing keys that only existed in .env.example
    * the frontend container was serving a build from before the changes
    * a build failed halfway and the old containers kept running, so everything
      looked deployed and nothing was

  This script checks for each of those before touching anything, then rebuilds,
  waits for health, and reports what is actually running.

.EXAMPLE
  cd C:\Users\Babblu\Documents\GitHub\Merit.Ai
  .\deploy.ps1
#>
[CmdletBinding()]
param(
    # Skip the image rebuild and just restart. Only safe when nothing has
    # changed in backend/ or frontend/ since the last build.
    [switch]$NoBuild,
    # Rebuild ignoring the layer cache. Slow (re-downloads every wheel), but the
    # right answer when a previous build failed part-way and left bad layers.
    [switch]$Clean
)

$ErrorActionPreference = "Stop"
$root = $PSScriptRoot
Set-Location $root

function Say($msg, $colour = "White") { Write-Host $msg -ForegroundColor $colour }
function Ok($msg)   { Say "  [ok]   $msg" "Green" }
function Warn($msg) { Say "  [warn] $msg" "Yellow" }
function Fail($msg) { Say "  [FAIL] $msg" "Red" }

Say ""
Say "Merit.Ai deploy" "Cyan"
Say "Folder: $root" "DarkGray"
Say ("-" * 62) "DarkGray"

# --- 1. Am I in the right folder? -------------------------------------------
# The single most wasteful failure mode: rebuilding a copy of the project that
# does not contain the changes being tested.
Say ""
Say "1. Checking this is the right checkout"
if (-not (Test-Path "$root\docker-compose.yml")) {
    Fail "No docker-compose.yml here. Run this from the project root."
    exit 1
}
if (Test-Path "$root\backend\app\services\email_service.py") {
    Ok "email_service.py present - this checkout has the email features"
} else {
    Fail "backend/app/services/email_service.py is missing."
    Fail "This looks like an older copy of the project. Check you are not in Downloads\Merit.Ai."
    exit 1
}

# --- 2. Is .env complete? ----------------------------------------------------
Say ""
Say "2. Checking .env"
if (-not (Test-Path "$root\.env")) {
    Fail ".env not found. Copy it from .env.example and fill it in."
    exit 1
}
$envText = Get-Content "$root\.env" -Raw
$missing = @()
foreach ($key in @("SECRET_KEY", "POSTGRES_PASSWORD", "EMAIL_ENABLED", "SMTP_USERNAME")) {
    if ($envText -notmatch "(?m)^\s*$key\s*=") { $missing += $key }
}
if ($missing.Count -gt 0) {
    Fail "Missing from .env: $($missing -join ', ')"
    Fail "Compare against .env.example - it lists every supported key."
    exit 1
}
Ok "required keys present"

# The check that would have saved the last round-trip: EMAIL_ENABLED=true with
# an empty SMTP_PASSWORD means is_enabled() stays false and every send is a
# silent no-op, which looks identical to email being broken.
$emailOn = $envText -match "(?m)^\s*EMAIL_ENABLED\s*=\s*true\s*$"
$pwdSet  = $envText -match "(?m)^\s*SMTP_PASSWORD\s*=\s*\S+"
if ($emailOn -and -not $pwdSet) {
    Warn "EMAIL_ENABLED=true but SMTP_PASSWORD is empty - no mail will be sent."
    Warn "Generate a Gmail App Password (2-Step Verification must be ON first):"
    Warn "  https://myaccount.google.com/apppasswords"
    Warn "Paste the 16 characters into .env WITHOUT spaces, then re-run this."
    Say ""
    $answer = Read-Host "Continue without working email? (y/N)"
    if ($answer -ne "y") { exit 1 }
} elseif ($emailOn) {
    Ok "email enabled and a password is set"
} else {
    Warn "EMAIL_ENABLED is not true - OTP and notification emails will be disabled"
}

# --- 3. Docker available? ----------------------------------------------------
Say ""
Say "3. Checking Docker"
try {
    docker info *> $null
    if ($LASTEXITCODE -ne 0) { throw }
    Ok "Docker daemon is reachable"
} catch {
    Fail "Docker is not running. Start Docker Desktop and try again."
    exit 1
}

# --- 4. Build ----------------------------------------------------------------
Say ""
if ($NoBuild) {
    Say "4. Skipping build (-NoBuild)"
} else {
    Say "4. Building images (first run downloads ~2GB of ML wheels; be patient)"
    $buildArgs = @("compose", "build")
    if ($Clean) { $buildArgs += "--no-cache" }
    & docker @buildArgs
    if ($LASTEXITCODE -ne 0) {
        Fail "Build failed. Nothing was deployed and your old containers are untouched."
        Fail "If it died mid-download, re-run with -Clean to discard partial layers."
        exit 1
    }
    Ok "images built"
}

# --- 5. Start ----------------------------------------------------------------
Say ""
Say "5. Starting the stack"
docker compose up -d
if ($LASTEXITCODE -ne 0) { Fail "docker compose up failed."; exit 1 }
Ok "containers started"

# --- 6. Wait for health ------------------------------------------------------
# Polling the app's own readiness endpoint rather than sleeping a fixed time:
# first boot runs migrations and warms OCR weights, which is far slower than
# every subsequent start.
Say ""
Say "6. Waiting for the API to become ready (up to 3 minutes)"
$ready = $false
foreach ($i in 1..90) {
    try {
        $r = Invoke-WebRequest -Uri "http://localhost/api/health/ready" -UseBasicParsing -TimeoutSec 3
        if ($r.StatusCode -eq 200) { $ready = $true; break }
    } catch { }
    Start-Sleep -Seconds 2
    if ($i % 10 -eq 0) { Say "   still waiting... ($($i*2)s)" "DarkGray" }
}
if ($ready) { Ok "API is ready" } else {
    Fail "API did not become ready. Recent logs:"
    docker compose logs --tail 40 core-api
    exit 1
}

# --- 7. Verify what is actually running --------------------------------------
Say ""
Say "7. Verifying the deployment"

$logs = docker compose logs core-api 2>$null | Out-String

if ($logs -match "SMTP authentication failed") {
    Fail "Gmail rejected the credentials."
    Fail "SMTP_PASSWORD must be a 16-character App Password, not the account password,"
    Fail "and 2-Step Verification must be enabled on that Google account."
} elseif ($logs -match "Email disabled; not sending") {
    Warn "Email is disabled - sends are being skipped (check EMAIL_ENABLED / SMTP_PASSWORD)"
} else {
    Ok "no SMTP errors in the logs"
}

if ($logs -match "Interactive API docs are disabled") { Ok "production mode: /docs is disabled" }
if ($logs -match "reminder scheduler started")        { Ok "exam reminder scheduler running" }
if ($logs -match "No admin seeded")                   { Warn "no admin account was created - set SEED_ADMIN_PASSWORD in .env" }

# Confirms the frontend container is serving the CURRENT build rather than a
# stale one, which is exactly the symptom that made the Forgot page look wrong.
try {
    $page = Invoke-WebRequest -Uri "http://localhost/" -UseBasicParsing -TimeoutSec 5
    if ($page.Content -match "cdnjs\.cloudflare\.com") {
        Warn "The served page still references cdnjs - the frontend image is STALE."
        Warn "Re-run with -Clean to force a rebuild."
    } else {
        Ok "frontend is serving the current build (no CDN references)"
    }
} catch { Warn "could not fetch the frontend to check it" }

Say ""
Say ("-" * 62) "DarkGray"
Say "Done. Open http://localhost" "Cyan"
Say ""
Say "To watch email being sent as you use the app:" "DarkGray"
Say "  docker compose logs -f core-api | Select-String 'Sent |SMTP'" "DarkGray"
Say ""
