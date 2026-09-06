# Frozen-HTTP smoke suite (Windows).
#
# The checks live in scripts/uat_smoke.py so one implementation covers every
# platform -- every backend route now needs a signed tenant header, and
# duplicating that handshake in PowerShell meant two things to keep in sync.
#
#   .\scripts\run_local.ps1     # in one terminal
#   .\scripts\uat_smoke.ps1     # in another

$ErrorActionPreference = "Stop"
Set-Location (Split-Path $PSScriptRoot -Parent)

$py = if (Test-Path .\.venv\Scripts\python.exe) { ".\.venv\Scripts\python.exe" } else { "python" }

& $py .\scripts\uat_smoke.py @args
exit $LASTEXITCODE
