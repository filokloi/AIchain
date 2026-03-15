<#
.SYNOPSIS
    Launcher script for the AIchain sidecar daemon.
.DESCRIPTION
    Sets the PYTHONPATH and executes aichaind.main with local context.
#>

$ErrorActionPreference = "Stop"

$workspaceDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location -Path $workspaceDir

$env:PYTHONPATH = "."

Write-Host "Booting AIchain daemon..." -ForegroundColor Cyan
python -m aichaind.main
