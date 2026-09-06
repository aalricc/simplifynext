# Start all CreatorLoop services for local UAT (Windows).
#   .\scripts\run_local.ps1
#   .\scripts\run_local.ps1 -Live
param(
  [switch]$Live
)

$ErrorActionPreference = "Stop"
Set-Location (Split-Path $PSScriptRoot -Parent)

$env:PYTHONPATH = "."
$env:DEMO_SPEED = if ($env:DEMO_SPEED) { $env:DEMO_SPEED } else { "1.0" }
# USE_FIXTURES now defaults OFF: canned Maya output is correct for tests and
# wrong for a real account. -Live is kept for muscle memory and is a no-op.
$env:USE_FIXTURES = if ($env:USE_FIXTURES) { $env:USE_FIXTURES } else { "0" }

if (-not (Test-Path .env)) {
  Copy-Item .env.example .env
  Write-Host "created .env from .env.example"
}

$py = if (Test-Path .\.venv\Scripts\python.exe) { ".\.venv\Scripts\python.exe" } else { "python" }

# No Postgres to hand? Fall back to SQLite so the stack still comes up.
if (-not $env:DATABASE_URL -and -not (Select-String -Path .env -Pattern '^DATABASE_URL=.+' -Quiet)) {
  $env:DATABASE_URL = "sqlite+aiosqlite:///./creatorloop.db"
  Write-Host "DATABASE_URL unset - using $($env:DATABASE_URL)"
}

# Schema first, or services fail in confusing places later.
Write-Host "applying migrations..."
& $py -m alembic upgrade head

$jobs = @()
$jobs += Start-Process -PassThru -NoNewWindow $py -ArgumentList "-m","uvicorn","mcp.server:app","--port","8085"
$jobs += Start-Process -PassThru -NoNewWindow $py -ArgumentList "-m","uvicorn","opportunity_finder.app:app","--port","8081"
$jobs += Start-Process -PassThru -NoNewWindow $py -ArgumentList "-m","uvicorn","pipeline_manager.app:app","--port","8082"
$jobs += Start-Process -PassThru -NoNewWindow $py -ArgumentList "-m","uvicorn","engagement_listener.app:app","--port","8083"
$jobs += Start-Process -PassThru -NoNewWindow $py -ArgumentList "-m","uvicorn","cdr.app:app","--port","8084"
# The real UI: accounts, onboarding, tenant-signing proxy.
# (ui_client\server.py is the keyless fixture demo and cannot sign anyone in.)
$jobs += Start-Process -PassThru -NoNewWindow $py -ArgumentList "-m","uvicorn","ui_client.app:app","--port","8000"

Write-Host "UI        http://localhost:8000"
Write-Host "sign in   http://localhost:8000/signin"
Write-Host "demo user $py scripts\seed_demo_user.py"
Write-Host "Finder :8081  Pipeline :8082  Engagement :8083  CDR :8084  MCP :8085"
Write-Host "USE_FIXTURES=$($env:USE_FIXTURES)  DATABASE_URL=$($env:DATABASE_URL)"
Write-Host "PIDs: $($jobs.Id -join ', ')"
Write-Host "Press Ctrl+C to stop all..."

try {
  while ($true) {
    Start-Sleep -Seconds 2
    $dead = $jobs | Where-Object { $_.HasExited }
    if ($dead) {
      Write-Host "Process(es) exited: $($dead.Id -join ', ')"
      break
    }
  }
} finally {
  foreach ($p in $jobs) {
    if (-not $p.HasExited) {
      Stop-Process -Id $p.Id -Force -ErrorAction SilentlyContinue
    }
  }
}
