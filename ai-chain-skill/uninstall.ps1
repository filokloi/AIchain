# AIchain — uninstall.ps1
# Clean removal of AIchain skill
# Usage: powershell -ExecutionPolicy Bypass -File uninstall.ps1

$ErrorActionPreference = "SilentlyContinue"
$DataDir = "$env:USERPROFILE\.openclaw\aichain"
$TaskName = "AIchain Ghost Watcher"

Write-Host ""
Write-Host "  AIchain — Uninstall" -ForegroundColor Yellow
Write-Host ""

# 1. Kill watcher process
Write-Host "  [1/4] Stopping watcher..." -ForegroundColor Cyan
Get-Process python* | Where-Object {
    $_.CommandLine -match "aichain" 
} | Stop-Process -Force 2>$null
Write-Host "        Done" -ForegroundColor Gray

# 2. Remove Task Scheduler entry
Write-Host "  [2/4] Removing scheduled task..." -ForegroundColor Cyan
schtasks /Delete /TN $TaskName /F 2>$null
Write-Host "        Done" -ForegroundColor Gray

# 3. Restore config from backup
Write-Host "  [3/4] Restoring config backup..." -ForegroundColor Cyan
$backups = Get-ChildItem "$DataDir\backups\openclaw.json.bak.*" -ErrorAction SilentlyContinue | Sort-Object LastWriteTime
if ($backups.Count -gt 0) {
    $latest = $backups[-1]
    Copy-Item $latest.FullName "$env:USERPROFILE\.openclaw\openclaw.json" -Force
    Write-Host "        Restored from $($latest.Name)" -ForegroundColor Green
}
else {
    Write-Host "        No backup found (config unchanged)" -ForegroundColor Yellow
}

# 4. Remove data directory
Write-Host "  [4/4] Cleaning data..." -ForegroundColor Cyan
if (Test-Path $DataDir) {
    Remove-Item $DataDir -Recurse -Force
    Write-Host "        Removed $DataDir" -ForegroundColor Gray
}

Write-Host ""
Write-Host "  ✓ Uninstall complete — no residue" -ForegroundColor Green
Write-Host ""
