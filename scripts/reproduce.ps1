# KindWindow reproduction — PowerShell wrapper around scripts/reproduce.py.
# Usage: .\scripts\reproduce.ps1 -Profile fixture   (default: fixture)
#   Profiles: fixture | verify | figures | demo | systems | real
#   Add -Full for the systems profile to run the full 18-attempt study.
# See scripts/reproduce.py for the profile contract and the test-once seal
# (evaluate is never re-run). Requires uv 0.13.9 on PATH and Python 3.12.
param(
    [string]$Profile = "fixture",
    [switch]$Full,
    [string]$Archive
)
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
Set-Location $root
if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    Write-Error "uv not found on PATH (expected uv 0.13.9)"
    exit 2
}
$pyArgs = @("scripts/reproduce.py", "--profile", $Profile)
if ($Full) { $pyArgs += "--full" }
if ($Archive) { $pyArgs += @("--archive", $Archive) }
python @pyArgs
exit $LASTEXITCODE
