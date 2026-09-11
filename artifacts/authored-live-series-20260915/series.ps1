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
    # coordinator action against the DPAPI environment store via
    # scripts\set-local-stack-knob.ps1 followed by a stack restart,
    # RUNBOOK.md section 2) -- it only verifies the running stack already
    # resolves them before spending any provider quota.
    [Parameter(Mandatory)]
    [hashtable]$StageKnobs,

    [string]$BaseUrl = 'http://localhost:5000',

    # The precheck resolves the stage knobs the way the running code does, per
    # component, through this script. It deliberately does NOT gate on
    # GET /api/v1/admin/provider-settings: every knob in the stage table is
    # registered targets: ["fastapi.runtime"], which that endpoint neither
    # stores nor returns, so gating on it could only ever produce a false
    # mismatch -- or, for a knob it silently drops on write, a false pass.
    [string]$KnobVerifyScript = (Join-Path $PSScriptRoot '..\..\scripts\verify-local-stack-knobs.ps1'),

    # Every component that participates in a run must resolve the stage knobs,
    # not just the API: generation runs in worker-generation and export in
    # worker-export, each its own OS process with its own environment.
    [string[]]$KnobComponents = @('api', 'worker-generation', 'worker-export'),

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
if (-not (Test-Path -LiteralPath $KnobVerifyScript -PathType Leaf)) { throw "KnobVerifyScript not found: $KnobVerifyScript" }

$stageDir = Join-Path (Resolve-Path -LiteralPath $SeriesRoot).Path $Stage
New-Item -ItemType Directory -Force -Path $stageDir | Out-Null

Write-Output "PRECHECK resolving the stage knobs per component, the way the running code resolves them"
$knobReportJson = & $KnobVerifyScript -AsJson -Components $KnobComponents
if ($LASTEXITCODE -ne 0) { throw "knob verification failed (exit $LASTEXITCODE): $KnobVerifyScript" }
$knobReport = (($knobReportJson | Out-String) | ConvertFrom-Json)

$mismatches = [System.Collections.Generic.List[object]]::new()
foreach ($component in $KnobComponents) {
    $entry = Get-JsonProperty $knobReport.components $component
    if ($null -eq $entry) {
        $mismatches.Add([pscustomobject]@{
            component = $component
            key = '(component)'
            expected = 'probed'
            actual = 'missing from the knob report'
        })
        continue
    }

    # A knob written to the store but not picked up by a process that started
    # before that write is exactly the failure the old GET-based precheck
    # could not see. Treat it as a mismatch, not a warning.
    if ($entry.runtime.restartRequired) {
        $mismatches.Add([pscustomobject]@{
            component = $component
            key = '(process)'
            expected = 'started after the store was written'
            actual = $entry.runtime.reason
        })
    }

    foreach ($key in $StageKnobs.Keys) {
        $expected = ConvertTo-ComparableString $StageKnobs[$key]
        $property = $entry.resolved.PSObject.Properties[$key]
        if ($null -eq $property) {
            # An unknown knob must fail loudly: silently "matching" a key the
            # probe does not resolve is how a stage runs under the wrong
            # configuration and still records a clean manifest.
            $mismatches.Add([pscustomobject]@{
                component = $component
                key = $key
                expected = $expected
                actual = 'not resolved by verify-local-stack-knobs.ps1; add an accessor for it there'
            })
            continue
        }
        $actual = ConvertTo-ComparableString $property.Value
        if ($actual -ne $expected) {
            $mismatches.Add([pscustomobject]@{
                component = $component
                key = $key
                expected = $expected
                actual = $actual
            })
        }
    }
}

# Provider and model identity is still worth recording for the stage, but it
# is provenance, never the gate: none of the stage knobs live there.
$observed = $null
try { $observed = Invoke-RestMethod -Method Get -Uri "$BaseUrl/api/v1/admin/provider-settings" }
catch { $observed = [pscustomobject]@{ error = $_.Exception.Message } }

$manifestPath = Join-Path $stageDir '00-stage-manifest.json'
$startedAt = [DateTimeOffset]::UtcNow
$manifest = [pscustomobject]@{
    stage = $Stage
    runCount = $RunCount
    stageKnobsRequested = $StageKnobs
    knobComponents = $KnobComponents
    resolvedStageKnobs = $knobReport
    observedProviderSettings = $observed
    startedAt = $startedAt
    stoppedBeforeFirstRun = ($mismatches.Count -gt 0)
}
if ($mismatches.Count -gt 0) {
    $manifest | Add-Member -NotePropertyName stopReason -NotePropertyValue 'resolved stage knobs do not match -StageKnobs: the coordinator has not written and restarted for this stage yet' -Force
    $manifest | Add-Member -NotePropertyName mismatches -NotePropertyValue $mismatches -Force
    $manifest | ConvertTo-Json -Depth 10 | Set-Content -LiteralPath $manifestPath -Encoding utf8
    foreach ($mismatch in $mismatches) {
        Write-Warning ("  {0} / {1}: expected '{2}', resolved '{3}'" -f $mismatch.component, $mismatch.key, $mismatch.expected, $mismatch.actual)
    }
    Write-Error "STOP: resolved stage knobs do not match -StageKnobs for stage '$Stage'. See $manifestPath. No run was started."
    exit 1
}

$manifest | ConvertTo-Json -Depth 10 | Set-Content -LiteralPath $manifestPath -Encoding utf8
Write-Output ("PRECHECK ok: {0} knob(s) match on {1} component(s)" -f $StageKnobs.Count, $KnobComponents.Count)

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
