[CmdletBinding()]
param(
    [Parameter(Mandatory)]
    [ValidateNotNullOrEmpty()]
    [string]$Out
)

# Sanctioned local wrapper, copied in shape from
# servers/fastapi/scripts/run_authored_checkpoint_repair_probe.ps1: secrets are
# decrypted only in memory, are never printed or persisted, and the process
# environment is restored immediately after the child exits. The child opens a
# read-only connection; a live series is writing to the same database.
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
$fastApiRoot = Join-Path $repoRoot 'servers\fastapi'
. (Join-Path $repoRoot 'scripts\local-stack-lib.ps1')

$stack = Read-StackEnvironment (
    Join-Path $repoRoot 'app_data\public-v2-local\local-stack-env.dpapi.xml'
)
$childEnvironment = @{}
foreach ($key in @('DATABASE_URL', 'APP_DATA_DIRECTORY', 'USER_CONFIG_PATH')) {
    $childEnvironment[$key] = $stack['api'][$key]
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
    $script = Join-Path $PSScriptRoot 'aggregate_speed.py'
    & (Join-Path $fastApiRoot '.venv\Scripts\python.exe') $script $outputPath
    $childExitCode = $LASTEXITCODE
}
finally {
    if ($locationPushed) { Pop-Location }
    foreach ($key in $allowedKeys) {
        [Environment]::SetEnvironmentVariable($key, $previous[$key], 'Process')
    }
}

exit $childExitCode
