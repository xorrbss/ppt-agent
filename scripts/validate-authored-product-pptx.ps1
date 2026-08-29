[CmdletBinding()]
param(
  [string]$Pptx,
  [Parameter(Mandatory = $true)]
  [string]$OutputDirectory,
  [string]$OwnershipManifest,
  [string]$PresentationId,
  [ValidateRange(0, [int]::MaxValue)]
  [int]$PresentationRevision = -1,
  [switch]$RunLibreOffice,
  [switch]$PlanOnly,
  [string]$PowerPointQaScript = (Join-Path $PSScriptRoot "..\servers\nextjs\scripts\qa-powerpoint-desktop.ps1"),
  [string]$OfficeMatrixScript = (Join-Path $PSScriptRoot "..\servers\nextjs\lib\ooxml-vnext\tools\office-compatibility-matrix.ps1")
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function Get-Sha256([string]$Path) {
  "sha256:" + (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToLowerInvariant()
}

function Write-Json([string]$Path, $Value) {
  [IO.File]::WriteAllText(
    $Path,
    ($Value | ConvertTo-Json -Depth 32),
    [Text.UTF8Encoding]::new($false)
  )
}

function Get-OfficeProcessCensus {
  @(
    Get-CimInstance Win32_Process -ErrorAction Stop |
      Where-Object { $_.Name -in @("POWERPNT.EXE", "soffice.exe", "soffice.bin") } |
      Sort-Object Name, ProcessId |
      ForEach-Object {
        [PSCustomObject]@{
          name = [string]$_.Name
          processId = [int]$_.ProcessId
          parentProcessId = [int]$_.ParentProcessId
          creationDateUtc = if ($null -ne $_.CreationDate) {
            ([datetime]$_.CreationDate).ToUniversalTime().ToString("o")
          } else { $null }
          executablePath = [string]$_.ExecutablePath
          commandLine = [string]$_.CommandLine
        }
      }
  )
}

function Assert-FreshDirectory([string]$Path) {
  $full = [IO.Path]::GetFullPath($Path)
  if (Test-Path -LiteralPath $full) {
    throw "output-directory-must-be-fresh:$full"
  }
  [IO.Directory]::CreateDirectory($full) | Out-Null
  $full
}

function Read-ProductOwnershipManifest(
  [string]$Path,
  [string]$ExpectedPresentationId,
  [int]$ExpectedRevision,
  [string]$ExpectedPptxSha256
) {
  $resolved = (Resolve-Path -LiteralPath $Path).Path
  $manifest = Get-Content -Raw -LiteralPath $resolved | ConvertFrom-Json
  $required = @(
    "schemaVersion", "presentationId", "presentationRevision", "pptxSha256",
    "deckDigest", "sceneDigest", "slides"
  )
  if (
    (@($manifest.PSObject.Properties.Name | Sort-Object) -join "|") -ne
    (@($required | Sort-Object) -join "|") -or
    $manifest.schemaVersion -ne "authored-product-pptx-ownership/v1" -or
    [string]::IsNullOrWhiteSpace([string]$manifest.presentationId) -or
    [int]$manifest.presentationRevision -lt 0 -or
    [string]$manifest.pptxSha256 -notmatch "^sha256:[0-9a-f]{64}$" -or
    [string]$manifest.deckDigest -notmatch "^sha256:[0-9a-f]{64}$" -or
    [string]$manifest.sceneDigest -notmatch "^sha256:[0-9a-f]{64}$" -or
    @($manifest.slides).Count -lt 1
  ) {
    throw "invalid-product-ownership-manifest"
  }
  if ($manifest.pptxSha256 -ne $ExpectedPptxSha256) {
    throw "ownership-manifest-pptx-digest-mismatch"
  }
  if ($ExpectedPresentationId -and $manifest.presentationId -ne $ExpectedPresentationId) {
    throw "ownership-manifest-presentation-id-mismatch"
  }
  if ($ExpectedRevision -ge 0 -and [int]$manifest.presentationRevision -ne $ExpectedRevision) {
    throw "ownership-manifest-presentation-revision-mismatch"
  }
  $allQids = @()
  $slides = @(
    foreach ($slide in @($manifest.slides)) {
      $keys = @("slideNumber", "qids")
      if (
        (@($slide.PSObject.Properties.Name | Sort-Object) -join "|") -ne
          (@($keys | Sort-Object) -join "|") -or
        [int]$slide.slideNumber -lt 1 -or @($slide.qids).Count -lt 1
      ) { throw "invalid-product-ownership-manifest-slide" }
      $qids = @($slide.qids | ForEach-Object { [string]$_ })
      if (
        @($qids | Where-Object { $_ -notmatch "^[A-Za-z][A-Za-z0-9._:-]{0,127}$" }).Count -ne 0 -or
        @($qids | Sort-Object -Unique).Count -ne $qids.Count
      ) { throw "invalid-product-ownership-manifest-qids" }
      $allQids += $qids
      [PSCustomObject]@{ slideNumber = [int]$slide.slideNumber; qids = $qids }
    }
  )
  if (@($slides.slideNumber | Sort-Object -Unique).Count -ne $slides.Count) {
    throw "product-ownership-manifest-duplicates"
  }
  [PSCustomObject]@{
    path = $resolved
    sha256 = Get-Sha256 $resolved
    presentationId = [string]$manifest.presentationId
    presentationRevision = [int]$manifest.presentationRevision
    pptxSha256 = [string]$manifest.pptxSha256
    deckDigest = [string]$manifest.deckDigest
    sceneDigest = [string]$manifest.sceneDigest
    slides = $slides
    qids = @($allQids | Sort-Object)
  }
}

function Compare-QidReadback($ShapeReadback, $ExpectedSlides) {
  $actual = @()
  foreach ($shape in @($ShapeReadback)) {
    foreach ($qid in @($shape.Qids)) {
      $actual += [PSCustomObject]@{ slideNumber = [int]$shape.SlideNumber; qid = [string]$qid; name = [string]$shape.Name; disposition = [string]$shape.OwnershipDisposition }
    }
  }
  $expected = @()
  foreach ($slide in @($ExpectedSlides)) {
    foreach ($qid in @($slide.qids)) {
      $expected += [PSCustomObject]@{ slideNumber = [int]$slide.slideNumber; qid = [string]$qid }
    }
  }
  $actualKeys = @($actual | ForEach-Object { "{0}|{1}" -f $_.slideNumber, $_.qid })
  $expectedKeys = @($expected | ForEach-Object { "{0}|{1}" -f $_.slideNumber, $_.qid })
  $duplicates = @($actualKeys | Group-Object | Where-Object Count -gt 1 | ForEach-Object Name)
  $missing = @($expectedKeys | Where-Object { $_ -notin $actualKeys })
  $unexpected = @($actualKeys | Where-Object { $_ -notin $expectedKeys })
  [PSCustomObject]@{
    exact = ($duplicates.Count -eq 0 -and $missing.Count -eq 0 -and $unexpected.Count -eq 0)
    expectedCount = $expectedKeys.Count
    actualCount = $actualKeys.Count
    duplicates = $duplicates
    missing = $missing
    unexpected = $unexpected
    nativeCount = @($actual | Where-Object { $_.disposition -eq "native" }).Count
    rasterCount = @($actual | Where-Object { $_.disposition -eq "raster" }).Count
    unsupportedDispositionCount = @($actual | Where-Object { $_.disposition -notin @("native", "raster") }).Count
    entries = $actual
  }
}

$resolvedOutput = Assert-FreshDirectory $OutputDirectory
$resolvedQa = (Resolve-Path -LiteralPath $PowerPointQaScript).Path
$resolvedMatrix = (Resolve-Path -LiteralPath $OfficeMatrixScript).Path
$plan = [ordered]@{
  schemaVersion = "authored-product-pptx-validation-plan/v1"
  createdAtUtc = [DateTime]::UtcNow.ToString("o")
  planOnly = [bool]$PlanOnly
  officeStarted = $false
  inputPptx = $Pptx
  qaCollector = [ordered]@{ path = $resolvedQa; sha256 = Get-Sha256 $resolvedQa }
  libreOfficeCollector = [ordered]@{ path = $resolvedMatrix; sha256 = Get-Sha256 $resolvedMatrix }
  requiredManifestSchema = "authored-product-pptx-ownership/v1"
  requiredManifestFields = @("presentationId", "presentationRevision", "pptxSha256", "deckDigest", "sceneDigest", "slides[].slideNumber", "slides[].qids")
  refusalRules = @(
    "Refuse any existing POWERPNT/soffice process before Office work.",
    "Refuse a missing or digest-mismatched product ownership manifest.",
    "Refuse a non-exact COM application PID/HWND binding or any survivor.",
    "Refuse duplicate, missing, or unexpected slide/QID ownership after first-open or reopen."
  )
}
if ($PlanOnly) {
  Write-Json (Join-Path $resolvedOutput "plan-only.json") $plan
  $plan
  exit 0
}

if (-not $Pptx -or -not $OwnershipManifest -or -not $PresentationId -or $PresentationRevision -lt 0) {
  throw "pptx-ownership-manifest-presentation-id-and-revision-are-required"
}
$resolvedPptx = (Resolve-Path -LiteralPath $Pptx).Path
if ([IO.Path]::GetExtension($resolvedPptx).ToLowerInvariant() -ne ".pptx") {
  throw "product-pptx-extension-required"
}
$inputDigestBefore = Get-Sha256 $resolvedPptx
$expected = Read-ProductOwnershipManifest $OwnershipManifest $PresentationId $PresentationRevision $inputDigestBefore
$before = @(Get-OfficeProcessCensus)
Write-Json (Join-Path $resolvedOutput "preflight.json") ([ordered]@{
  schemaVersion = "authored-product-pptx-preflight/v1"; observedAtUtc = [DateTime]::UtcNow.ToString("o")
  productPptx = [ordered]@{ path = $resolvedPptx; sha256 = $inputDigestBefore }
  ownershipManifest = $expected
  officeProcesses = $before
})
if ($before.Count -ne 0) { throw "ambiguous-office-process-ownership" }

# The collector opens read-only, creates/saves only a copy below OutputDirectory,
# binds COM HWND/PID to the single newly launched process, then reopens that copy.
& $resolvedQa -Presentation @($resolvedPptx) -OutputDirectory $resolvedOutput
$desktopPath = Join-Path $resolvedOutput "powerpoint-desktop-validation.json"
if (-not (Test-Path -LiteralPath $desktopPath -PathType Leaf)) { throw "powerpoint-desktop-receipt-missing" }
$desktop = Get-Content -Raw -LiteralPath $desktopPath | ConvertFrom-Json
$afterPowerPoint = @(Get-OfficeProcessCensus)
if (@($desktop.PowerPointProcessCensusBefore).Count -ne 0 -or @($desktop.PowerPointProcessCensusAfter).Count -ne 0 -or $afterPowerPoint.Count -ne 0) {
  throw "office-process-census-not-zero"
}
if ([int]$desktop.LaunchedProcessId -ne [int]$desktop.AttachedApplicationProcessId -or [int64]$desktop.AttachedApplicationHwnd -eq 0) {
  throw "powerpoint-owned-com-binding-not-exact"
}
$presentation = @($desktop.Presentations | Where-Object { $_.Path -eq $resolvedPptx })
if ($presentation.Count -ne 1) { throw "powerpoint-product-presentation-receipt-missing" }
$item = $presentation[0]
$sourceQids = Compare-QidReadback $item.SourceShapeReadback $expected.slides
$reopenQids = Compare-QidReadback $item.ShapeReadback $expected.slides
$inputDigestAfterPowerPoint = Get-Sha256 $resolvedPptx
if (-not $item.ReadbackPassed -or -not $item.FirstOpenReopenedRenderHashesMatch -or
    -not $item.ShapeReadbackPreserved -or -not $item.SemanticOwnershipPreserved -or
    -not $sourceQids.exact -or -not $reopenQids.exact -or
    $sourceQids.unsupportedDispositionCount -ne 0 -or $reopenQids.unsupportedDispositionCount -ne 0 -or
    $inputDigestBefore -ne $inputDigestAfterPowerPoint) {
  throw "powerpoint-product-readback-or-qid-validation-failed"
}

$libreOffice = [ordered]@{ status = "not-requested"; receipt = $null }
if ($RunLibreOffice) {
  if (@(Get-OfficeProcessCensus).Count -ne 0) { throw "ambiguous-office-process-before-libreoffice" }
  $loReceiptPath = Join-Path $resolvedOutput "libreoffice-compatibility.json"
  & $resolvedMatrix -InputPath $resolvedPptx -OutputJson $loReceiptPath -RunLibreOffice
  if ($LASTEXITCODE -ne 0 -or -not (Test-Path -LiteralPath $loReceiptPath -PathType Leaf)) {
    throw "libreoffice-comparison-failed"
  }
  $libreOffice.receipt = $loReceiptPath
  $libreOffice.status = (Get-Content -Raw -LiteralPath $loReceiptPath | ConvertFrom-Json).matrix.libreOffice.status
  if (@(Get-OfficeProcessCensus).Count -ne 0) { throw "office-process-survivor-after-libreoffice" }
}

$final = [ordered]@{
  schemaVersion = "authored-product-pptx-validation/v1"
  authorityDomain = "actual_product"
  completedAtUtc = [DateTime]::UtcNow.ToString("o")
  presentationId = $expected.presentationId
  presentationRevision = $expected.presentationRevision
  productPptx = [ordered]@{ path = $resolvedPptx; sha256Before = $inputDigestBefore; sha256After = (Get-Sha256 $resolvedPptx); unchanged = $inputDigestBefore -eq (Get-Sha256 $resolvedPptx) }
  manifest = [ordered]@{ path = $expected.path; sha256 = $expected.sha256; deckDigest = $expected.deckDigest; sceneDigest = $expected.sceneDigest }
  ownedPowerPoint = [ordered]@{ launchedProcessId = $desktop.LaunchedProcessId; attachedApplicationProcessId = $desktop.AttachedApplicationProcessId; hwnd = $desktop.AttachedApplicationHwnd; activationMethod = $desktop.ActivationMethod; censusBefore = $desktop.PowerPointProcessCensusBefore; censusAfter = $desktop.PowerPointProcessCensusAfter }
  editableNativeRaster = [ordered]@{ source = $sourceQids; reopened = $reopenQids; readbackPreserved = [bool]$item.ShapeReadbackPreserved; renderHashesMatch = [bool]$item.FirstOpenReopenedRenderHashesMatch; savedCopy = $item.ReopenedPath }
  powerpointReceipt = $desktopPath
  libreOffice = $libreOffice
  status = "passed"
}
Write-Json (Join-Path $resolvedOutput "product-pptx-validation.json") $final
$final
