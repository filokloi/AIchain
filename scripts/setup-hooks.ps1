$ErrorActionPreference = 'Stop'

$repoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $repoRoot

if (-not (git rev-parse --is-inside-work-tree 2>$null)) {
    throw "Not inside git repository: $repoRoot"
}

git config core.hooksPath .githooks
git config commit.template .gitmessage

Write-Host "Configured:" -ForegroundColor Green
Write-Host "  core.hooksPath=.githooks"
Write-Host "  commit.template=.gitmessage"
Write-Host ""
Write-Host "Next commit will enforce conventional commit format." -ForegroundColor Cyan
