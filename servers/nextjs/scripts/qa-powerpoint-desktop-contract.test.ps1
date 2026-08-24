$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$scriptPath = Join-Path $PSScriptRoot "qa-powerpoint-desktop.ps1"
$tokens = $null
$parseErrors = $null
$ast = [Management.Automation.Language.Parser]::ParseFile(
  $scriptPath,
  [ref]$tokens,
  [ref]$parseErrors
)
if ($parseErrors.Count -ne 0) {
  throw "qa-powerpoint-desktop-parse-errors:$($parseErrors.Count)"
}
foreach ($functionName in @(
  "Get-Sha256Text",
  "Get-PowerPointApplicationIdentity",
  "Complete-PowerPointFallbackWindowBinding",
  "Get-UnexpectedPowerPointObservation",
  "Get-PowerPointObserverEpochGuarantee",
  "Read-SemanticOwnershipAlternativeText"
)) {
  $functionAst = $ast.Find({
    param($node)
    $node -is [Management.Automation.Language.FunctionDefinitionAst] -and
      $node.Name -eq $functionName
  }, $true)
  if ($null -eq $functionAst) {
    throw "qa-powerpoint-desktop-function-missing:$functionName"
  }
  Invoke-Expression $functionAst.Extent.Text
}

$semanticOwnershipPrefix = "presenton-semantic-raster-ownership-v1:"
$qids = @("s01-title-main", "s01-chart-revenue")
$payload = [ordered]@{
  schemaVersion = "presenton-semantic-raster-ownership/v1"
  slideNumber = 1
  qids = $qids
  qidSetDigest = Get-Sha256Text ($qids -join "`n")
}
$encoded = [Convert]::ToBase64String(
  [Text.Encoding]::UTF8.GetBytes(($payload | ConvertTo-Json -Compress))
).TrimEnd('=').Replace('+', '-').Replace('/', '_')
$readback = Read-SemanticOwnershipAlternativeText `
  ($semanticOwnershipPrefix + $encoded) 1
if (($readback.Qids -join '|') -ne ($qids -join '|')) {
  throw "semantic-ownership-qid-readback-mismatch"
}
if ($readback.QidSetDigest -ne $payload.qidSetDigest) {
  throw "semantic-ownership-digest-readback-mismatch"
}

$badPayload = [ordered]@{
  schemaVersion = $payload.schemaVersion
  slideNumber = $payload.slideNumber
  qids = $payload.qids
  qidSetDigest = "sha256:" + ("0" * 64)
}
$badEncoded = [Convert]::ToBase64String(
  [Text.Encoding]::UTF8.GetBytes(($badPayload | ConvertTo-Json -Compress))
).TrimEnd('=').Replace('+', '-').Replace('/', '_')
$rejected = $false
try {
  Read-SemanticOwnershipAlternativeText `
    ($semanticOwnershipPrefix + $badEncoded) 1 | Out-Null
} catch {
  $rejected = $_.Exception.Message -match "failed validation"
}
if (-not $rejected) {
  throw "semantic-ownership-digest-mutation-not-rejected"
}

$observation = Get-UnexpectedPowerPointObservation 41 `
  @([PSCustomObject]@{ ProcessId = 41 }, [PSCustomObject]@{ ProcessId = 99 }) `
  @([PSCustomObject]@{ ProcessId = 41 }, [PSCustomObject]@{ ProcessId = 100 })
if ($observation.UnexpectedPowerPointProcessCount -ne 2) {
  throw "unexpected-powerpoint-process-count-not-calculated"
}
if (($observation.UnexpectedPowerPointProcessIds -join ',') -ne '99,100') {
  throw "unexpected-powerpoint-process-ids-not-deduplicated"
}

$oldEpoch = "00000000-0000-4000-8000-000000000041"
$newEpoch = "00000000-0000-4000-8000-000000000042"
$oldObservation = [PSCustomObject]@{
  EpochId = $oldEpoch
  Processes = @([PSCustomObject]@{ EpochId = $oldEpoch; ProcessId = 41 })
  Windows = @()
}
$newObservation = [PSCustomObject]@{
  EpochId = $newEpoch
  Processes = @([PSCustomObject]@{ EpochId = $newEpoch; ProcessId = 42 })
  Windows = @([PSCustomObject]@{ EpochId = $newEpoch; ProcessId = 42; Hwnd = 4200 })
}
Get-PowerPointObserverEpochGuarantee $oldObservation $oldEpoch 41 $false | Out-Null
Get-PowerPointObserverEpochGuarantee $newObservation $newEpoch 42 $true | Out-Null
$combinedRejected = $false
try {
  $combined = [PSCustomObject]@{
    EpochId = $newEpoch
    Processes = @($oldObservation.Processes) + @($newObservation.Processes)
    Windows = @($newObservation.Windows)
  }
  Get-PowerPointObserverEpochGuarantee $combined $newEpoch 42 $true | Out-Null
} catch {
  $combinedRejected = $_.Exception.Message -match "epoch identity"
}
if (-not $combinedRejected) {
  throw "fallback-observer-epoch-history-not-rejected"
}

$provisionalIdentity = [PSCustomObject]@{ Hwnd = [int64]0; ProcessId = 42 }
$postOpenIdentity = [PSCustomObject]@{ Hwnd = [int64]4200; ProcessId = 42 }
$completedIdentity = Complete-PowerPointFallbackWindowBinding `
  $provisionalIdentity $postOpenIdentity
if ($completedIdentity.Hwnd -ne 4200 -or $completedIdentity.ProcessId -ne 42) {
  throw "fallback-post-open-window-binding-not-completed"
}
$postOpenMismatchRejected = $false
try {
  Complete-PowerPointFallbackWindowBinding $provisionalIdentity `
    ([PSCustomObject]@{ Hwnd = [int64]9900; ProcessId = 99 }) | Out-Null
} catch {
  $postOpenMismatchRejected = $_.Exception.Message -match "post-open HWND/PID"
}
if (-not $postOpenMismatchRejected) {
  throw "fallback-post-open-window-pid-mismatch-not-rejected"
}

$source = Get-Content -Raw -LiteralPath $scriptPath
foreach ($required in @(
  "powerpoint-attended-repair-observation.json",
  "authored-powerpoint-attended-repair-observation/v1",
  "powerpoint-process-census.json",
  "authored-powerpoint-process-census/v1",
  "Start-PowerPointWindowObserver",
  "PresentonPowerPointApplicationWindowIdentity",
  "`$candidateIdentity.ProcessId -ne [int]`$launched.Id",
  "AttachedApplicationHwnd",
  "AttachedApplicationProcessId",
  "ApplicationWindowBindingPhase",
  "post-first-open-enumerated-frame-hwnd",
  "VisiblePptFrameHwndsForProcess",
  "unique visible PPTFrameClass window",
  "ObservedPowerPointProcesses",
  "DiscardedObserverEpochs",
  "safe-mode-rot-unavailable",
  "unexpectedPowerPointProcessCount = [int]`$unexpectedPowerPointObservation.UnexpectedPowerPointProcessCount"
)) {
  if (-not $source.Contains($required)) {
    throw "qa-powerpoint-desktop-authority-contract-missing:$required"
  }
}

$firstCanonicalOpen = $source.IndexOf('$sourceDeck = $application.Presentations.Open')
$finalFallbackBinding = $source.IndexOf('$applicationIdentity = Complete-PowerPointFallbackWindowBinding')
if ($firstCanonicalOpen -lt 0 -or $finalFallbackBinding -le $firstCanonicalOpen) {
  throw "fallback-window-binding-must-follow-first-canonical-open"
}

Write-Output "qa-powerpoint-desktop-contract: PASS"
