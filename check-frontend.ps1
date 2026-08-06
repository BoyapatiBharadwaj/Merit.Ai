# Which frontend is the browser actually being served?
#
# Both the OTP reset page and the OTP signup flow have been correct in the
# source for a while, yet the running site kept showing the old versions. That
# combination has exactly one cause: the container is serving a bundle built
# from different source than the one you are reading.
#
# The usual reason is subtle and easy to miss. Docker Compose names the project
# after the directory it runs in, and BOTH copies of this repo are called
# "Merit.Ai" -- so `docker compose up -d --build` from Downloads\Merit.Ai
# rebuilds and replaces the very same containers, using the OLD source. It
# reports success. Nothing about the output tells you which tree it used.
#
# This inspects the files nginx is actually serving, so it cannot be fooled by
# a build that looked fine.
#
#   powershell -ExecutionPolicy Bypass -File check-frontend.ps1

$ErrorActionPreference = "Stop"

Write-Host ""
Write-Host "Frontend bundle check" -ForegroundColor Cyan
Write-Host ("=" * 60)
Write-Host ""
Write-Host ("Running from: " + (Get-Location).Path)
Write-Host ""

# Two markers, one per symptom. Each string exists only in the fixed code:
# the old ForgotPassword page had no API call at all, and the old Register
# page never asked for a code.
$markers = @(
    @{ Name = "OTP password reset";  Needle = "password-reset/request" },
    @{ Name = "OTP signup";          Needle = "otp/signup/request"     }
)

$allFound = $true

foreach ($m in $markers) {
    $hit = docker compose exec -T frontend `
        sh -c "grep -rl '$($m.Needle)' /usr/share/nginx/html/assets/ 2>/dev/null | head -1"

    if ([string]::IsNullOrWhiteSpace($hit)) {
        Write-Host ("  MISSING  " + $m.Name) -ForegroundColor Red
        $allFound = $false
    } else {
        Write-Host ("  present  " + $m.Name + "  ->  " + $hit.Trim()) -ForegroundColor Green
    }
}

Write-Host ""
Write-Host ("=" * 60)

if ($allFound) {
    Write-Host "The container IS serving the fixed frontend." -ForegroundColor Green
    Write-Host ""
    Write-Host "So the stale pages are coming from your browser, not the server."
    Write-Host "In the tab showing the old page:"
    Write-Host "  1. F12 to open DevTools"
    Write-Host "  2. Application -> Storage -> Clear site data"
    Write-Host "  3. Or open the site in a private window to confirm"
} else {
    Write-Host "The container is serving an OLD bundle." -ForegroundColor Red
    Write-Host ""
    Write-Host "It was built from different source than the folder above."
    Write-Host "Rebuild WITHOUT the cache, from this directory:"
    Write-Host ""
    Write-Host "    docker compose build --no-cache frontend" -ForegroundColor Yellow
    Write-Host "    docker compose up -d frontend" -ForegroundColor Yellow
    Write-Host ""
    Write-Host "Then run this script again. If it still reports MISSING, the"
    Write-Host "compose file being used is not the one in this directory --"
    Write-Host "check for another Merit.Ai folder you may have built from:"
    Write-Host ""
    Write-Host "    docker compose ls" -ForegroundColor Yellow
    Write-Host "    docker inspect meritai-frontend-1 --format '{{ index .Config.Labels \"com.docker.compose.project.working_dir\" }}'" -ForegroundColor Yellow
}
Write-Host ""
