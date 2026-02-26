param(
    [Parameter(Mandatory = $true)]
    [ValidateSet('feat','fix','docs','style','refactor','perf','test','build','ci','chore','revert')]
    [string]$Type,

    [Parameter(Mandatory = $true)]
    [string]$Message,

    [string]$Scope = "",
    [switch]$Push
)

$ErrorActionPreference = 'Stop'

$repoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $repoRoot

if (-not (git rev-parse --is-inside-work-tree 2>$null)) {
    throw "Not inside a git repository: $repoRoot"
}

if ([string]::IsNullOrWhiteSpace($Scope)) {
    $commitMsg = "$Type`: $Message"
} else {
    $commitMsg = "$Type($Scope): $Message"
}

Write-Host "Staging all changes..." -ForegroundColor Cyan
git add -A

$staged = git diff --cached --name-only
if (-not $staged) {
    Write-Host "No staged changes to commit." -ForegroundColor Yellow
    exit 0
}

Write-Host "Committing: $commitMsg" -ForegroundColor Green
git commit -m $commitMsg

if ($Push) {
    $branch = git branch --show-current
    if ([string]::IsNullOrWhiteSpace($branch)) {
        throw "Unable to determine current branch"
    }
    Write-Host "Pushing to origin/$branch ..." -ForegroundColor Cyan
    git push origin $branch
}
