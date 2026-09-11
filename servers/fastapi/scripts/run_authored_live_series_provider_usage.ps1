[CmdletBinding()]
param(
    [Parameter(Mandatory)]
    [Guid]$Job,

    [Parameter(Mandatory)]
    [ValidateNotNullOrEmpty()]
    [string]$Out
)

# Sanctioned local wrapper for authored_live_series_provider_usage.py, mirroring
# run_authored_checkpoint_repair_probe.ps1: secrets are decrypted only in
# memory, are never printed or persisted, and are removed/restored
# immediately after the child. Built for the live validation series harness,
# artifacts/authored-live-series-20260915/ (see that directory's RUNBOOK.md,
# section 6, "Provider usage").
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..\..\..')).Path
$fastApiRoot = Join-Path $repoRoot 'servers\fastapi'
. (Join-Path $repoRoot 'scripts\local-stack-lib.ps1')

$stack = Read-StackEnvironment (
    Join-Path $repoRoot 'app_data\public-v2-local\local-stack-env.dpapi.xml'
)
# Only DATABASE_URL is needed -- this script performs one read-only SELECT
# and writes no HTML, renders nothing, and touches no other stack component.
$childEnvironment = @{
    DATABASE_URL = $stack['api']['DATABASE_URL']
}
$allowedKeys = @($childEnvironment.Keys)
$previous = @{}
$locationPushed = $false

try {
    foreach ($key in $allowedKeys) {
        $previous[$key] = [Environment]::GetEnvironmentVariable($key, 'Process')
        $value = $childEnvironment[$key]
        if ($null -ne $value -and -not [string]::IsNullOrWhiteSpace([string]$value)) {
            [Environment]::SetEnvironmentVariable($key, [string]$value, 'Process')
        }
        else {
            [Environment]::SetEnvironmentVariable($key, $null, 'Process')
        }
    }

    Push-Location $fastApiRoot
    $locationPushed = $true
    $outputPath = if ([IO.Path]::IsPathRooted($Out)) { $Out } else { Join-Path $repoRoot $Out }
    & uv run python scripts/authored_live_series_provider_usage.py `
        --job $Job.ToString() `
        --out $outputPath
    $exitCode = $LASTEXITCODE
}
finally {
    if ($locationPushed) { Pop-Location }
    foreach ($key in $allowedKeys) {
        [Environment]::SetEnvironmentVariable($key, $previous[$key], 'Process')
    }
}

exit $exitCode
