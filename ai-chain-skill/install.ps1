# AIchain — install.ps1
# Automated skill installation for Windows/OpenClaw
# Run as Administrator for Task Scheduler setup
# Usage: powershell -ExecutionPolicy Bypass -File install.ps1

param(
    [string]$RoutingUrl = "https://raw.githubusercontent.com/filok94/AIchain/main/ai_routing_table.json"
)

$ErrorActionPreference = "Stop"
$SkillDir = $PSScriptRoot
$DataDir = "$env:USERPROFILE\.openclaw\aichain"
$ConfigFile = Join-Path $SkillDir "bridge_config.json"

Write-Host ""
Write-Host "  AIchain v4.0 — Sovereign Skill Installer" -ForegroundColor Green
Write-Host "  =========================================" -ForegroundColor Green
Write-Host ""

# 1. Create data directory
Write-Host "  [1/5] Creating data directory..." -ForegroundColor Cyan
New-Item -ItemType Directory -Path $DataDir -Force | Out-Null
New-Item -ItemType Directory -Path "$DataDir\backups" -Force | Out-Null
Write-Host "        $DataDir" -ForegroundColor Gray

# 2. Check Python + requests
Write-Host "  [2/5] Checking Python..." -ForegroundColor Cyan
try {
    $pyVer = python --version 2>&1
    Write-Host "        $pyVer" -ForegroundColor Gray
} catch {
    Write-Host "  [ERROR] Python not found. Install Python 3.11+" -ForegroundColor Red
    exit 1
}

$hasRequests = python -c "import requests; print('ok')" 2>$null
if ($hasRequests -ne "ok") {
    Write-Host "        Installing requests..." -ForegroundColor Yellow
    pip install requests --quiet
}

# 3. Update routing URL in config
Write-Host "  [3/5] Configuring routing URL..." -ForegroundColor Cyan
$cfg = Get-Content $ConfigFile -Raw | ConvertFrom-Json
$cfg.routing_url = $RoutingUrl
$cfg | ConvertTo-Json -Depth 10 | Set-Content $ConfigFile -Encoding UTF8
Write-Host "        $RoutingUrl" -ForegroundColor Gray

# 4. Initial sync
Write-Host "  [4/5] Running initial sync..." -ForegroundColor Cyan
$syncResult = python (Join-Path $SkillDir "aichain.py") --sync 2>&1
if ($LASTEXITCODE -eq 0) {
    Write-Host "        Sync OK" -ForegroundColor Green
} else {
    Write-Host "        Sync failed (non-critical — will retry on next cycle)" -ForegroundColor Yellow
    Write-Host "        $syncResult" -ForegroundColor Gray
}

# 5. Task Scheduler (optional, requires elevation)
Write-Host "  [5/5] Task Scheduler setup..." -ForegroundColor Cyan
$isAdmin = ([Security.Principal.WindowsPrincipal] [Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)

if ($isAdmin) {
    $taskName = "AIchain Ghost Watcher"

    # Remove existing task if present
    schtasks /Delete /TN $taskName /F 2>$null

    # Create via schtasks (simpler than XML)
    $pythonPath = (Get-Command python).Source
    $scriptPath = Join-Path $SkillDir "aichain.py"
    $action = "`"$pythonPath`" `"$scriptPath`" --watch"

    schtasks /Create /TN $taskName `
        /SC ONLOGON `
        /TR $action `
        /RL LIMITED `
        /DELAY 0000:30 `
        /F | Out-Null

    Write-Host "        Scheduled: $taskName (on logon)" -ForegroundColor Green
} else {
    Write-Host "        Skipped (run as Administrator for Task Scheduler)" -ForegroundColor Yellow
    Write-Host "        Manual: python aichain.py --watch" -ForegroundColor Gray
}

Write-Host ""
Write-Host "  ✓ Installation complete" -ForegroundColor Green
Write-Host ""
Write-Host "  Quick commands:" -ForegroundColor Cyan
Write-Host "    python aichain.py --status   # Check status" -ForegroundColor Gray
Write-Host "    python aichain.py --watch    # Start watcher" -ForegroundColor Gray
Write-Host "    python aichain.py --godmode openai/o3-pro" -ForegroundColor Gray
Write-Host ""
