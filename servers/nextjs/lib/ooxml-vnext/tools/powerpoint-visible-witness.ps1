[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$InputPath,
    [string]$WitnessRoot = [System.IO.Path]::GetTempPath(),
    [string]$OutputJson,
    [switch]$LaunchVisibleWitness,
    [switch]$LaunchOpenSaveReopenWitness,
    [switch]$RunLibreOffice,
    [string]$PowerPointExecutable = "C:\Program Files\Microsoft Office\root\Office16\POWERPNT.EXE"
)

Set-StrictMode -Version 3.0
$ErrorActionPreference = "Stop"
Add-Type -AssemblyName System.IO.Compression.FileSystem

function Get-Sha256([string]$Path) {
    return (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToLowerInvariant()
}

function ConvertTo-ObservedBoolean([string]$Answer) {
    $normalized = $Answer.Trim().ToLowerInvariant()
    if ($normalized -eq "yes") { return $true }
    if ($normalized -eq "no") { return $false }
    return $null
}

function Write-Json([string]$Path, [object]$Value) {
    $full = [System.IO.Path]::GetFullPath($Path)
    $parent = [System.IO.Path]::GetDirectoryName($full)
    if (-not (Test-Path -LiteralPath $parent -PathType Container)) {
        throw "Output directory does not exist: $parent"
    }
    [System.IO.File]::WriteAllText(
        $full,
        ($Value | ConvertTo-Json -Depth 20),
        [System.Text.UTF8Encoding]::new($false)
    )
}

function New-WitnessDirectory([string]$Root) {
    $rootFull = [System.IO.Path]::GetFullPath($Root)
    if (-not (Test-Path -LiteralPath $rootFull -PathType Container)) {
        throw "Witness root does not exist: $rootFull"
    }
    $name = "ooxml-vnext-powerpoint-witness-$([Guid]::NewGuid().ToString('N'))"
    $directory = [System.IO.Path]::GetFullPath((Join-Path $rootFull $name))
    $boundary = $rootFull.TrimEnd(
        [IO.Path]::DirectorySeparatorChar,
        [IO.Path]::AltDirectorySeparatorChar
    ) + [IO.Path]::DirectorySeparatorChar
    if (-not $directory.StartsWith($boundary, [StringComparison]::OrdinalIgnoreCase)) {
        throw "Refusing witness path outside the configured root: $directory"
    }
    New-Item -ItemType Directory -Path $directory | Out-Null
    return $directory
}

$source = [System.IO.Path]::GetFullPath($InputPath)
if (-not (Test-Path -LiteralPath $source -PathType Leaf)) {
    throw "Input PPTX does not exist: $source"
}
if ([System.IO.Path]::GetExtension($source) -ine ".pptx") {
    throw "Only .pptx input is accepted."
}
$sourceDigestBefore = Get-Sha256 $source
$witnessDirectory = New-WitnessDirectory $WitnessRoot
$disposableCopy = Join-Path $witnessDirectory "disposable-witness.pptx"
Copy-Item -LiteralPath $source -Destination $disposableCopy
$copyDigestBefore = Get-Sha256 $disposableCopy
$archive = [IO.Compression.ZipFile]::OpenRead($disposableCopy)
try {
    $expectedSlideCount = @(
        $archive.Entries | Where-Object { $_.FullName -match '^ppt/slides/slide[0-9]+\.xml$' }
    ).Count
}
finally { $archive.Dispose() }
$existing = @(Get-Process -Name POWERPNT -ErrorAction SilentlyContinue)
$powerPointAvailable = Test-Path -LiteralPath $PowerPointExecutable -PathType Leaf
$powerPoint = [ordered]@{
    available = $powerPointAvailable
    executable = [System.IO.Path]::GetFullPath($PowerPointExecutable)
    existingProcessIds = @($existing | ForEach-Object { $_.Id })
    launched = $false
    spawnedProcessId = $null
    status = "awaiting-human-witness"
    repairDialogObserved = $null
    repairDialogZeroClaimSupported = $false
    saved = $false
    witnessMode = if ($LaunchOpenSaveReopenWitness) { "interactive-open-save-reopen" } else { "visible-open-no-save" }
    expectedSlideCount = $expectedSlideCount
    openSlideCountObserved = $null
    reopenSlideCountObserved = $null
    openRepairDialogObserved = $null
    reopenRepairDialogObserved = $null
    targetedEditCreated = $null
    companyMasterConnected = $null
    layoutVariantsConnected = $null
    logoConnected = $null
    placeholderMappingValid = $null
    pageNumberFieldValid = $null
    fontManifestAndComplexTextValid = $null
    reopenTargetedEditPersisted = $null
    reopenSpawnedProcessId = $null
    reopened = $false
}

if ($LaunchVisibleWitness -and $LaunchOpenSaveReopenWitness) {
    throw "Choose only one PowerPoint witness launch mode."
}

if ($LaunchOpenSaveReopenWitness) {
    if (-not $powerPointAvailable) {
        $powerPoint.status = "blocked-powerpoint-unavailable"
    }
    elseif ($existing.Count -gt 0) {
        $powerPoint.status = "blocked-existing-powerpoint-process"
    }
    else {
        $arguments = @("/x", ('"' + $disposableCopy + '"'))
        $opened = Start-Process -FilePath $PowerPointExecutable -ArgumentList $arguments -PassThru
        $powerPoint.launched = $true
        $powerPoint.spawnedProcessId = $opened.Id
        Write-Host "Inspect only the disposable copy. Keep macros disabled. Make one visible body-text edit, inspect Master/Layout linkage and required furniture, save in place, then close that new PowerPoint window."
        $openRepair = Read-Host "Repair dialog observed on first open? Enter yes or no"
        $openSlides = Read-Host "Slide count visible on first open"
        $targetedEdit = Read-Host "Was a visible body-text edit made successfully? Enter yes or no"
        $companyMaster = Read-Host "Is the company master connected in PowerPoint Master View? Enter yes or no"
        $layoutVariants = Read-Host "Are cover, section, standard, and long-title layouts connected? Enter yes or no"
        $logo = Read-Host "Is the logo connected through the expected master/layout furniture? Enter yes or no"
        $placeholders = Read-Host "Are title/body placeholder mappings correct and editable? Enter yes or no"
        $pageNumber = Read-Host "Is the page-number field present and correctly connected? Enter yes or no"
        $fonts = Read-Host "Do the font manifest and Korean/CJK, mixed text, long titles, emoji, combining marks, and fallback text render acceptably? Enter yes or no"
        $saved = Read-Host "Was the disposable presentation saved in place? Enter yes or no"
        $powerPoint.saved = $saved.Trim().ToLowerInvariant() -eq "yes"
        $powerPoint.openRepairDialogObserved = ConvertTo-ObservedBoolean $openRepair
        $powerPoint.targetedEditCreated = ConvertTo-ObservedBoolean $targetedEdit
        $powerPoint.companyMasterConnected = ConvertTo-ObservedBoolean $companyMaster
        $powerPoint.layoutVariantsConnected = ConvertTo-ObservedBoolean $layoutVariants
        $powerPoint.logoConnected = ConvertTo-ObservedBoolean $logo
        $powerPoint.placeholderMappingValid = ConvertTo-ObservedBoolean $placeholders
        $powerPoint.pageNumberFieldValid = ConvertTo-ObservedBoolean $pageNumber
        $powerPoint.fontManifestAndComplexTextValid = ConvertTo-ObservedBoolean $fonts
        $parsedOpenSlides = 0
        if ([int]::TryParse($openSlides, [ref]$parsedOpenSlides)) {
            $powerPoint.openSlideCountObserved = $parsedOpenSlides
        }
        if (-not $opened.WaitForExit(10000)) {
            $powerPoint.status = "failed-first-spawned-process-still-open"
        }
        elseif (-not $powerPoint.saved) {
            $powerPoint.status = "failed-human-save-not-confirmed"
        }
        elseif ($null -eq $powerPoint.openRepairDialogObserved) {
            $powerPoint.status = "inconclusive-open-repair-observation"
        }
        elseif ($powerPoint.openRepairDialogObserved) {
            $powerPoint.status = "failed-repair-dialog-observed-on-open"
        }
        elseif ($powerPoint.openSlideCountObserved -ne $expectedSlideCount) {
            $powerPoint.status = "failed-slide-count-mismatch-on-open"
        }
        elseif ($null -in @(
            $powerPoint.targetedEditCreated,
            $powerPoint.companyMasterConnected,
            $powerPoint.layoutVariantsConnected,
            $powerPoint.logoConnected,
            $powerPoint.placeholderMappingValid,
            $powerPoint.pageNumberFieldValid,
            $powerPoint.fontManifestAndComplexTextValid
        )) {
            $powerPoint.status = "inconclusive-required-product-observation"
        }
        elseif ($false -in @(
            $powerPoint.targetedEditCreated,
            $powerPoint.companyMasterConnected,
            $powerPoint.layoutVariantsConnected,
            $powerPoint.logoConnected,
            $powerPoint.placeholderMappingValid,
            $powerPoint.pageNumberFieldValid,
            $powerPoint.fontManifestAndComplexTextValid
        )) {
            $powerPoint.status = "failed-required-product-observation"
        }
        elseif (@(Get-Process -Name POWERPNT -ErrorAction SilentlyContinue).Count -gt 0) {
            $powerPoint.status = "blocked-existing-powerpoint-process-before-reopen"
        }
        else {
            $reopened = Start-Process -FilePath $PowerPointExecutable -ArgumentList $arguments -PassThru
            $powerPoint.reopened = $true
            $powerPoint.reopenSpawnedProcessId = $reopened.Id
            Write-Host "Inspect the reopened disposable copy, do not save again, and close that new PowerPoint window."
            $reopenRepair = Read-Host "Repair dialog observed on reopen? Enter yes or no"
            $reopenSlides = Read-Host "Slide count visible on reopen"
            $reopenEdit = Read-Host "Is the saved body-text edit present and still editable after reopen? Enter yes or no"
            $powerPoint.reopenRepairDialogObserved = ConvertTo-ObservedBoolean $reopenRepair
            $powerPoint.reopenTargetedEditPersisted = ConvertTo-ObservedBoolean $reopenEdit
            $parsedReopenSlides = 0
            if ([int]::TryParse($reopenSlides, [ref]$parsedReopenSlides)) {
                $powerPoint.reopenSlideCountObserved = $parsedReopenSlides
            }
            if (-not $reopened.WaitForExit(10000)) {
                $powerPoint.status = "failed-reopen-spawned-process-still-open"
            }
            elseif ($null -eq $powerPoint.reopenRepairDialogObserved) {
                $powerPoint.status = "inconclusive-reopen-repair-observation"
            }
            elseif ($powerPoint.reopenRepairDialogObserved) {
                $powerPoint.status = "failed-repair-dialog-observed-on-reopen"
            }
            elseif ($powerPoint.reopenSlideCountObserved -ne $expectedSlideCount) {
                $powerPoint.status = "failed-slide-count-mismatch-on-reopen"
            }
            elseif ($null -eq $powerPoint.reopenTargetedEditPersisted) {
                $powerPoint.status = "inconclusive-reopen-edit-observation"
            }
            elseif (-not $powerPoint.reopenTargetedEditPersisted) {
                $powerPoint.status = "failed-targeted-edit-not-persisted"
            }
            else {
                $powerPoint.status = "passed-interactive-open-save-reopen"
                $powerPoint.repairDialogObserved = $false
                $powerPoint.repairDialogZeroClaimSupported = $true
            }
        }
    }
}
elseif ($LaunchVisibleWitness) {
    if (-not $powerPointAvailable) {
        $powerPoint.status = "blocked-powerpoint-unavailable"
    }
    elseif ($existing.Count -gt 0) {
        $powerPoint.status = "blocked-existing-powerpoint-process"
    }
    else {
        $arguments = @("/x", ('"' + $disposableCopy + '"'))
        $spawned = Start-Process `
            -FilePath $PowerPointExecutable `
            -ArgumentList $arguments `
            -PassThru
        $powerPoint.launched = $true
        $powerPoint.spawnedProcessId = $spawned.Id
        Write-Host "Inspect only the disposable copy. Keep macros disabled, do not save, and close that new PowerPoint window."
        $observation = Read-Host "Repair dialog observed? Enter yes or no"
        $saved = Read-Host "Was the disposable presentation saved? Enter yes or no"
        $spawned.Refresh()
        $powerPoint.saved = $saved.Trim().ToLowerInvariant() -eq "yes"
        if (-not $spawned.HasExited) {
            $powerPoint.status = "failed-spawned-process-still-open"
        }
        elseif ($powerPoint.saved) {
            $powerPoint.status = "failed-witness-was-saved"
        }
        elseif ($observation.Trim().ToLowerInvariant() -eq "no") {
            $powerPoint.status = "passed-visible-no-repair-dialog"
            $powerPoint.repairDialogObserved = $false
            $powerPoint.repairDialogZeroClaimSupported = $true
        }
        elseif ($observation.Trim().ToLowerInvariant() -eq "yes") {
            $powerPoint.status = "failed-repair-dialog-observed"
            $powerPoint.repairDialogObserved = $true
        }
        else {
            $powerPoint.status = "inconclusive-human-observation"
        }
    }
}

$libreOffice = [ordered]@{
    status = "not-requested"
    receipt = $null
    limitation = "LibreOffice is supplemental read-back only; it cannot establish PowerPoint repair-dialog behavior or byte preservation."
}
if ($RunLibreOffice) {
    $matrixScript = Join-Path $PSScriptRoot "office-compatibility-matrix.ps1"
    $matrixText = & powershell.exe `
        -NoLogo `
        -NoProfile `
        -NonInteractive `
        -File $matrixScript `
        -InputPath $disposableCopy `
        -RunLibreOffice
    if ($LASTEXITCODE -ne 0) {
        $libreOffice.status = "failed"
    }
    else {
        $matrix = ($matrixText -join [Environment]::NewLine) | ConvertFrom-Json
        $libreOffice.status = $matrix.matrix.libreOffice.status
        $libreOffice.receipt = $matrix.matrix.libreOffice
    }
}

$sourceDigestAfter = Get-Sha256 $source
$copyDigestAfter = Get-Sha256 $disposableCopy
$report = [ordered]@{
    schemaVersion = "ooxml-vnext/powerpoint-visible-witness/v1"
    createdAtUtc = [DateTime]::UtcNow.ToString("o")
    source = [ordered]@{
        path = $source
        sha256Before = $sourceDigestBefore
        sha256After = $sourceDigestAfter
        unchanged = $sourceDigestBefore -eq $sourceDigestAfter
        openedByPowerPoint = $false
    }
    disposable = [ordered]@{
        directory = $witnessDirectory
        path = $disposableCopy
        sha256Before = $copyDigestBefore
        sha256After = $copyDigestAfter
        unchanged = $copyDigestBefore -eq $copyDigestAfter
        mutationExpected = [bool]$LaunchOpenSaveReopenWitness
        retainedForAudit = $true
    }
    safety = [ordered]@{
        existingProcessAttach = $false
        existingProcessTermination = $false
        spawnedProcessTermination = $false
        powerPointSaveCalls = 0
        humanPowerPointSaveConfirmed = $powerPoint.saved
        newProcessSwitch = "/x"
    }
    powerPoint = $powerPoint
    libreOffice = $libreOffice
    overallStatus = if (
        -not ($sourceDigestBefore -eq $sourceDigestAfter) -or
        (-not $LaunchOpenSaveReopenWitness -and -not ($copyDigestBefore -eq $copyDigestAfter)) -or
        $powerPoint.status -like "failed-*"
    ) { "failed" }
    elseif ($powerPoint.status -in @("passed-visible-no-repair-dialog", "passed-interactive-open-save-reopen")) { "passed" }
    else { "inconclusive" }
    limitations = @(
        "Preparation or availability alone never establishes repair dialog = 0.",
        "Only the explicit visible human observation can set repairDialogZeroClaimSupported=true.",
        "Open/save/reopen mode requires a human to verify editability, company master/layout/furniture, placeholders, page-number field, font/complex-text rendering, save, and close both newly spawned /x PowerPoint processes.",
        "The runner never attaches to or terminates an existing PowerPoint process and never opens the source file."
    )
}
$templatePath = Join-Path $witnessDirectory "witness-receipt.json"
Write-Json $templatePath $report
if ($OutputJson) { Write-Json $OutputJson $report }
$report | ConvertTo-Json -Depth 20

if ($report.overallStatus -eq "failed") { exit 1 }
exit 0
