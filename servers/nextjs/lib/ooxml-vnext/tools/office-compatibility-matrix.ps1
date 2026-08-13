[CmdletBinding()]
param(
    [string]$InputPath,
    [string]$OutputJson,
    [switch]$RunOpenXmlSdk,
    [switch]$RunLibreOffice,
    [switch]$CreateManualWitness,
    [string]$ManualWitnessRoot = [System.IO.Path]::GetTempPath(),
    [string]$PowerPointExecutable = "C:\Program Files\Microsoft Office\root\Office16\POWERPNT.EXE",
    [string]$LibreOfficeExecutable = "C:\Program Files\LibreOffice\program\soffice.exe",
    [ValidateRange(10, 1800)][int]$TimeoutSeconds = 120,
    [switch]$ForceOutput
)

Set-StrictMode -Version 3.0
$ErrorActionPreference = "Stop"

function Get-Sha256([string]$Path) {
    return (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToLowerInvariant()
}

function Get-ExecutableEvidence([string]$Path) {
    $fullPath = [System.IO.Path]::GetFullPath($Path)
    if (-not (Test-Path -LiteralPath $fullPath -PathType Leaf)) {
        return [ordered]@{ available = $false; path = $fullPath; fileVersion = $null; productVersion = $null }
    }
    $item = Get-Item -LiteralPath $fullPath
    return [ordered]@{
        available = $true
        path = $item.FullName
        fileVersion = $item.VersionInfo.FileVersion
        productVersion = $item.VersionInfo.ProductVersion
    }
}

function Get-PackageSummary([string]$Path) {
    $summary = [ordered]@{ readable = $false; partCount = $null; slideCount = $null; error = $null }
    try {
        Add-Type -AssemblyName System.IO.Compression.FileSystem
        $archive = [System.IO.Compression.ZipFile]::OpenRead($Path)
        try {
            $names = @($archive.Entries | ForEach-Object { $_.FullName.Replace("\", "/") })
            $summary.partCount = $names.Count
            $summary.slideCount = @($names | Where-Object { $_ -match '^ppt/slides/slide[0-9]+\.xml$' }).Count
            $summary.readable = ($names -contains "[Content_Types].xml") -and ($names -contains "ppt/presentation.xml")
        }
        finally { $archive.Dispose() }
    }
    catch { $summary.error = $_.Exception.Message }
    return $summary
}

function ConvertTo-FileUri([string]$Path) {
    return ([Uri][System.IO.Path]::GetFullPath($Path)).AbsoluteUri
}

function New-OwnedDirectory([string]$Root, [string]$Prefix) {
    $rootFull = [System.IO.Path]::GetFullPath($Root)
    if (-not (Test-Path -LiteralPath $rootFull -PathType Container)) {
        throw "Working root does not exist: $rootFull"
    }
    $name = "$Prefix-$([Guid]::NewGuid().ToString('N'))"
    $candidate = [System.IO.Path]::GetFullPath((Join-Path $rootFull $name))
    $rootBoundary = $rootFull.TrimEnd([IO.Path]::DirectorySeparatorChar, [IO.Path]::AltDirectorySeparatorChar) + [IO.Path]::DirectorySeparatorChar
    if (-not $candidate.StartsWith($rootBoundary, [StringComparison]::OrdinalIgnoreCase)) {
        throw "Refusing working path outside configured root: $candidate"
    }
    New-Item -ItemType Directory -Path $candidate | Out-Null
    return $candidate
}

function Remove-OwnedDirectory([string]$Path, [string]$Root, [string]$Prefix) {
    $pathFull = [System.IO.Path]::GetFullPath($Path)
    $rootFull = [System.IO.Path]::GetFullPath($Root)
    $rootBoundary = $rootFull.TrimEnd([IO.Path]::DirectorySeparatorChar, [IO.Path]::AltDirectorySeparatorChar) + [IO.Path]::DirectorySeparatorChar
    $item = Get-Item -LiteralPath $pathFull -Force
    if (-not $pathFull.StartsWith($rootBoundary, [StringComparison]::OrdinalIgnoreCase) -or
        -not $item.Name.StartsWith("$Prefix-", [StringComparison]::Ordinal) -or
        ($item.Attributes -band [IO.FileAttributes]::ReparsePoint)) {
        throw "Refusing unsafe cleanup target: $pathFull"
    }
    [System.IO.Directory]::Delete($pathFull, $true)
}

function Invoke-OpenXmlSdk([string]$Path, [string]$ValidatorDll) {
    if (-not (Test-Path -LiteralPath $ValidatorDll -PathType Leaf)) {
        return [ordered]@{
            status = "unavailable-unbuilt"
            message = "Build the exact-locked validator before requesting this lane."
            command = "dotnet restore --locked-mode; dotnet build --no-restore --configuration Release"
        }
    }
    $stdout = & dotnet $ValidatorDll $Path
    $exitCode = $LASTEXITCODE
    try { $receipt = (($stdout -join [Environment]::NewLine) | ConvertFrom-Json) }
    catch {
        return [ordered]@{ status = "tool-error"; exitCode = $exitCode; message = "Validator did not return JSON." }
    }
    if ($null -eq $receipt) {
        return [ordered]@{ status = "tool-error"; exitCode = $exitCode; message = "Validator returned no JSON." }
    }
    return [ordered]@{ status = if ($exitCode -eq 0) { "passed" } else { "failed" }; exitCode = $exitCode; receipt = $receipt }
}

function Invoke-LibreOfficeReadBack([string]$Path, [string]$Executable, [int]$Timeout, [string]$WorkingRoot) {
    if (-not (Test-Path -LiteralPath $Executable -PathType Leaf)) {
        return [ordered]@{ status = "unavailable"; message = "LibreOffice executable was not found." }
    }
    $work = New-OwnedDirectory -Root $WorkingRoot -Prefix "ooxml-vnext-lo"
    $stopwatch = [Diagnostics.Stopwatch]::StartNew()
    try {
        $inputDirectory = Join-Path $work "input"
        $outputDirectory = Join-Path $work "output"
        $profileDirectory = Join-Path $work "profile"
        New-Item -ItemType Directory -Path $inputDirectory, $outputDirectory, $profileDirectory | Out-Null
        $copyPath = Join-Path $inputDirectory ("disposable" + [IO.Path]::GetExtension($Path).ToLowerInvariant())
        Copy-Item -LiteralPath $Path -Destination $copyPath
        $arguments = @(
            "--headless", "--nologo", "--nodefault", "--nofirststartwizard", "--nolockcheck",
            "-env:UserInstallation=$(ConvertTo-FileUri $profileDirectory)",
            "--convert-to", "pptx", "--outdir", $outputDirectory, $copyPath
        )
        $stdoutPath = Join-Path $work "stdout.txt"
        $stderrPath = Join-Path $work "stderr.txt"
        $existingLibreOfficeProcessIds = @(
            Get-Process -Name "soffice", "soffice.bin" -ErrorAction SilentlyContinue |
                ForEach-Object { $_.Id }
        )
        $process = Start-Process -FilePath $Executable -ArgumentList $arguments -PassThru -WindowStyle Hidden -RedirectStandardOutput $stdoutPath -RedirectStandardError $stderrPath
        $observedProcessIds = @{}
        $peakWorkingSetBytes = [int64]0
        $peakPrivateMemoryBytes = [int64]0
        $peakTotalCpuMs = 0.0
        $completed = $false
        while ($stopwatch.Elapsed.TotalSeconds -lt $Timeout) {
            $observed = @(
                Get-Process -Name "soffice", "soffice.bin" -ErrorAction SilentlyContinue |
                    Where-Object { $_.Id -notin $existingLibreOfficeProcessIds }
            )
            $workingSet = [int64]0
            $privateMemory = [int64]0
            $totalCpuMs = 0.0
            foreach ($item in $observed) {
                $observedProcessIds[$item.Id] = $true
                $workingSet += [int64]$item.WorkingSet64
                $privateMemory += [int64]$item.PrivateMemorySize64
                if ($null -ne $item.CPU) { $totalCpuMs += [double]$item.CPU * 1000 }
            }
            $peakWorkingSetBytes = [Math]::Max($peakWorkingSetBytes, $workingSet)
            $peakPrivateMemoryBytes = [Math]::Max($peakPrivateMemoryBytes, $privateMemory)
            $peakTotalCpuMs = [Math]::Max($peakTotalCpuMs, $totalCpuMs)
            if ($process.WaitForExit(100)) { $completed = $true; break }
        }
        if (-not $completed) {
            $process.Kill($true)
            $process.WaitForExit()
            $stopwatch.Stop()
            return [ordered]@{
                status = "timed-out"
                disposableCopyOnly = $true
                performance = [ordered]@{ wallClockMs = [int64]$stopwatch.ElapsedMilliseconds }
            }
        }
        $process.WaitForExit()
        $process.Refresh()
        $exitCode = [int]$process.ExitCode
        $stopwatch.Stop()
        $performance = [ordered]@{
            wallClockMs = [int64]$stopwatch.ElapsedMilliseconds
            peakWorkingSetBytes = $peakWorkingSetBytes
            peakPrivateMemoryBytes = $peakPrivateMemoryBytes
            peakObservedTotalCpuMs = [Math]::Round($peakTotalCpuMs, 3)
            observedProcessIds = @($observedProcessIds.Keys | Sort-Object)
            sampleIntervalMs = 100
            measurementScope = "new LibreOffice process family; preexisting process ids excluded"
        }
        $converted = Join-Path $outputDirectory "disposable.pptx"
        $summary = if (Test-Path -LiteralPath $converted -PathType Leaf) { Get-PackageSummary $converted } else { $null }
        return [ordered]@{
            status = if ($exitCode -eq 0 -and $null -ne $summary -and $summary.readable) { "passed" } else { "failed" }
            exitCode = $exitCode
            disposableCopyOnly = $true
            isolatedUserProfile = $true
            convertedPackage = $summary
            sourceUnchanged = $true
            performance = $performance
            limitation = "LibreOffice conversion is not byte-preservation evidence and says nothing about a PowerPoint repair dialog."
        }
    }
    finally {
        Remove-OwnedDirectory -Path $work -Root $WorkingRoot -Prefix "ooxml-vnext-lo"
    }
}

function New-ManualWitness([string]$Path, [string]$Root) {
    $directory = New-OwnedDirectory -Root $Root -Prefix "ooxml-vnext-visible-witness"
    $copyPath = Join-Path $directory ("disposable" + [IO.Path]::GetExtension($Path).ToLowerInvariant())
    Copy-Item -LiteralPath $Path -Destination $copyPath
    $digest = Get-Sha256 $copyPath
    $template = [ordered]@{
        schemaVersion = "ooxml-vnext/powerpoint-visible-manual-witness/v1"
        status = "awaiting-human-witness"
        createdAtUtc = [DateTime]::UtcNow.ToString("o")
        disposableCopy = [ordered]@{ path = $copyPath; sha256 = $digest; sourceDigestMatchedAtCreation = $true }
        procedure = @(
            "Confirm this is the disposable copy, not the source file.",
            "Open it visibly in PowerPoint without attaching automation or enabling macros.",
            "Record whether a repair dialog appeared; do not save the presentation.",
            "Close only the presentation opened for this witness. Do not terminate PowerPoint processes.",
            "Have the human witness fill witnessedAtUtc, witness, repairDialogObserved, and notes."
        )
        witnessedAtUtc = $null
        witness = $null
        repairDialogObserved = $null
        saved = $false
        notes = $null
    }
    $templatePath = Join-Path $directory "manual-witness.json"
    [IO.File]::WriteAllText($templatePath, ($template | ConvertTo-Json -Depth 8), [Text.UTF8Encoding]::new($false))
    return [ordered]@{
        status = "awaiting-human-witness"
        directory = $directory
        disposableCopy = $copyPath
        template = $templatePath
        launchedPowerPoint = $false
        repairDialogZeroClaimSupported = $false
    }
}

$startedAt = [DateTime]::UtcNow
$input = $null
$resolvedInput = $null
if ($InputPath) {
    if (-not (Test-Path -LiteralPath $InputPath -PathType Leaf)) { throw "Input file not found: $InputPath" }
    $extension = [IO.Path]::GetExtension($InputPath)
    if ($extension -notin @(".pptx", ".pptm")) { throw "Only .pptx and .pptm inputs are accepted." }
    $resolvedInput = (Resolve-Path -LiteralPath $InputPath).Path
    $input = [ordered]@{
        fileName = [IO.Path]::GetFileName($resolvedInput)
        sha256Before = Get-Sha256 $resolvedInput
        package = Get-PackageSummary $resolvedInput
    }
}
if (($RunOpenXmlSdk -or $RunLibreOffice -or $CreateManualWitness) -and $null -eq $input) {
    throw "-InputPath is required for requested verification actions."
}
if (($RunOpenXmlSdk -or $RunLibreOffice -or $CreateManualWitness) -and -not $input.package.readable) {
    throw "Requested verification actions require a readable OOXML presentation package."
}

$powerPoint = Get-ExecutableEvidence $PowerPointExecutable
$powerPoint.processDetected = @((Get-Process -Name POWERPNT -ErrorAction SilentlyContinue)).Count -gt 0
$powerPoint.automationStatus = "not-run"
$powerPoint.reason = "This matrix never attaches to, launches, saves through, or terminates PowerPoint."
$powerPoint.repairDialog = [ordered]@{
    status = "not-observed"
    zeroClaimSupported = $false
    requiredEvidence = "visible human open of the generated disposable copy, without save"
}

$dotnetCommand = Get-Command dotnet -ErrorAction SilentlyContinue
$validatorProject = Join-Path $PSScriptRoot "openxml-validator\OpenXmlValidator.csproj"
$validatorDll = Join-Path $PSScriptRoot "openxml-validator\bin\Release\net9.0\OpenXmlValidator.dll"
$openXml = [ordered]@{
    available = ($null -ne $dotnetCommand) -and (Test-Path -LiteralPath $validatorProject -PathType Leaf)
    implementation = "Microsoft Open XML SDK"
    package = "DocumentFormat.OpenXml"
    exactVersionPin = "3.5.1"
    target = "Microsoft365"
    status = "not-run"
}
if ($RunOpenXmlSdk) { $openXml.result = Invoke-OpenXmlSdk -Path $resolvedInput -ValidatorDll $validatorDll; $openXml.status = $openXml.result.status }

$libreOffice = Get-ExecutableEvidence $LibreOfficeExecutable
$libreOffice.status = "not-run"
$libreOffice.role = "isolated headless conversion/read-back signal"
if ($RunLibreOffice) { $libreOffice.result = Invoke-LibreOfficeReadBack -Path $resolvedInput -Executable $LibreOfficeExecutable -Timeout $TimeoutSeconds -WorkingRoot ([IO.Path]::GetTempPath()); $libreOffice.status = $libreOffice.result.status }

$manualWitness = if ($CreateManualWitness) { New-ManualWitness -Path $resolvedInput -Root $ManualWitnessRoot } else { [ordered]@{ status = "not-created" } }
if ($null -ne $input) {
    $input.sha256After = Get-Sha256 $resolvedInput
    $input.unchanged = $input.sha256Before -eq $input.sha256After
}

$requestedStatuses = @()
if ($RunOpenXmlSdk) { $requestedStatuses += $openXml.status }
if ($RunLibreOffice) { $requestedStatuses += $libreOffice.status }
$overallStatus = if ($null -ne $input -and -not $input.unchanged) { "failed" }
elseif (@($requestedStatuses | Where-Object { $_ -ne "passed" }).Count -gt 0) { "incomplete-or-failed" }
else { "completed" }

$report = [ordered]@{
    schemaVersion = "ooxml-vnext/office-compatibility-matrix/v1"
    startedAtUtc = $startedAt.ToString("o")
    completedAtUtc = [DateTime]::UtcNow.ToString("o")
    overallStatus = $overallStatus
    input = $input
    matrix = [ordered]@{
        powerPointDesktop = $powerPoint
        openXmlSdk = $openXml
        libreOffice = $libreOffice
        visibleRepairDialogWitness = $manualWitness
    }
    claims = [ordered]@{
        openXmlSdkSchemaValidity = if ($RunOpenXmlSdk -and $openXml.status -eq "passed") { "supported" } else { "not-established" }
        libreOfficeReadBack = if ($RunLibreOffice -and $libreOffice.status -eq "passed") { "supported" } else { "not-established" }
        powerPointReadBack = "not-established"
        powerPointRepairDialogZero = "not-established"
    }
    safety = [ordered]@{
        sourceOpenedReadOnly = $true
        sourceDigestChecked = $null -ne $input
        powerPointAutomationCalls = 0
        powerPointSaveCalls = 0
        existingPowerPointProcessAttachOrTermination = $false
        networkProviderCalls = 0
    }
}

$json = $report | ConvertTo-Json -Depth 16
if ($OutputJson) {
    $outputFull = [IO.Path]::GetFullPath($OutputJson)
    if ((Test-Path -LiteralPath $outputFull) -and -not $ForceOutput) { throw "Output already exists; pass -ForceOutput to replace it: $outputFull" }
    $parent = Split-Path -Parent $outputFull
    if ($parent -and -not (Test-Path -LiteralPath $parent -PathType Container)) { New-Item -ItemType Directory -Path $parent | Out-Null }
    [IO.File]::WriteAllText($outputFull, $json, [Text.UTF8Encoding]::new($false))
}
Write-Output $json

if ($overallStatus -eq "completed") { exit 0 }
exit 1
