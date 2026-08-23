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
  "ObservedPowerPointProcesses",
  "DiscardedObserverEpochs",
  "safe-mode-rot-unavailable",
  "unexpectedPowerPointProcessCount = [int]`$unexpectedPowerPointObservation.UnexpectedPowerPointProcessCount"
)) {
  if (-not $source.Contains($required)) {
    throw "qa-powerpoint-desktop-authority-contract-missing:$required"
  }
}

Write-Output "qa-powerpoint-desktop-contract: PASS"
