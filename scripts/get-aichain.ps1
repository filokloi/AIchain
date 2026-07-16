<#
.SYNOPSIS
    One-line AIchain installer for Windows (beginner-friendly).
.DESCRIPTION
    Run from anywhere:
      irm https://raw.githubusercontent.com/filokloi/AIchain/main/scripts/get-aichain.ps1 | iex
    Installs to %USERPROFILE%\AIchain, sets up dependencies, creates a
    desktop launcher, and prints exactly what to paste into your AI app.
#>
$ErrorActionPreference = "Stop"
$Repo = "https://github.com/filokloi/AIchain"
$Dest = Join-Path $env:USERPROFILE "AIchain"

Write-Host ""
Write-Host "  AIchain instalacija" -ForegroundColor Cyan
Write-Host "  ===================" -ForegroundColor Cyan

# 1) Python 3.10+
$py = Get-Command python -ErrorAction SilentlyContinue
if (-not $py) {
    Write-Host "  [!] Python nije pronadjen." -ForegroundColor Red
    Write-Host "      Instaliraj sa https://www.python.org/downloads/ (stikliraj 'Add to PATH'), pa pokreni ovo ponovo."
    Write-Host "      Ili preuzmi gotov .exe (bez Pythona): $Repo/releases/latest"
    return
}
$ver = (python -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
$maj,$min = $ver.Split('.') | ForEach-Object { [int]$_ }
if ($maj -lt 3 -or ($maj -eq 3 -and $min -lt 10)) {
    Write-Host "  [!] Treba Python 3.10+, nadjen $ver. Alternativa: gotov .exe -> $Repo/releases/latest" -ForegroundColor Red
    return
}
Write-Host "  [1/4] Python $ver OK" -ForegroundColor Green

# 2) Kod (git ako postoji, inace ZIP)
if (Test-Path (Join-Path $Dest ".git")) {
    Write-Host "  [2/4] Osvezavam postojecu instalaciju..." -ForegroundColor Green
    git -C $Dest pull --ff-only | Out-Null
} elseif (Get-Command git -ErrorAction SilentlyContinue) {
    Write-Host "  [2/4] Preuzimam kod (git clone)..." -ForegroundColor Green
    git clone --depth 1 $Repo $Dest | Out-Null
} else {
    Write-Host "  [2/4] Preuzimam kod (ZIP, bez git-a)..." -ForegroundColor Green
    $zip = Join-Path $env:TEMP "aichain.zip"
    Invoke-WebRequest "$Repo/archive/refs/heads/main.zip" -OutFile $zip
    Expand-Archive $zip -DestinationPath $env:TEMP -Force
    if (Test-Path $Dest) { Remove-Item $Dest -Recurse -Force }
    Move-Item (Join-Path $env:TEMP "AIchain-main") $Dest
    Remove-Item $zip -Force
}

# 3) Zavisnosti + komanda `aichaind`
Write-Host "  [3/4] Instaliram zavisnosti..." -ForegroundColor Green
python -m pip install --quiet --disable-pip-version-check -e $Dest
if ($LASTEXITCODE -ne 0) { Write-Host "  [!] pip instalacija nije uspela." -ForegroundColor Red; return }

# 4) Desktop precica
$launcher = Join-Path ([Environment]::GetFolderPath("Desktop")) "AIchain Router.cmd"
@"
@echo off
title AIchain Router
cd /d "$Dest"
set PYTHONPATH=.
python -m aichaind.main
pause
"@ | Set-Content -Path $launcher -Encoding ASCII
Write-Host "  [4/4] Precica na Desktopu: 'AIchain Router'" -ForegroundColor Green

Write-Host ""
Write-Host "  GOTOVO. Sledeci koraci:" -ForegroundColor Cyan
Write-Host "   1. Dupli klik na 'AIchain Router' (Desktop) - prozor ostaje otvoren, to je ruter."
Write-Host "   2. Ruter ce ispisati Base URL, API key i model - nalepi ih u bilo koju AI aplikaciju."
Write-Host "   3. (Opciono) Cloud modeli: setx OPENROUTER_KEY \"tvoj-kljuc\" pa restartuj ruter."
Write-Host ""
