[CmdletBinding()]
param(
    [Parameter(Mandatory)]
    [Guid]$Job,

    [Parameter(Mandatory)]
    [ValidateNotNullOrEmpty()]
    [string]$Slides,

    [switch]$ScanOnly,

    [Parameter(Mandatory)]
    [ValidateNotNullOrEmpty()]
    [string]$Out,

    [ValidateRange(1, 100)]
    [int]$MaxCycles = 2
)

# Sanctioned local wrapper: secrets are decrypted only in memory, are never
# printed or persisted, and are removed/restored immediately after the child.
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..\..\..')).Path
$fastApiRoot = Join-Path $repoRoot 'servers\fastapi'
. (Join-Path $repoRoot 'scripts\local-stack-lib.ps1')

$stack = Read-StackEnvironment (
    Join-Path $repoRoot 'app_data\public-v2-local\local-stack-env.dpapi.xml'
)
$childEnvironment = @{}
foreach ($entry in $stack['worker-generation'].GetEnumerator()) {
    $childEnvironment[[string]$entry.Key] = $entry.Value
}
foreach ($key in @('DATABASE_URL', 'APP_DATA_DIRECTORY', 'USER_CONFIG_PATH')) {
    if (-not $childEnvironment.ContainsKey($key)) {
        $childEnvironment[$key] = $stack['api'][$key]
    }
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
    $probeArgs = @(
        'run', 'python', 'scripts/authored_checkpoint_repair_probe.py',
        '--job', $Job.ToString(),
        '--slides', $Slides,
        '--out', $outputPath,
        '--max-cycles', $MaxCycles.ToString()
    )
    if ($ScanOnly) { $probeArgs += '--scan-only' }
    & uv @probeArgs
    $probeExitCode = $LASTEXITCODE
}
finally {
    if ($locationPushed) { Pop-Location }
    foreach ($key in $allowedKeys) {
        [Environment]::SetEnvironmentVariable($key, $previous[$key], 'Process')
    }
}

exit $probeExitCode
