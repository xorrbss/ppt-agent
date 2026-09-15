[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$repo = (Resolve-Path (Join-Path $PSScriptRoot '..\..\..')).Path
. (Join-Path $repo 'scripts\local-stack-lib.ps1')
$storePath = Join-Path $repo 'app_data\public-v2-local\local-stack-env.dpapi.xml'
$components = Read-StackEnvironment $storePath
if (-not $components.ContainsKey('worker-generation')) {
    throw 'managed local-stack environment has no worker-generation entry'
}
$workerEnvironment = $components['worker-generation']
$previous = @{}
foreach ($entry in $workerEnvironment.GetEnumerator()) {
    $existing = Get-Item -LiteralPath "Env:$($entry.Key)" -ErrorAction SilentlyContinue
    $previous[$entry.Key] = if ($null -eq $existing) { $null } else { $existing.Value }
    Set-Item -LiteralPath "Env:$($entry.Key)" -Value ([string]$entry.Value)
}
try {
    Push-Location (Join-Path $repo 'servers\fastapi')
    try {
        & uv run python (Join-Path $PSScriptRoot 'paid_trial.py') --execute
        exit $LASTEXITCODE
    } finally {
        Pop-Location
    }
} finally {
    foreach ($entry in $previous.GetEnumerator()) {
        if ($null -eq $entry.Value) {
            Remove-Item -LiteralPath "Env:$($entry.Key)" -ErrorAction SilentlyContinue
        } else {
            Set-Item -LiteralPath "Env:$($entry.Key)" -Value $entry.Value
        }
    }
}
