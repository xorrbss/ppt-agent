[CmdletBinding()]
param(
    # Stage label, e.g. "gate-shadow", "gate-enforce", "normalization-tree",
    # "token-mode", "repair-loop-n1", "repair-loop-n2". See RUNBOOK.md section 1.
    [Parameter(Mandatory)]
    [ValidateNotNullOrEmpty()]
    [string]$Stage,

    [ValidateRange(1, 50)]
    [int]$RunCount = 5,

    [string]$SeriesRoot = (Join-Path $PSScriptRoot '.'),

    # The knob values this stage is supposed to be running under, per
    # RUNBOOK.md section 1's table. This script never writes them (that is a
    # coordinator action against /api/v1/admin/provider-settings followed by
    # a stack restart, RUNBOOK.md section 2) -- it only verifies the running
    # stack already reports them before spending any provider quota.
    [Parameter(Mandatory)]
    [hashtable]$StageKnobs,

    [string]$BaseUrl = 'http://localhost:5000',

    # Reuse the existing single-generation harness and audit script as-is.
    [string]$RunScript = (Join-Path $PSScriptRoot '..\authored-full-product-validation-20260910\run.ps1'),
    [string]$AuditScript = (Join-Path $PSScriptRoot '..\authored-full-product-validation-20260910\audit-full-run.ps1'),

    [string]$SourcePath,
    [ValidateRange(1, 10000)]
    [int]$ExpectedSlides = 35,
    [ValidateRange(0, 100000)]
    [int]$ExpectedSourceBlocks = 289,
    [ValidatePattern('^[a-fA-F0-9]{64}$')]
    [string]$ExpectedInputSha256 = '7746aaeddbf1222fd73c9870ac7297af98446b8e5df22d52c809b4d69b5340ae',

    # Stop the stage at the first run that does not complete and pass audit,
    # rather than burning the remaining runs' quota on an already-failing
    # stage. RUNBOOK.md section 5 ("stop conditions") is the coordinator's
    # cross-stage decision; this is the narrower in-stage guard.
    [bool]$StopOnFailure = $true
)

# This script drives the *existing* run.ps1/audit-full-run.ps1 for N sequential
# runs of one stage and records a per-run summary. It sets no runtime knob,
# restarts nothing, and touches nothing outside $SeriesRoot/$Stage.
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

function Get-JsonProperty {
    param($Object, [string]$Name)
    if ($null -eq $Object) { return $null }
    $property = $Object.PSObject.Properties[$Name]
    if ($null -eq $property) { return $null }
    return $property.Value
}

function ConvertTo-ComparableString($Value) {
    if ($Value -is [bool]) { return $(if ($Value) { 'true' } else { 'false' }) }
    if ($null -eq $Value) { return '' }
    return [string]$Value
}

if (-not (Test-Path -LiteralPath $RunScript -PathType Leaf)) { throw "RunScript not found: $RunScript" }
if (-not (Test-Path -LiteralPath $AuditScript -PathType Leaf)) { throw "AuditScript not found: $AuditScript" }

$stageDir = Join-Path (Resolve-Path -LiteralPath $SeriesRoot).Path $Stage
New-Item -ItemType Directory -Force -Path $stageDir | Out-Null

Write-Output "PRECHECK verifying live provider-settings match -StageKnobs before spending any quota"
$observed = Invoke-RestMethod -Method Get -Uri "$BaseUrl/api/v1/admin/provider-settings"
$mismatches = [System.Collections.Generic.List[object]]::new()
foreach ($key in $StageKnobs.Keys) {
    $expected = ConvertTo-ComparableString $StageKnobs[$key]
    $actual = ConvertTo-ComparableString (Get-JsonProperty $observed $key)
    if ($actual -ne $expected) {
        $mismatches.Add([pscustomobject]@{ key = $key; expected = $expected; actual = $actual })
    }
}
$manifestPath = Join-Path $stageDir '00-stage-manifest.json'
$startedAt = [DateTimeOffset]::UtcNow
if ($mismatches.Count -gt 0) {
    [pscustomobject]@{
        stage = $Stage
        runCount = $RunCount
        stageKnobsRequested = $StageKnobs
        observedProviderSettings = $observed
        startedAt = $startedAt
        stoppedBeforeFirstRun = $true
        stopReason = 'provider-settings mismatch: the coordinator has not written and restarted for this stage yet'
        mismatches = $mismatches
    } | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $manifestPath -Encoding utf8
    Write-Error "STOP: live provider-settings do not match -StageKnobs for stage '$Stage'. See $manifestPath. No run was started."
    exit 1
}

[pscustomobject]@{
    stage = $Stage
    runCount = $RunCount
    stageKnobsRequested = $StageKnobs
    observedProviderSettings = $observed
    startedAt = $startedAt
    stoppedBeforeFirstRun = $false
} | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $manifestPath -Encoding utf8

$summaries = [System.Collections.Generic.List[object]]::new()
$stopReason = $null

for ($i = 1; $i -le $RunCount; $i++) {
    $runDir = Join-Path $stageDir "run-$i"
    Write-Output "RUN $i/$RunCount -> $runDir"
    $runStarted = [DateTimeOffset]::UtcNow
    $runArgs = @{ OutputDirectory = $runDir }
    if ($PSBoundParameters.ContainsKey('SourcePath')) { $runArgs.SourcePath = $SourcePath }
    if ($PSBoundParameters.ContainsKey('BaseUrl')) { $runArgs.BaseUrl = $BaseUrl }

    $generationLog = Join-Path $stageDir "run-$i.log"
    $generationExitCode = 0
    $generationError = $null
    try {
        & $RunScript @runArgs 2>&1 | Tee-Object -FilePath $generationLog
        $generationExitCode = $LASTEXITCODE
    }
    catch {
        $generationExitCode = 1
        $generationError = $_.Exception.Message
        $generationError | Add-Content -LiteralPath $generationLog -Encoding utf8
    }

    $auditVerdict = 'not-run'
    $auditJsonPath = $null
    $auditError = $null
    if ($generationExitCode -eq 0) {
        $auditJsonPath = Join-Path $runDir 'audit.json'
        try {
            $auditArgs = @{
                RunDirectory = $runDir
                ExpectedSlides = $ExpectedSlides
                ExpectedSourceBlocks = $ExpectedSourceBlocks
                ExpectedInputSha256 = $ExpectedInputSha256
            }
            $auditJson = & $AuditScript @auditArgs
            $auditExitCode = $LASTEXITCODE
            $auditJson | Set-Content -LiteralPath $auditJsonPath -Encoding utf8
            $auditVerdict = ($auditJson | ConvertFrom-Json).verdict
            if ($auditExitCode -ne 0 -and $auditVerdict -eq 'passed') {
                # Should not happen -- treat a verdict/exit-code disagreement as a failure to be safe.
                $auditVerdict = 'audit-exit-code-mismatch'
            }
        }
        catch {
            $auditVerdict = 'audit-error'
            $auditError = $_.Exception.Message
        }
    }

    $operationId = $null
    $admissionPath = Join-Path $runDir '05-admission.json'
    if (Test-Path -LiteralPath $admissionPath -PathType Leaf) {
        $admission = Get-Content -LiteralPath $admissionPath -Raw -Encoding UTF8 | ConvertFrom-Json
        $operationId = Get-JsonProperty $admission 'operation_id'
    }

    $runFinished = [DateTimeOffset]::UtcNow
    $summary = [pscustomobject]@{
        runIndex = $i
        runDirectory = $runDir
        startedAt = $runStarted
        finishedAt = $runFinished
        wallSeconds = [Math]::Round(($runFinished - $runStarted).TotalSeconds, 1)
        operationId = $operationId
        generationExitCode = $generationExitCode
        generationError = $generationError
        generationLog = $generationLog
        auditVerdict = $auditVerdict
        auditJsonPath = $auditJsonPath
        auditError = $auditError
    }
    $summaries.Add($summary)
    Write-Output ("RUN {0}/{1} DONE exitCode={2} auditVerdict={3}" -f $i, $RunCount, $generationExitCode, $auditVerdict)

    $summariesPath = Join-Path $stageDir 'series-summary.json'
    $summaries | ConvertTo-Json -Depth 10 | Set-Content -LiteralPath $summariesPath -Encoding utf8

    $runFailed = ($generationExitCode -ne 0) -or ($auditVerdict -ne 'passed')
    if ($runFailed -and $StopOnFailure) {
        $stopReason = "run $i failed (generationExitCode=$generationExitCode, auditVerdict=$auditVerdict); StopOnFailure is set"
        Write-Warning "STOP: $stopReason"
        break
    }
}

$manifest = Get-Content -LiteralPath $manifestPath -Raw -Encoding UTF8 | ConvertFrom-Json
$manifest | Add-Member -NotePropertyName finishedAt -NotePropertyValue ([DateTimeOffset]::UtcNow) -Force
$manifest | Add-Member -NotePropertyName runsCompleted -NotePropertyValue $summaries.Count -Force
$manifest | Add-Member -NotePropertyName stopReason -NotePropertyValue $stopReason -Force
$manifest | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $manifestPath -Encoding utf8

$failedCount = @($summaries | Where-Object { $_.generationExitCode -ne 0 -or $_.auditVerdict -ne 'passed' }).Count
if ($summaries.Count -lt $RunCount -or $failedCount -gt 0) {
    Write-Output ("STAGE '{0}' INCOMPLETE: {1}/{2} runs recorded, {3} failed" -f $Stage, $summaries.Count, $RunCount, $failedCount)
    exit 2
}
Write-Output ("STAGE '{0}' COMPLETE: {1}/{1} runs passed" -f $Stage, $RunCount)
exit 0
