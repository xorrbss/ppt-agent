[CmdletBinding()]
param(
  [Parameter(Mandatory = $true)]
  [string]$SourceManifest,

  [Parameter(Mandatory = $true)]
  [string]$ProductArtifactsManifest,

  [Parameter(Mandatory = $true)]
  [string]$OutputDirectory,

  [string[]]$DeckId = @(),

  [ValidateRange(30, 1800)]
  [int]$TimeoutSeconds = 300,

  [string]$PowerPointQaScript = (
    Join-Path $PSScriptRoot "..\servers\nextjs\scripts\qa-powerpoint-desktop.ps1"
  ),

  [string]$RendererReadbackScript = (
    Join-Path $PSScriptRoot "..\docs\experiments\authored-vnext\03-ooxml-roundtrip\tools\windows-reopen.ps1"
  ),

  [string]$LibreOfficeExecutable = "C:\Program Files\LibreOffice\program\soffice.exe"
)

Set-StrictMode -Version 3.0
$ErrorActionPreference = "Stop"

function Write-Utf8Json {
  param(
    [Parameter(Mandatory = $true)][string]$Path,
    [Parameter(Mandatory = $true)]$Value
  )
  $json = $Value | ConvertTo-Json -Depth 20
  [IO.File]::WriteAllText($Path, $json, [Text.UTF8Encoding]::new($false))
}

function Get-Sha256Label {
  param([Parameter(Mandatory = $true)][string]$Path)
  "sha256:" + (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToLowerInvariant()
}

function Test-DescendantPath {
  param(
    [Parameter(Mandatory = $true)][string]$Path,
    [Parameter(Mandatory = $true)][string]$Root
  )
  $rootPath = [IO.Path]::GetFullPath($Root).TrimEnd('\', '/') + [IO.Path]::DirectorySeparatorChar
  $candidatePath = [IO.Path]::GetFullPath($Path)
  $candidatePath.StartsWith($rootPath, [StringComparison]::OrdinalIgnoreCase)
}

function Get-PptxPackageSummary {
  param([Parameter(Mandatory = $true)][string]$Path)
  $result = [ordered]@{
    readable = $false
    partCount = 0
    slidePartCount = 0
    hasContentTypes = $false
    hasRootRelationships = $false
    hasPresentationPart = $false
    hasPresentationRelationships = $false
    duplicateEntryCount = 0
    unsafeEntryCount = 0
    error = $null
  }
  try {
    Add-Type -AssemblyName System.IO.Compression.FileSystem
    $archive = [IO.Compression.ZipFile]::OpenRead($Path)
    try {
      $names = @($archive.Entries | ForEach-Object { $_.FullName.Replace('\', '/') })
      $result.partCount = $names.Count
      $result.slidePartCount = @($names | Where-Object {
        $_ -match '^ppt/slides/slide[0-9]+\.xml$'
      }).Count
      $result.hasContentTypes = $names -contains '[Content_Types].xml'
      $result.hasRootRelationships = $names -contains '_rels/.rels'
      $result.hasPresentationPart = $names -contains 'ppt/presentation.xml'
      $result.hasPresentationRelationships = $names -contains 'ppt/_rels/presentation.xml.rels'
      $result.duplicateEntryCount = @(
        $names | Group-Object | Where-Object { $_.Count -ne 1 }
      ).Count
      $result.unsafeEntryCount = @($names | Where-Object {
        $_ -match '(^|/)\.\.(/|$)' -or $_.StartsWith('/') -or $_ -match '^[A-Za-z]:'
      }).Count
      $result.readable =
        $result.partCount -gt 0 -and
        $result.slidePartCount -gt 0 -and
        $result.hasContentTypes -and
        $result.hasRootRelationships -and
        $result.hasPresentationPart -and
        $result.hasPresentationRelationships -and
        $result.duplicateEntryCount -eq 0 -and
        $result.unsafeEntryCount -eq 0
    } finally {
      $archive.Dispose()
    }
  } catch {
    $result.error = $_.Exception.Message
  }
  [PSCustomObject]$result
}

function Resolve-ManifestArtifact {
  param(
    [Parameter(Mandatory = $true)]$Artifact,
    [Parameter(Mandatory = $true)][string]$RepositoryRoot
  )
  $required = @('deckId', 'slideCount', 'operationId', 'presentationId', 'mediaType', 'path', 'bytes', 'sha256')
  foreach ($name in $required) {
    if ($null -eq $Artifact.PSObject.Properties[$name]) {
      throw "product-artifact-missing-field:$name"
    }
  }
  if (
    [string]::IsNullOrWhiteSpace([string]$Artifact.deckId) -or
    [string]::IsNullOrWhiteSpace([string]$Artifact.operationId) -or
    [string]::IsNullOrWhiteSpace([string]$Artifact.presentationId) -or
    [int]$Artifact.slideCount -lt 1 -or
    [string]$Artifact.sha256 -cnotmatch '^sha256:[0-9a-f]{64}$' -or
    [int64]$Artifact.bytes -lt 1
  ) {
    throw "product-artifact-field-invalid:$([string]$Artifact.deckId)"
  }
  $relativePath = [string]$Artifact.path
  if (
    [IO.Path]::IsPathRooted($relativePath) -or
    $relativePath -match '(^|[\\/])\.\.([\\/]|$)'
  ) {
    throw "product-artifact-path-must-be-repository-relative:$relativePath"
  }
  $resolvedPath = [IO.Path]::GetFullPath((Join-Path $RepositoryRoot $relativePath))
  if (-not (Test-DescendantPath $resolvedPath $RepositoryRoot)) {
    throw "product-artifact-path-outside-repository:$relativePath"
  }
  if (-not (Test-Path -LiteralPath $resolvedPath -PathType Leaf)) {
    throw "product-artifact-missing:$relativePath"
  }
  $item = Get-Item -LiteralPath $resolvedPath
  $actualDigest = Get-Sha256Label $resolvedPath
  if ([int64]$item.Length -ne [int64]$Artifact.bytes) {
    throw "product-artifact-byte-count-mismatch:$relativePath"
  }
  if ($actualDigest -cne [string]$Artifact.sha256) {
    throw "product-artifact-digest-mismatch:$relativePath"
  }
  [PSCustomObject]@{
    deckId = [string]$Artifact.deckId
    slideCount = [int]$Artifact.slideCount
    operationId = [string]$Artifact.operationId
    presentationId = [string]$Artifact.presentationId
    exportOperationId = if ($null -ne $Artifact.PSObject.Properties['exportOperationId']) {
      [string]$Artifact.exportOperationId
    } else { $null }
    artifactId = if ($null -ne $Artifact.PSObject.Properties['artifactId']) {
      [string]$Artifact.artifactId
    } else { $null }
    mediaType = [string]$Artifact.mediaType
    relativePath = $relativePath.Replace('\', '/')
    path = $resolvedPath
    bytes = [int64]$item.Length
    sha256 = $actualDigest
    completedAt = if ($null -ne $Artifact.PSObject.Properties['completedAt']) {
      [string]$Artifact.completedAt
    } else { $null }
  }
}

function Assert-SourceManifest {
  param(
    [Parameter(Mandatory = $true)][string]$ManifestPath,
    [Parameter(Mandatory = $true)][string]$RepositoryRoot,
    [Parameter(Mandatory = $true)][string]$ExpectedCommit,
    [Parameter(Mandatory = $true)][string]$RequiredRunnerRelativePath
  )
  $resolved = (Resolve-Path -LiteralPath $ManifestPath).Path
  if (-not (Test-DescendantPath $resolved $RepositoryRoot)) {
    throw 'source-manifest-must-be-inside-current-rc-worktree'
  }
  $digest = Get-Sha256Label $resolved
  $manifest = Get-Content -LiteralPath $resolved -Raw -Encoding UTF8 | ConvertFrom-Json
  if (
    [string]$manifest.schemaVersion -ne 'public-service-source-manifest/v1' -or
    [string]$manifest.sourceTreeCommit -cne $ExpectedCommit -or
    $null -eq $manifest.files
  ) {
    throw 'source-manifest-contract-invalid'
  }
  $seen = [Collections.Generic.HashSet[string]]::new([StringComparer]::Ordinal)
  foreach ($file in @($manifest.files)) {
    if (
      [string]::IsNullOrWhiteSpace([string]$file.path) -or
      [string]$file.sha256 -cnotmatch '^sha256:[0-9a-f]{64}$' -or
      [IO.Path]::IsPathRooted([string]$file.path) -or
      [string]$file.path -match '(^|[\\/])\.\.([\\/]|$)'
    ) {
      throw 'source-manifest-file-entry-invalid'
    }
    $relative = ([string]$file.path).Replace('\', '/')
    if (-not $seen.Add($relative)) {
      throw "source-manifest-duplicate-path:$relative"
    }
    $absolute = [IO.Path]::GetFullPath((Join-Path $RepositoryRoot $relative))
    if (
      -not (Test-DescendantPath $absolute $RepositoryRoot) -or
      -not (Test-Path -LiteralPath $absolute -PathType Leaf)
    ) {
      throw "source-manifest-file-missing:$relative"
    }
    if ((Get-Sha256Label $absolute) -cne [string]$file.sha256) {
      throw "source-manifest-file-digest-mismatch:$relative"
    }
  }
  if (-not $seen.Contains($RequiredRunnerRelativePath.Replace('\', '/'))) {
    throw 'source-manifest-does-not-bind-powerpoint-readback-runner'
  }
  [PSCustomObject]@{
    path = $resolved
    sha256 = $digest
    schemaVersion = [string]$manifest.schemaVersion
    sourceTreeCommit = [string]$manifest.sourceTreeCommit
    fileCount = $seen.Count
  }
}

function Invoke-IsolatedPowerPointQa {
  param(
    [Parameter(Mandatory = $true)][string]$PwshPath,
    [Parameter(Mandatory = $true)][string]$ScriptPath,
    [Parameter(Mandatory = $true)][string]$PresentationPath,
    [Parameter(Mandatory = $true)][string]$OutputPath,
    [Parameter(Mandatory = $true)][string]$LogPath
  )
  $lines = @(
    & $PwshPath -NoProfile -NonInteractive -ExecutionPolicy Bypass -File $ScriptPath `
      -Presentation $PresentationPath -OutputDirectory $OutputPath 2>&1
  )
  $exitCode = $LASTEXITCODE
  [IO.File]::WriteAllLines($LogPath, @($lines | ForEach-Object { [string]$_ }), [Text.UTF8Encoding]::new($false))
  [PSCustomObject]@{ exitCode = $exitCode; lines = $lines }
}

function Invoke-LibreOfficeReadback {
  param(
    [Parameter(Mandatory = $true)][string]$PwshPath,
    [Parameter(Mandatory = $true)][string]$ScriptPath,
    [Parameter(Mandatory = $true)][string]$PresentationPath,
    [Parameter(Mandatory = $true)][string]$OutputJson,
    [Parameter(Mandatory = $true)][string]$WorkingRoot,
    [Parameter(Mandatory = $true)][string]$LibreOfficePath,
    [Parameter(Mandatory = $true)][int]$Timeout,
    [Parameter(Mandatory = $true)][string]$LogPath
  )
  $lines = @(
    & $PwshPath -NoProfile -NonInteractive -ExecutionPolicy Bypass -File $ScriptPath `
      -InputPath $PresentationPath -Engine LibreOffice -OutputJson $OutputJson `
      -LibreOfficeExecutable $LibreOfficePath -TimeoutSeconds $Timeout `
      -WorkingRoot $WorkingRoot -KeepWorkingDirectory 2>&1
  )
  $exitCode = $LASTEXITCODE
  [IO.File]::WriteAllLines($LogPath, @($lines | ForEach-Object { [string]$_ }), [Text.UTF8Encoding]::new($false))
  [PSCustomObject]@{ exitCode = $exitCode; lines = $lines }
}

function Invoke-PdfRendererReadback {
  param(
    [Parameter(Mandatory = $true)][string]$PdfInfoPath,
    [Parameter(Mandatory = $true)][string]$PdfToPpmPath,
    [Parameter(Mandatory = $true)][string]$PdfPath,
    [Parameter(Mandatory = $true)][string]$OutputPath,
    [Parameter(Mandatory = $true)][int]$ExpectedPageCount
  )
  [IO.Directory]::CreateDirectory($OutputPath) | Out-Null
  $digestBefore = Get-Sha256Label $PdfPath
  $pdfInfoLines = @(& $PdfInfoPath $PdfPath 2>&1)
  $pdfInfoExitCode = $LASTEXITCODE
  $pageLine = @($pdfInfoLines | Where-Object { [string]$_ -match '^Pages:\s+([0-9]+)\s*$' })
  $pageCount = if ($pageLine.Count -eq 1 -and [string]$pageLine[0] -match '^Pages:\s+([0-9]+)\s*$') {
    [int]$Matches[1]
  } else { 0 }
  $prefix = Join-Path $OutputPath 'page'
  $renderLines = @(& $PdfToPpmPath -png -r 96 $PdfPath $prefix 2>&1)
  $renderExitCode = $LASTEXITCODE
  $pngs = @(Get-ChildItem -LiteralPath $OutputPath -Filter 'page-*.png' -File | Sort-Object Name)
  $digestAfter = Get-Sha256Label $PdfPath
  [PSCustomObject]@{
    pdfInfoExitCode = $pdfInfoExitCode
    renderExitCode = $renderExitCode
    pageCount = $pageCount
    expectedPageCount = $ExpectedPageCount
    renderedPageCount = $pngs.Count
    inputSha256Before = $digestBefore
    inputSha256After = $digestAfter
    inputDigestPreserved = $digestBefore -ceq $digestAfter
    passed =
      $pdfInfoExitCode -eq 0 -and
      $renderExitCode -eq 0 -and
      $pageCount -eq $ExpectedPageCount -and
      $pngs.Count -eq $ExpectedPageCount -and
      $digestBefore -ceq $digestAfter
    pdfInfo = @($pdfInfoLines | ForEach-Object { [string]$_ })
    renderOutput = @($renderLines | ForEach-Object { [string]$_ })
    renderedPages = @($pngs | ForEach-Object {
      [PSCustomObject]@{
        path = $_.FullName
        bytes = [int64]$_.Length
        sha256 = Get-Sha256Label $_.FullName
      }
    })
  }
}

$repositoryRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$sourceTreeCommit = (& git -C $repositoryRoot rev-parse HEAD).Trim()
if ($LASTEXITCODE -ne 0 -or $sourceTreeCommit -notmatch '^[0-9a-f]{40}$') {
  throw 'current-source-tree-commit-unavailable'
}
$sourceManifestObservation = Assert-SourceManifest `
  $SourceManifest $repositoryRoot $sourceTreeCommit `
  'scripts/collect-public-service-powerpoint-readback.ps1'
$releaseCandidate = $sourceManifestObservation.sha256
$resolvedManifest = (Resolve-Path -LiteralPath $ProductArtifactsManifest).Path
if (-not (Test-DescendantPath $resolvedManifest $repositoryRoot)) {
  throw 'product-artifacts-manifest-must-be-inside-current-rc-worktree'
}
$manifest = Get-Content -LiteralPath $resolvedManifest -Raw -Encoding UTF8 | ConvertFrom-Json
$manifestSourcePath = if ($null -ne $manifest.PSObject.Properties['sourceManifestPath']) {
  [string]$manifest.sourceManifestPath
} else { '' }
$manifestSourceAbsolute = if (
  -not [string]::IsNullOrWhiteSpace($manifestSourcePath) -and
  -not [IO.Path]::IsPathRooted($manifestSourcePath) -and
  $manifestSourcePath -notmatch '(^|[\\/])\.\.([\\/]|$)'
) {
  [IO.Path]::GetFullPath((Join-Path $repositoryRoot $manifestSourcePath))
} else { '' }
if (
  [string]$manifest.schemaVersion -ne 'public-service-product-artifacts/v1' -or
  [string]$manifest.releaseCandidate -cne $releaseCandidate -or
  [string]$manifest.sourceTreeCommit -cne $sourceTreeCommit -or
  [string]$manifest.sourceManifestSha256 -cne $releaseCandidate -or
  [string]::IsNullOrWhiteSpace($manifestSourceAbsolute) -or
  -not [string]::Equals(
    $manifestSourceAbsolute,
    [IO.Path]::GetFullPath($sourceManifestObservation.path),
    [StringComparison]::OrdinalIgnoreCase
  ) -or
  [string]$manifest.evidenceClass -ne 'product-path'
) {
  throw 'product-artifacts-manifest-not-same-rc-product-path'
}

$resolvedArtifacts = @($manifest.artifacts | ForEach-Object {
  Resolve-ManifestArtifact $_ $repositoryRoot
})
$availableDeckIds = @($resolvedArtifacts.deckId | Sort-Object -Unique)
$selectedDeckIds = if ($DeckId.Count -gt 0) { @($DeckId | Sort-Object -Unique) } else {
  @($availableDeckIds | Where-Object {
    $candidate = $_
    @($resolvedArtifacts | Where-Object {
      $_.deckId -eq $candidate -and $_.mediaType -eq 'application/vnd.openxmlformats-officedocument.presentationml.presentation'
    }).Count -eq 1 -and
    @($resolvedArtifacts | Where-Object {
      $_.deckId -eq $candidate -and $_.mediaType -eq 'application/pdf'
    }).Count -eq 1
  })
}
if ($selectedDeckIds.Count -lt 1) {
  throw 'no-complete-pptx-pdf-product-artifact-pair'
}

$pairs = @($selectedDeckIds | ForEach-Object {
  $currentDeckId = $_
  $pptx = @($resolvedArtifacts | Where-Object {
    $_.deckId -eq $currentDeckId -and $_.mediaType -eq 'application/vnd.openxmlformats-officedocument.presentationml.presentation'
  })
  $pdf = @($resolvedArtifacts | Where-Object {
    $_.deckId -eq $currentDeckId -and $_.mediaType -eq 'application/pdf'
  })
  if ($pptx.Count -ne 1 -or $pdf.Count -ne 1) {
    throw "selected-deck-requires-exactly-one-pptx-and-pdf:$currentDeckId"
  }
  if (
    $pptx[0].slideCount -ne $pdf[0].slideCount -or
    $pptx[0].presentationId -ne $pdf[0].presentationId
  ) {
    throw "selected-deck-artifact-lineage-mismatch:$currentDeckId"
  }
  [PSCustomObject]@{ deckId = $currentDeckId; pptx = $pptx[0]; pdf = $pdf[0] }
})

$resolvedOutput = [IO.Path]::GetFullPath($OutputDirectory)
if (-not (Test-DescendantPath $resolvedOutput $repositoryRoot)) {
  throw 'output-directory-must-be-inside-current-rc-worktree'
}
if (Test-Path -LiteralPath $resolvedOutput) {
  throw "immutable-output-directory-already-exists:$resolvedOutput"
}
[IO.Directory]::CreateDirectory($resolvedOutput) | Out-Null

$resolvedPowerPointQa = (Resolve-Path -LiteralPath $PowerPointQaScript).Path
$resolvedRendererReadback = (Resolve-Path -LiteralPath $RendererReadbackScript).Path
$pwsh = (Get-Command pwsh -ErrorAction Stop).Source
$pdfInfo = (Get-Command pdfinfo -ErrorAction Stop).Source
$pdfToPpm = (Get-Command pdftoppm -ErrorAction Stop).Source
if (-not (Test-Path -LiteralPath $LibreOfficeExecutable -PathType Leaf)) {
  throw "libreoffice-executable-not-found:$LibreOfficeExecutable"
}

$productArtifactsManifestDigest = Get-Sha256Label $resolvedManifest
$runnerRelativePath = 'scripts/collect-public-service-powerpoint-readback.ps1'
$runnerDigest = Get-Sha256Label (Join-Path $repositoryRoot $runnerRelativePath)
$observations = @()
foreach ($pair in $pairs) {
  $safeDeckId = ($pair.deckId -replace '[^A-Za-z0-9._-]', '_')
  $pairOutput = Join-Path $resolvedOutput $safeDeckId
  [IO.Directory]::CreateDirectory($pairOutput) | Out-Null

  $pptxDigestBefore = Get-Sha256Label $pair.pptx.path
  $pdfDigestBefore = Get-Sha256Label $pair.pdf.path
  $package = Get-PptxPackageSummary $pair.pptx.path

  $powerPointOutput = Join-Path $pairOutput 'powerpoint'
  [IO.Directory]::CreateDirectory($powerPointOutput) | Out-Null
  $powerPointInvocation = Invoke-IsolatedPowerPointQa `
    $pwsh $resolvedPowerPointQa $pair.pptx.path $powerPointOutput `
    (Join-Path $pairOutput 'powerpoint-invocation.log')
  $powerPointReportPath = Join-Path $powerPointOutput 'powerpoint-desktop-validation.json'
  $powerPointReport = if (Test-Path -LiteralPath $powerPointReportPath -PathType Leaf) {
    Get-Content -LiteralPath $powerPointReportPath -Raw -Encoding UTF8 | ConvertFrom-Json
  } else { $null }
  $powerPointPresentation = if ($null -ne $powerPointReport) {
    @($powerPointReport.Presentations | Where-Object {
      [IO.Path]::GetFullPath([string]$_.Path) -eq [IO.Path]::GetFullPath($pair.pptx.path)
    })
  } else { @() }
  $powerPointPassed =
    $powerPointInvocation.exitCode -eq 0 -and
    $powerPointPresentation.Count -eq 1 -and
    [bool]$powerPointPresentation[0].Reopened -and
    [bool]$powerPointPresentation[0].ReadbackPassed -and
    [bool]$powerPointPresentation[0].FirstOpenReopenedRenderHashesMatch -and
    -not [bool]$powerPointPresentation[0].RepairWarningObserved -and
    [int]$powerPointPresentation[0].SlideCount -eq [int]$pair.pptx.slideCount -and
    [int]$powerPointReport.UnexpectedPowerPointProcessCount -eq 0 -and
    @($powerPointReport.PowerPointProcessCensusAfter).Count -eq 0

  $libreOfficeRoot = Join-Path $pairOutput 'libreoffice-work'
  [IO.Directory]::CreateDirectory($libreOfficeRoot) | Out-Null
  $libreOfficeReportPath = Join-Path $pairOutput 'libreoffice-readback.json'
  $libreOfficeInvocation = Invoke-LibreOfficeReadback `
    $pwsh $resolvedRendererReadback $pair.pptx.path $libreOfficeReportPath `
    $libreOfficeRoot $LibreOfficeExecutable $TimeoutSeconds `
    (Join-Path $pairOutput 'libreoffice-invocation.log')
  $libreOfficeReport = if (Test-Path -LiteralPath $libreOfficeReportPath -PathType Leaf) {
    Get-Content -LiteralPath $libreOfficeReportPath -Raw -Encoding UTF8 | ConvertFrom-Json
  } else { $null }
  $libreOfficeEngine = if ($null -ne $libreOfficeReport) {
    @($libreOfficeReport.engines | Where-Object { $_.engine -eq 'LibreOffice' })
  } else { @() }
  $libreOfficePassed =
    $libreOfficeInvocation.exitCode -eq 0 -and
    $null -ne $libreOfficeReport -and
    $libreOfficeReport.overallStatus -eq 'passed' -and
    [bool]$libreOfficeReport.source.unchanged -and
    $libreOfficeEngine.Count -eq 1 -and
    $libreOfficeEngine[0].status -eq 'passed' -and
    [bool]$libreOfficeEngine[0].output.packageReadBack.readable -and
    [bool]$libreOfficeEngine[0].output.slideCountMatchesInput

  $pdfObservation = Invoke-PdfRendererReadback `
    $pdfInfo $pdfToPpm $pair.pdf.path (Join-Path $pairOutput 'pdf-poppler') `
    $pair.pdf.slideCount

  $pptxDigestAfter = Get-Sha256Label $pair.pptx.path
  $pdfDigestAfter = Get-Sha256Label $pair.pdf.path
  $digestPassed =
    $pptxDigestBefore -ceq $pair.pptx.sha256 -and
    $pptxDigestAfter -ceq $pair.pptx.sha256 -and
    $pdfDigestBefore -ceq $pair.pdf.sha256 -and
    $pdfDigestAfter -ceq $pair.pdf.sha256
  $packagePassed =
    [bool]$package.readable -and
    [int]$package.slidePartCount -eq [int]$pair.pptx.slideCount
  $matrixPassed =
    $packagePassed -and $digestPassed -and $powerPointPassed -and
    $libreOfficePassed -and [bool]$pdfObservation.passed

  $observations += [PSCustomObject]@{
    deckId = $pair.deckId
    operationId = $pair.pptx.operationId
    presentationId = $pair.pptx.presentationId
    slideCount = $pair.pptx.slideCount
    pptx = $pair.pptx
    pdf = $pair.pdf
    sourceDigestPreserved = $digestPassed
    packageReadback = $package
    packageReadbackPassed = $packagePassed
    powerPoint = [PSCustomObject]@{
      invocationExitCode = $powerPointInvocation.exitCode
      reportPath = if ($null -ne $powerPointReport) { $powerPointReportPath } else { $null }
      executableVersion = if ($null -ne $powerPointReport) { [string]$powerPointReport.PowerPointVersion } else { $null }
      ownedProcessBindingExact = if ($null -ne $powerPointReport) {
        [int]$powerPointReport.LaunchedProcessId -eq [int]$powerPointReport.AttachedApplicationProcessId
      } else { $false }
      unexpectedProcessCount = if ($null -ne $powerPointReport) {
        [int]$powerPointReport.UnexpectedPowerPointProcessCount
      } else { $null }
      finalProcessCount = if ($null -ne $powerPointReport) {
        @($powerPointReport.PowerPointProcessCensusAfter).Count
      } else { $null }
      firstOpenSaveCopyReopenReadbackPassed = $powerPointPassed
      presentation = if ($powerPointPresentation.Count -eq 1) { $powerPointPresentation[0] } else { $null }
    }
    libreOffice = [PSCustomObject]@{
      invocationExitCode = $libreOfficeInvocation.exitCode
      reportPath = if ($null -ne $libreOfficeReport) { $libreOfficeReportPath } else { $null }
      passed = $libreOfficePassed
      report = $libreOfficeReport
    }
    pdfPoppler = $pdfObservation
    supportedRendererMatrixPassed = $matrixPassed
  }
}

$testedPptxCount = $observations.Count
$reopenPassedCount = @($observations | Where-Object {
  $_.powerPoint.firstOpenSaveCopyReopenReadbackPassed
}).Count
$packageReadbackFailureCount = @($observations | Where-Object {
  -not $_.packageReadbackPassed -or -not $_.libreOffice.passed -or -not $_.pdfPoppler.passed
}).Count
$digestReadbackMismatchCount = @($observations | Where-Object {
  -not $_.sourceDigestPreserved
}).Count
$supportedRendererMatrixPassed =
  $testedPptxCount -gt 0 -and
  @($observations | Where-Object { -not $_.supportedRendererMatrixPassed }).Count -eq 0

$evidence = [ordered]@{
  schemaVersion = 'public-service-powerpoint-readback-evidence/v1'
  releaseCandidate = $releaseCandidate
  evidenceClass = 'product-path'
  collectedAt = (Get-Date).ToUniversalTime().ToString('o')
  sourceTree = [ordered]@{
    repositoryRoot = $repositoryRoot
    sourceTreeCommit = $sourceTreeCommit
    sourceManifest = $sourceManifestObservation
  }
  productArtifactsManifest = [ordered]@{
    path = $resolvedManifest
    sha256 = $productArtifactsManifestDigest
    schemaVersion = [string]$manifest.schemaVersion
    releaseCandidate = [string]$manifest.releaseCandidate
    evidenceClass = [string]$manifest.evidenceClass
  }
  provenance = [ordered]@{
    runnerPath = $runnerRelativePath
    runnerSha256 = $runnerDigest
    sourceManifestDigest = $releaseCandidate
  }
  selectedDeckIds = $selectedDeckIds
  ignoredArtifactCount = @($resolvedArtifacts | Where-Object {
    $_.deckId -notin $selectedDeckIds
  }).Count
  rendererPolicy = [ordered]@{
    requiredPptxRenderers = @('Microsoft PowerPoint 16 owned COM first-open/save-copy/reopen', 'LibreOffice Impress isolated headless readback')
    requiredPdfRenderers = @('Poppler pdfinfo', 'Poppler pdftoppm PNG')
    visualParityClaimed = $false
    matrixMeaning = 'package/readback/render compatibility only; no cross-renderer visual parity claim'
  }
  testedPptxCount = $testedPptxCount
  reopenPassedCount = $reopenPassedCount
  packageReadbackFailureCount = $packageReadbackFailureCount
  digestReadbackMismatchCount = $digestReadbackMismatchCount
  supportedRendererMatrixPassed = $supportedRendererMatrixPassed
  observations = $observations
  promotionReady = $false
  deploymentAuthorized = $false
}
$resultPath = Join-Path $resolvedOutput 'powerpoint-readback.json'
Write-Utf8Json $resultPath $evidence
$evidence | ConvertTo-Json -Depth 20
if (-not $supportedRendererMatrixPassed) { exit 2 }
