<#
.SYNOPSIS
    Scheduled Patreon creator scrape for the knowledge base. No LLM required.

.DESCRIPTION
    Activates the project venv and runs `kb patreon scrape-creator`, which:
      1. ensures the logged-in browser daemon is running,
      2. refreshes + validates the session cookie,
      3. crawls and downloads every registered creator incrementally.

    Intended for Windows Task Scheduler. The script exits with the CLI's exit
    code so the scheduler can detect failures:
      0 = success, 1 = a creator failed, 2 = session/daemon needs interactive login.

.EXAMPLE
    pwsh -File scripts\scrape_patreon.ps1
    pwsh -File scripts\scrape_patreon.ps1 -Creators aminvest -Limit 50
#>
[CmdletBinding()]
param(
    # Specific creators to scrape; default = all registered in the DB.
    [string[]] $Creators = @(),
    # Max new downloads per creator (0 = all pending).
    [int] $Limit = 0,
    # Only download posts from this calendar year (0 = no filter).
    [int] $Year = 0
)

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

$cmd = @('run', 'kb', 'patreon', 'scrape-creator')
if ($Creators.Count) { $cmd += $Creators }
if ($Limit -gt 0)    { $cmd += @('--limit', $Limit) }
if ($Year  -gt 0)    { $cmd += @('--year',  $Year) }

Write-Host "[$(Get-Date -Format s)] uv $($cmd -join ' ')"
& uv @cmd
exit $LASTEXITCODE
