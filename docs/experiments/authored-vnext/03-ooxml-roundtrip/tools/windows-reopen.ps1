[CmdletBinding()]
param(
    [string]$InputPath,
    [ValidateSet("Both", "PowerPoint", "LibreOffice")]
    [string]$Engine = "Both",
    [string]$OutputJson,
    [string]$PowerPointExecutable = "C:\Program Files\Microsoft Office\root\Office16\POWERPNT.EXE",
    [string]$LibreOfficeExecutable = "C:\Program Files\LibreOffice\program\soffice.exe",
    [ValidateRange(10, 3600)]
    [int]$TimeoutSeconds = 120,
    [string]$WorkingRoot = [System.IO.Path]::GetTempPath(),
    [switch]$KeepWorkingDirectory,
    [switch]$Worker,
    [string]$WorkerConfig
)

Set-StrictMode -Version 2.0
$ErrorActionPreference = "Stop"

function Write-Utf8NoBom {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][string]$Content
    )

    $parent = Split-Path -Parent $Path
    if ($parent -and -not (Test-Path -LiteralPath $parent)) {
        New-Item -ItemType Directory -Path $parent -Force | Out-Null
    }
    $encoding = New-Object System.Text.UTF8Encoding($false)
    [System.IO.File]::WriteAllText($Path, $Content, $encoding)
}

function Get-Sha256 {
    param([Parameter(Mandatory = $true)][string]$Path)

    return (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToLowerInvariant()
}

function Test-IsDescendantPath {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][string]$Root
    )

    $separator = [System.IO.Path]::DirectorySeparatorChar
    $rootFull = [System.IO.Path]::GetFullPath($Root).TrimEnd($separator, [System.IO.Path]::AltDirectorySeparatorChar) + $separator
    $pathFull = [System.IO.Path]::GetFullPath($Path)
    return $pathFull.StartsWith($rootFull, [System.StringComparison]::OrdinalIgnoreCase)
}

function Get-ExecutableInfo {
    param([Parameter(Mandatory = $true)][string]$Path)

    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        return [ordered]@{
            path = [System.IO.Path]::GetFullPath($Path)
            exists = $false
            fileVersion = $null
            productVersion = $null
        }
    }

    $item = Get-Item -LiteralPath $Path
    return [ordered]@{
        path = $item.FullName
        exists = $true
        fileVersion = $item.VersionInfo.FileVersion
        productVersion = $item.VersionInfo.ProductVersion
    }
}

function Get-OpenXmlPackageSummary {
    param([Parameter(Mandatory = $true)][string]$Path)

    $summary = [ordered]@{
        readable = $false
        partCount = $null
        slidePartCount = $null
        hasContentTypes = $false
        hasPresentationPart = $false
        error = $null
    }

    try {
        Add-Type -AssemblyName System.IO.Compression.FileSystem
        $archive = [System.IO.Compression.ZipFile]::OpenRead($Path)
        try {
            $names = @($archive.Entries | ForEach-Object { $_.FullName.Replace("\\", "/") })
            $summary.partCount = $names.Count
            $summary.slidePartCount = @($names | Where-Object { $_ -match '^ppt/slides/slide[0-9]+\.xml$' }).Count
            $summary.hasContentTypes = $names -contains "[Content_Types].xml"
            $summary.hasPresentationPart = $names -contains "ppt/presentation.xml"
            $summary.readable = $summary.hasContentTypes -and $summary.hasPresentationPart
        }
        finally {
            $archive.Dispose()
        }
    }
    catch {
        $summary.error = $_.Exception.Message
    }

    return $summary
}

function ConvertTo-FileUri {
    param([Parameter(Mandatory = $true)][string]$Path)

    $fullPath = [System.IO.Path]::GetFullPath($Path)
    return (New-Object System.Uri($fullPath)).AbsoluteUri
}

function Release-ComObject {
    param($Value)

    if ($null -ne $Value -and [System.Runtime.InteropServices.Marshal]::IsComObject($Value)) {
        [void][System.Runtime.InteropServices.Marshal]::FinalReleaseComObject($Value)
    }
}

function Invoke-PowerPointWorker {
    param($Config)

    $startedAt = [DateTime]::UtcNow
    $application = $null
    $presentations = $null
    $presentation = $null
    $previousAutomationSecurity = $null
    $inputHashBefore = Get-Sha256 -Path $Config.inputPath
    $inputPackage = Get-OpenXmlPackageSummary -Path $Config.inputPath
    $result = [ordered]@{
        engine = "PowerPoint"
        status = "failed"
        startedAtUtc = $startedAt.ToString("o")
        completedAtUtc = $null
        executable = Get-ExecutableInfo -Path $Config.executable
        input = [ordered]@{
            path = $Config.inputPath
            sha256Before = $inputHashBefore
            sha256After = $null
            unchanged = $null
        }
        open = [ordered]@{
            succeeded = $false
            readOnlyRequested = $true
            withWindow = $false
            macrosForcedDisabled = $true
            automationSecurityRestored = $false
            processIds = @()
            processPaths = @()
            expectedExecutableMatched = $false
            presentationReadOnly = $null
            slideCount = $null
            slideCountMatchesPackage = $null
            fullName = $null
            savedFlag = $null
        }
        packageReadBack = $inputPackage
        repairDialog = [ordered]@{
            status = "not-observable"
            zeroClaimSupported = $false
            reason = "Hidden PowerPoint COM open suppresses UI and exposes no reliable repaired-file or repair-dialog flag. A successful open proves read-back only; repair dialog=0 requires a separate visible human witness on a disposable copy."
        }
        error = $null
    }

    try {
        if (-not $result.executable.exists) {
            throw "PowerPoint executable not found: $($Config.executable)"
        }
        $lateExistingPowerPoint = @(Get-Process -Name POWERPNT -ErrorAction SilentlyContinue)
        if ($lateExistingPowerPoint.Count -gt 0) {
            throw "A PowerPoint process appeared after the parent safety check; refusing COM activation."
        }

        $application = New-Object -ComObject PowerPoint.Application
        # msoAutomationSecurityForceDisable = 3; ppAlertsNone = 1.
        $previousAutomationSecurity = $application.AutomationSecurity
        $application.AutomationSecurity = 3
        $application.DisplayAlerts = 1
        $powerPointProcesses = @(Get-Process -Name POWERPNT -ErrorAction SilentlyContinue)
        $result.open.processIds = @($powerPointProcesses | ForEach-Object { $_.Id })
        $result.open.processPaths = @($powerPointProcesses | ForEach-Object { try { $_.Path } catch { $null } } | Where-Object { $_ })
        $expectedExecutablePath = [System.IO.Path]::GetFullPath($Config.executable)
        $result.open.expectedExecutableMatched = @($result.open.processPaths | Where-Object { [string]::Equals($_, $expectedExecutablePath, [System.StringComparison]::OrdinalIgnoreCase) }).Count -gt 0
        if (-not $result.open.expectedExecutableMatched) {
            throw "PowerPoint COM did not start the expected executable: $expectedExecutablePath"
        }
        $presentations = $application.Presentations
        # MsoTriState: ReadOnly=msoTrue (-1), Untitled/WithWindow=msoFalse (0).
        $presentation = $presentations.Open($Config.inputPath, -1, 0, 0)
        $result.open.succeeded = $true
        $result.open.presentationReadOnly = [bool]$presentation.ReadOnly
        $result.open.slideCount = [int]$presentation.Slides.Count
        $result.open.slideCountMatchesPackage = $result.open.slideCount -eq $inputPackage.slidePartCount
        $result.open.fullName = [string]$presentation.FullName
        $result.open.savedFlag = [bool]$presentation.Saved
        if (-not $result.open.presentationReadOnly) {
            throw "PowerPoint did not report the disposable presentation as read-only."
        }
        if (-not $result.open.slideCountMatchesPackage) {
            throw "PowerPoint slide count does not match the source package slide-part count."
        }
        $result.status = "passed"
    }
    catch {
        $result.error = [ordered]@{
            type = $_.Exception.GetType().FullName
            message = $_.Exception.Message
            hresult = ("0x{0:X8}" -f ($_.Exception.HResult -band 0xffffffffL))
        }
    }
    finally {
        if ($null -ne $presentation) {
            try { $presentation.Close() } catch { }
        }
        if ($null -ne $application) {
            if ($null -ne $previousAutomationSecurity) {
                try {
                    $application.AutomationSecurity = $previousAutomationSecurity
                    $result.open.automationSecurityRestored = $true
                }
                catch { }
            }
            try { $application.Quit() } catch { }
        }
        Release-ComObject -Value $presentation
        Release-ComObject -Value $presentations
        Release-ComObject -Value $application
        [GC]::Collect()
        [GC]::WaitForPendingFinalizers()

        $result.input.sha256After = Get-Sha256 -Path $Config.inputPath
        $result.input.unchanged = $result.input.sha256Before -eq $result.input.sha256After
        if (-not $result.input.unchanged) {
            $result.status = "failed"
            if ($null -eq $result.error) {
                $result.error = [ordered]@{
                    type = "InputMutation"
                    message = "The disposable PowerPoint input copy changed during read-only validation."
                    hresult = $null
                }
            }
        }
        $result.completedAtUtc = [DateTime]::UtcNow.ToString("o")
    }

    return $result
}

function Invoke-LibreOfficeWorker {
    param($Config)

    $startedAt = [DateTime]::UtcNow
    $inputHashBefore = Get-Sha256 -Path $Config.inputPath
    $inputPackage = Get-OpenXmlPackageSummary -Path $Config.inputPath
    $outputDirectory = Join-Path $Config.workingDirectory "converted"
    $profileDirectory = Join-Path $Config.workingDirectory "profile"
    New-Item -ItemType Directory -Path $outputDirectory, $profileDirectory -Force | Out-Null
    $outputPath = Join-Path $outputDirectory ([System.IO.Path]::GetFileName($Config.inputPath))

    $result = [ordered]@{
        engine = "LibreOffice"
        status = "failed"
        startedAtUtc = $startedAt.ToString("o")
        completedAtUtc = $null
        executable = Get-ExecutableInfo -Path $Config.executable
        input = [ordered]@{
            path = $Config.inputPath
            sha256Before = $inputHashBefore
            sha256After = $null
            unchanged = $null
            packageReadBack = $inputPackage
        }
        invocation = [ordered]@{
            headless = $true
            isolatedUserProfile = $profileDirectory
            outputDirectory = $outputDirectory
            exitCode = $null
            stdout = $null
            stderr = $null
            console = $null
        }
        output = [ordered]@{
            path = $outputPath
            exists = $false
            sha256 = $null
            packageReadBack = $null
            slideCountMatchesInput = $null
        }
        repairDialog = [ordered]@{
            status = "not-applicable-headless"
            zeroClaimSupported = $false
            reason = "Headless LibreOffice conversion has no visible dialog surface. Exit code and converted-package read-back do not prove PowerPoint repair dialog=0."
        }
        error = $null
    }

    try {
        if (-not $result.executable.exists) {
            throw "LibreOffice executable not found: $($Config.executable)"
        }

        $arguments = @(
            "--headless",
            "--nologo",
            "--nodefault",
            "--nolockcheck",
            "--norestore",
            "-env:UserInstallation=$(ConvertTo-FileUri -Path $profileDirectory)",
            "--convert-to", "pptx",
            "--outdir", $outputDirectory,
            $Config.inputPath
        )
        $stdoutPath = Join-Path $Config.workingDirectory "libreoffice-stdout.txt"
        $stderrPath = Join-Path $Config.workingDirectory "libreoffice-stderr.txt"
        $argumentLine = @($arguments | ForEach-Object { Quote-NativeArgument -Value ([string]$_) }) -join " "
        $process = Start-Process -FilePath $Config.executable -ArgumentList $argumentLine -PassThru -Wait -WindowStyle Hidden -RedirectStandardOutput $stdoutPath -RedirectStandardError $stderrPath
        $stdoutRaw = if (Test-Path -LiteralPath $stdoutPath) { Get-Content -LiteralPath $stdoutPath -Raw -ErrorAction SilentlyContinue } else { $null }
        $stderrRaw = if (Test-Path -LiteralPath $stderrPath) { Get-Content -LiteralPath $stderrPath -Raw -ErrorAction SilentlyContinue } else { $null }
        $stdout = if ($null -eq $stdoutRaw) { "" } else { $stdoutRaw.Trim() }
        $stderr = if ($null -eq $stderrRaw) { "" } else { $stderrRaw.Trim() }
        $result.invocation.exitCode = $process.ExitCode
        $result.invocation.stdout = $stdout
        $result.invocation.stderr = $stderr
        $result.invocation.console = (@($stdout, $stderr) | Where-Object { $_ }) -join [Environment]::NewLine
        $result.output.exists = Test-Path -LiteralPath $outputPath -PathType Leaf
        if ($result.output.exists) {
            $result.output.sha256 = Get-Sha256 -Path $outputPath
            $result.output.packageReadBack = Get-OpenXmlPackageSummary -Path $outputPath
            $result.output.slideCountMatchesInput = $result.output.packageReadBack.slidePartCount -eq $inputPackage.slidePartCount
        }

        if ($result.invocation.exitCode -ne 0) {
            throw "LibreOffice exited with code $($result.invocation.exitCode)."
        }
        if (-not $result.output.exists) {
            throw "LibreOffice returned success without creating the expected PPTX output."
        }
        if (-not $result.output.packageReadBack.readable) {
            throw "LibreOffice output is not a readable PPTX package."
        }
        if (-not $result.output.slideCountMatchesInput) {
            throw "LibreOffice output slide count does not match the disposable input."
        }

        $result.status = "passed"
    }
    catch {
        $result.error = [ordered]@{
            type = $_.Exception.GetType().FullName
            message = $_.Exception.Message
            fullyQualifiedErrorId = $_.FullyQualifiedErrorId
            scriptStackTrace = $_.ScriptStackTrace
        }
    }
    finally {
        $result.input.sha256After = Get-Sha256 -Path $Config.inputPath
        $result.input.unchanged = $result.input.sha256Before -eq $result.input.sha256After
        if (-not $result.input.unchanged) {
            $result.status = "failed"
            if ($null -eq $result.error) {
                $result.error = [ordered]@{
                    type = "InputMutation"
                    message = "The disposable LibreOffice input copy changed during conversion."
                }
            }
        }
        $result.completedAtUtc = [DateTime]::UtcNow.ToString("o")
    }

    return $result
}

function Invoke-WorkerMode {
    if (-not $WorkerConfig) {
        throw "-WorkerConfig is required with -Worker."
    }

    $configPath = (Resolve-Path -LiteralPath $WorkerConfig).Path
    $config = Get-Content -LiteralPath $configPath -Raw -Encoding UTF8 | ConvertFrom-Json
    try {
        if ($config.engine -eq "PowerPoint") {
            $result = Invoke-PowerPointWorker -Config $config
        }
        elseif ($config.engine -eq "LibreOffice") {
            $result = Invoke-LibreOfficeWorker -Config $config
        }
        else {
            throw "Unsupported worker engine: $($config.engine)"
        }
        Write-Utf8NoBom -Path $config.outputJson -Content ($result | ConvertTo-Json -Depth 12)
        if ($result.status -eq "passed") { exit 0 }
        exit 1
    }
    catch {
        $failure = [ordered]@{
            engine = [string]$config.engine
            status = "worker-error"
            completedAtUtc = [DateTime]::UtcNow.ToString("o")
            error = [ordered]@{
                type = $_.Exception.GetType().FullName
                message = $_.Exception.Message
            }
        }
        Write-Utf8NoBom -Path $config.outputJson -Content ($failure | ConvertTo-Json -Depth 8)
        exit 1
    }
}

function Quote-NativeArgument {
    param([Parameter(Mandatory = $true)][string]$Value)

    return '"' + $Value.Replace('"', '\"') + '"'
}

function New-BlockedPowerPointResult {
    param(
        [string]$Executable,
        [int[]]$ProcessIds
    )

    return [ordered]@{
        engine = "PowerPoint"
        status = "blocked-existing-process"
        startedAtUtc = [DateTime]::UtcNow.ToString("o")
        completedAtUtc = [DateTime]::UtcNow.ToString("o")
        executable = Get-ExecutableInfo -Path $Executable
        existingProcessIds = @($ProcessIds)
        repairDialog = [ordered]@{
            status = "not-observed"
            zeroClaimSupported = $false
            reason = "Validation was not started because an existing PowerPoint session could be user-owned."
        }
        error = [ordered]@{
            type = "ExistingPowerPointProcess"
            message = "Close PowerPoint and rerun. The harness will not attach to or quit an existing user session."
        }
    }
}

function Invoke-BoundedWorker {
    param(
        [Parameter(Mandatory = $true)][ValidateSet("PowerPoint", "LibreOffice")][string]$WorkerEngine,
        [Parameter(Mandatory = $true)][string]$SourcePath,
        [Parameter(Mandatory = $true)][string]$Executable,
        [Parameter(Mandatory = $true)][string]$RunDirectory,
        [Parameter(Mandatory = $true)][int]$Timeout
    )

    $engineDirectory = Join-Path $RunDirectory $WorkerEngine.ToLowerInvariant()
    New-Item -ItemType Directory -Path $engineDirectory -Force | Out-Null
    $inputDirectory = Join-Path $engineDirectory "input"
    New-Item -ItemType Directory -Path $inputDirectory -Force | Out-Null
    $temporaryInput = Join-Path $inputDirectory ([System.IO.Path]::GetFileName($SourcePath))
    Copy-Item -LiteralPath $SourcePath -Destination $temporaryInput

    $workerOutput = Join-Path $engineDirectory "worker-result.json"
    $workerConfig = Join-Path $engineDirectory "worker-config.json"
    $stdoutPath = Join-Path $engineDirectory "worker-stdout.txt"
    $stderrPath = Join-Path $engineDirectory "worker-stderr.txt"
    $config = [ordered]@{
        engine = $WorkerEngine
        inputPath = $temporaryInput
        executable = $Executable
        outputJson = $workerOutput
        workingDirectory = $engineDirectory
    }
    Write-Utf8NoBom -Path $workerConfig -Content ($config | ConvertTo-Json -Depth 6)

    $hostExecutable = (Get-Process -Id $PID).Path
    $argumentLine = @(
        "-NoProfile",
        "-NonInteractive",
        "-ExecutionPolicy", "Bypass",
        "-File", (Quote-NativeArgument -Value $PSCommandPath),
        "-Worker",
        "-WorkerConfig", (Quote-NativeArgument -Value $workerConfig)
    ) -join " "

    $baselinePowerPointIds = @()
    if ($WorkerEngine -eq "PowerPoint") {
        $baselinePowerPointIds = @(Get-Process -Name POWERPNT -ErrorAction SilentlyContinue | ForEach-Object { $_.Id })
        if ($baselinePowerPointIds.Count -gt 0) {
            return New-BlockedPowerPointResult -Executable $Executable -ProcessIds $baselinePowerPointIds
        }
    }
    $process = Start-Process -FilePath $hostExecutable -ArgumentList $argumentLine -PassThru -WindowStyle Hidden -RedirectStandardOutput $stdoutPath -RedirectStandardError $stderrPath
    $timedOut = -not $process.WaitForExit($Timeout * 1000)
    $cleanup = [ordered]@{
        workerProcessTerminated = $false
        newPowerPointProcessIds = @()
        confirmedWorkerChildPowerPointProcessIds = @()
        newPowerPointProcessesTerminated = @()
    }

    if ($timedOut) {
        try {
            Stop-Process -Id $process.Id -Force -ErrorAction Stop
            $cleanup.workerProcessTerminated = $true
        }
        catch { }

        if ($WorkerEngine -eq "PowerPoint") {
            $newPowerPointProcesses = @(Get-Process -Name POWERPNT -ErrorAction SilentlyContinue | Where-Object { $baselinePowerPointIds -notcontains $_.Id })
            $cleanup.newPowerPointProcessIds = @($newPowerPointProcesses | ForEach-Object { $_.Id })
            $directChildren = @()
            foreach ($powerPointProcess in $newPowerPointProcesses) {
                $cimProcess = Get-CimInstance Win32_Process -Filter "ProcessId=$($powerPointProcess.Id)" -ErrorAction SilentlyContinue
                if ($null -ne $cimProcess -and [int]$cimProcess.ParentProcessId -eq $process.Id) {
                    $directChildren += $powerPointProcess
                }
            }
            $cleanup.confirmedWorkerChildPowerPointProcessIds = @($directChildren | ForEach-Object { $_.Id })
            foreach ($powerPointProcess in $directChildren) {
                try {
                    Stop-Process -Id $powerPointProcess.Id -Force -ErrorAction Stop
                    $cleanup.newPowerPointProcessesTerminated += $powerPointProcess.Id
                }
                catch { }
            }
        }

        return [ordered]@{
            engine = $WorkerEngine
            status = "timed-out"
            startedAtUtc = $process.StartTime.ToUniversalTime().ToString("o")
            completedAtUtc = [DateTime]::UtcNow.ToString("o")
            timeoutSeconds = $Timeout
            disposableInput = $temporaryInput
            cleanup = $cleanup
            repairDialog = [ordered]@{
                status = "not-observable"
                zeroClaimSupported = $false
                reason = "The bounded worker timed out; no repair-dialog claim can be made."
            }
            error = [ordered]@{
                type = "WorkerTimeout"
                message = "$WorkerEngine validation exceeded $Timeout seconds."
            }
        }
    }

    if (Test-Path -LiteralPath $workerOutput -PathType Leaf) {
        return Get-Content -LiteralPath $workerOutput -Raw -Encoding UTF8 | ConvertFrom-Json
    }

    $workerStderrRaw = if (Test-Path -LiteralPath $stderrPath) { Get-Content -LiteralPath $stderrPath -Raw -ErrorAction SilentlyContinue } else { $null }
    $workerStderr = if ($null -eq $workerStderrRaw) { "" } else { $workerStderrRaw.Trim() }
    return [ordered]@{
        engine = $WorkerEngine
        status = "worker-error"
        startedAtUtc = $process.StartTime.ToUniversalTime().ToString("o")
        completedAtUtc = [DateTime]::UtcNow.ToString("o")
        exitCode = $process.ExitCode
        repairDialog = [ordered]@{
            status = "not-observable"
            zeroClaimSupported = $false
            reason = "The worker produced no structured result."
        }
        error = [ordered]@{
            type = "MissingWorkerResult"
            message = if ($workerStderr) { $workerStderr } else { "Worker exited without producing JSON." }
        }
    }
}

if ($Worker) {
    Invoke-WorkerMode
}

if (-not $InputPath) {
    throw "-InputPath is required."
}
if (-not (Test-Path -LiteralPath $InputPath -PathType Leaf)) {
    throw "Input PPTX not found: $InputPath"
}
if ([System.IO.Path]::GetExtension($InputPath) -ine ".pptx") {
    throw "Only .pptx input is accepted by this harness."
}
if (-not (Test-Path -LiteralPath $WorkingRoot -PathType Container)) {
    throw "Working root does not exist: $WorkingRoot"
}

$resolvedInput = (Resolve-Path -LiteralPath $InputPath).Path
$sourceHashBefore = Get-Sha256 -Path $resolvedInput
$sourcePackage = Get-OpenXmlPackageSummary -Path $resolvedInput
$startedAt = [DateTime]::UtcNow
$runId = "windows-reopen-{0}-{1}" -f $startedAt.ToString("yyyyMMddTHHmmssfffZ"), ([Guid]::NewGuid().ToString("N").Substring(0, 8))
$workingRootFull = [System.IO.Path]::GetFullPath($WorkingRoot)
$runDirectory = Join-Path $workingRootFull $runId
if (-not (Test-IsDescendantPath -Path $runDirectory -Root $workingRootFull)) {
    throw "Refusing to create a run directory outside the working root: $runDirectory"
}
New-Item -ItemType Directory -Path $runDirectory -Force | Out-Null
$results = New-Object System.Collections.ArrayList

try {
    if ($Engine -in @("Both", "PowerPoint")) {
        $existingPowerPoint = @(Get-Process -Name POWERPNT -ErrorAction SilentlyContinue)
        if ($existingPowerPoint.Count -gt 0) {
            [void]$results.Add((New-BlockedPowerPointResult -Executable $PowerPointExecutable -ProcessIds @($existingPowerPoint | ForEach-Object { $_.Id })))
        }
        else {
            [void]$results.Add((Invoke-BoundedWorker -WorkerEngine "PowerPoint" -SourcePath $resolvedInput -Executable $PowerPointExecutable -RunDirectory $runDirectory -Timeout $TimeoutSeconds))
        }
    }

    if ($Engine -in @("Both", "LibreOffice")) {
        [void]$results.Add((Invoke-BoundedWorker -WorkerEngine "LibreOffice" -SourcePath $resolvedInput -Executable $LibreOfficeExecutable -RunDirectory $runDirectory -Timeout $TimeoutSeconds))
    }
}
finally {
    $sourceHashAfter = Get-Sha256 -Path $resolvedInput
}

$statuses = @($results | ForEach-Object { $_.status })
$overallStatus = if (@($statuses | Where-Object { $_ -eq "failed" -or $_ -eq "worker-error" -or $_ -eq "timed-out" }).Count -gt 0) {
    "failed"
}
elseif (@($statuses | Where-Object { $_ -like "blocked-*" }).Count -gt 0) {
    "blocked"
}
else {
    "passed"
}

$report = [ordered]@{
    schemaVersion = "1.0"
    runId = $runId
    startedAtUtc = $startedAt.ToString("o")
    completedAtUtc = [DateTime]::UtcNow.ToString("o")
    overallStatus = $overallStatus
    source = [ordered]@{
        path = $resolvedInput
        length = (Get-Item -LiteralPath $resolvedInput).Length
        sha256Before = $sourceHashBefore
        sha256After = $sourceHashAfter
        unchanged = $sourceHashBefore -eq $sourceHashAfter
        openedDirectlyByOffice = $false
        packageReadBack = $sourcePackage
    }
    safety = [ordered]@{
        disposableCopiesOnly = $true
        existingPowerPointSessionPolicy = "fail-closed"
        powerPointSaveCalls = 0
        powerPointMacrosForcedDisabled = $true
        boundedWorkerTimeoutSeconds = $TimeoutSeconds
    }
    workingDirectory = [ordered]@{
        path = $runDirectory
        retained = $true
        cleanupStatus = if ($KeepWorkingDirectory) { "retained-by-request" } else { "pending" }
    }
    engines = @($results)
    limitations = @(
        "Hidden PowerPoint COM read-back cannot establish repair dialog=0 because repair UI is not observable and PowerPoint exposes no reliable repair flag.",
        "LibreOffice conversion is a compatibility/read-back signal, not a byte-preserving no-op and not evidence of PowerPoint repair behavior.",
        "A repair dialog=0 claim requires a separate visible PowerPoint open performed by a human on a disposable copy, with no save."
    )
}

if (-not $KeepWorkingDirectory) {
    if (-not (Test-IsDescendantPath -Path $runDirectory -Root $workingRootFull) -or ([System.IO.Path]::GetFileName($runDirectory) -notlike "windows-reopen-*")) {
        throw "Refusing unsafe cleanup target: $runDirectory"
    }
    Remove-Item -LiteralPath $runDirectory -Recurse -Force
    $report.workingDirectory.retained = $false
    $report.workingDirectory.cleanupStatus = "deleted"
}

if (-not $report.source.unchanged) {
    $report.overallStatus = "failed"
}

$json = $report | ConvertTo-Json -Depth 16
if ($OutputJson) {
    $outputFullPath = [System.IO.Path]::GetFullPath($OutputJson)
    Write-Utf8NoBom -Path $outputFullPath -Content $json
}
Write-Output $json

if ($report.overallStatus -eq "passed") { exit 0 }
exit 1
