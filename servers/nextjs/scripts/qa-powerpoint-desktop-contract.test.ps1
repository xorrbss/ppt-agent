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

$source = Get-Content -Raw -LiteralPath $scriptPath
foreach ($required in @(
  "powerpoint-attended-repair-observation.json",
  "authored-powerpoint-attended-repair-observation/v1",
  "powerpoint-process-census.json",
  "authored-powerpoint-process-census/v1",
  "Start-PowerPointWindowObserver"
)) {
  if (-not $source.Contains($required)) {
    throw "qa-powerpoint-desktop-authority-contract-missing:$required"
  }
}

Write-Output "qa-powerpoint-desktop-contract: PASS"
