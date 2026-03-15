<#
.SYNOPSIS
    Bootstrap script for aichaind on Windows.
.DESCRIPTION
    Installs Python dependencies, ensures the config directories exist, and verifies port availability.
#>

$ErrorActionPreference = "Stop"
Write-Host "Starting AIchain bootstrap for Windows..." -ForegroundColor Cyan

# 1. Verify Python
$pythonVersion = (python -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')") 2>$null
if (-not $pythonVersion) {
    Write-Error "Python is not installed or not in PATH."
    exit 1
}

$major, $minor = $pythonVersion.Split('.') | ForEach-Object { [int]$_ }
if ($major -lt 3 -or ($major -eq 3 -and $minor -lt 11)) {
    Write-Error "AIchain requires Python 3.11+. Found: $pythonVersion"
    exit 1
}
Write-Host "1. Python $pythonVersion verified." -ForegroundColor Green

# 2. Install dependencies
Write-Host "2. Installing requirements..."
python -m pip install -r requirements.txt
if ($LASTEXITCODE -ne 0) {
    Write-Error "Failed to install Python dependencies."
    exit 1
}
Write-Host "   Dependencies installed." -ForegroundColor Green

# 3. Create config directories
$configDir = "$env:USERPROFILE\.openclaw\aichain"
if (-not (Test-Path $configDir)) {
    New-Item -ItemType Directory -Force -Path $configDir | Out-Null
    Write-Host "3. Created data directory at $configDir" -ForegroundColor Green
} else {
    Write-Host "3. Data directory exists at $configDir" -ForegroundColor Green
}

# 4. Check OpenClaw basic config 
$ocConfigPath = "$env:USERPROFILE\.openclaw\openclaw.json"
if (-not (Test-Path $ocConfigPath)) {
    # Provide a minimal stub just so aichaind doesn't hard-crash if the bridge runs before openclaw
    "{}`n" | Set-Content $ocConfigPath -Encoding UTF8
    Write-Host "   Created stub OpenClaw config at $ocConfigPath" -ForegroundColor DarkGray
}

# 5. Check Port Availability
$portAvailable = $true
$listener = $null
try {
    $listener = [System.Net.Sockets.TcpListener]::new([System.Net.IPAddress]::Loopback, 8080)
    $listener.Start()
} catch {
    $portAvailable = $false
} finally {
    if ($listener) { $listener.Stop() }
}

if (-not $portAvailable) {
    Write-Host "WARNING: Port 8080 is currently in use. aichaind usually requires it." -ForegroundColor Yellow
} else {
    Write-Host "4. Port 8080 is free." -ForegroundColor Green
}

Write-Host "`nBootstrap Complete! You can now run aichaind using: .\start-aichain.ps1" -ForegroundColor Cyan
