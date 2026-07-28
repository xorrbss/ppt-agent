[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidateScript({ Test-Path -LiteralPath $_ -PathType Leaf })]
    [string]$InputPptx,

    [Parameter(Mandatory = $true)]
    [string]$OutputDirectory,

    [switch]$SkipCalibration
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$ppLayoutBlank = 12
$ppSaveAsOpenXmlPresentation = 24
$ppAlignLeft = 1
$ppAlignCenter = 2
$ppAutoSizeNone = 0
$ppAutoSizeShapeToFitText = 1

function Get-PowerPointProcessIds {
    return @(
        Get-Process -Name POWERPNT -ErrorAction SilentlyContinue |
            ForEach-Object { [int]$_.Id } |
            Sort-Object -Unique
    )
}

function New-ResultDirectory([string]$Parent) {
    $resolved = [IO.Path]::GetFullPath($Parent)
    New-Item -ItemType Directory -Path $resolved -Force | Out-Null
    $runName = "powerpoint-desktop-" + (Get-Date -Format "yyyyMMdd-HHmmss-fff")
    $result = Join-Path $resolved $runName
    if (Test-Path -LiteralPath $result) {
        throw "Refusing to overwrite existing result directory: $result"
    }
    New-Item -ItemType Directory -Path $result | Out-Null
    return $result
}

function Write-Json([string]$Path, [object]$Value) {
    $Value | ConvertTo-Json -Depth 12 | Set-Content -LiteralPath $Path -Encoding utf8
}

function Get-ComError([Exception]$Exception) {
    return [ordered]@{
        type = $Exception.GetType().FullName
        message = $Exception.Message
        hresult = ("0x{0:X8}" -f ($Exception.HResult -band 0xffffffff))
    }
}

function Get-TextBounds($Shape) {
    $range = $Shape.TextFrame.TextRange
    return [ordered]@{
        left = [double]$range.BoundLeft
        top = [double]$range.BoundTop
        width = [double]$range.BoundWidth
        height = [double]$range.BoundHeight
    }
}

function Get-LineCount($TextRange) {
    try {
        return [int]$TextRange.Lines().Count
    } catch {
        # Office versions disagree on Lines().Count for text that wraps. The
        # raw bounds remain valuable, and the null makes this uncertainty
        # explicit to consumers instead of fabricating a line count.
        return $null
    }
}

function Add-CalibrationProbe($Slide, [hashtable]$Probe, [int]$Index) {
    $column = $Index % 2
    $row = [Math]::Floor($Index / 2)
    $left = 36 + (330 * $column)
    $top = 36 + (70 * ($row % 9))
    $width = if ($Probe.widthMode -eq "fixed") { 270 } else { 520 }
    $height = if ($Probe.lineMode -eq "multiline") { 60 } else { 36 }
    $shape = $Slide.Shapes.AddTextbox(1, $left, $top, $width, $height)
    $shape.TextFrame.MarginLeft = 0
    $shape.TextFrame.MarginRight = 0
    $shape.TextFrame.MarginTop = 0
    $shape.TextFrame.MarginBottom = 0
    $shape.TextFrame.WordWrap = -1
    $shape.TextFrame.AutoSize = $ppAutoSizeNone
    $range = $shape.TextFrame.TextRange
    $range.Text = $Probe.text
    $range.Font.Name = $Probe.fontFamily
    $range.Font.Size = $Probe.fontSizePt
    $range.Font.Bold = if ($Probe.fontWeight -ge 700) { -1 } else { 0 }
    $range.ParagraphFormat.Alignment = if ($Probe.horizontalAlignment -eq "center") {
        $ppAlignCenter
    } else {
        $ppAlignLeft
    }
    if ($Probe.widthMode -eq "content") {
        $shape.TextFrame.AutoSize = $ppAutoSizeShapeToFitText
    }
    $box = [ordered]@{
        left = [double]$shape.Left
        top = [double]$shape.Top
        width = [double]$shape.Width
        height = [double]$shape.Height
    }
    return [ordered]@{
        id = $Probe.id
        fontFamily = $Probe.fontFamily
        fontWeight = $Probe.fontWeight
        fontSizePt = $Probe.fontSizePt
        lineMode = $Probe.lineMode
        horizontalAlignment = $Probe.horizontalAlignment
        widthMode = $Probe.widthMode
        text = $Probe.text
        boxBoundsPt = $box
        textBoundsPt = Get-TextBounds $shape
        lineCount = Get-LineCount $range
        resolvedFontName = [string]$range.Font.Name
    }
}

function New-CalibrationProbes {
    $weights = @(
        @{ label = "regular"; value = 400 },
        @{ label = "bold"; value = 700 }
    )
    $sizes = @(9, 12, 16, 20, 24, 32, 40, 54)
    $result = @()
    foreach ($weight in $weights) {
        foreach ($size in $sizes) {
            foreach ($lineMode in @("single", "multiline")) {
                foreach ($alignment in @("left", "center")) {
                    foreach ($widthMode in @("fixed", "content")) {
                        $result += [ordered]@{
                            id = "noto-sans-kr-$($weight.label)-$($size)pt-$lineMode-$alignment-$widthMode"
                            fontFamily = "Noto Sans KR"
                            fontWeight = $weight.value
                            fontSizePt = $size
                            lineMode = $lineMode
                            horizontalAlignment = $alignment
                            widthMode = $widthMode
                            text = if ($lineMode -eq "single") {
                                "PowerPoint 가나다 ABC 123"
                            } else {
                                "PowerPoint 가나다 ABC`n줄 간격 측정 123"
                            }
                        }
                    }
                }
            }
        }
    }
    return $result
}

$startedAt = (Get-Date).ToUniversalTime().ToString("o")
$resultDirectory = New-ResultDirectory $OutputDirectory
$reportPath = Join-Path $resultDirectory "powerpoint-desktop-report.json"
$calibrationPath = Join-Path $resultDirectory "powerpoint-text-calibration.json"
$sourceCopy = Join-Path $resultDirectory "A-source-copy.pptx"
$desktopSaveAs = Join-Path $resultDirectory "B-powerpoint-saveas.pptx"
$before = Get-PowerPointProcessIds
$report = [ordered]@{
    schemaVersion = 1
    startedAt = $startedAt
    inputPptx = [IO.Path]::GetFullPath($InputPptx)
    resultDirectory = $resultDirectory
    status = "failed"
    safety = [ordered]@{
        preexistingPowerPointProcessIds = $before
        neverTerminatesProcesses = $true
        openedOnlyCopiedInput = $true
    }
    ab = [ordered]@{
        aSourceCopy = $sourceCopy
        bDesktopSaveAs = $desktopSaveAs
        open = $null
        saveAs = $null
        reopen = $null
    }
    calibration = [ordered]@{
        requested = -not $SkipCalibration
        path = if ($SkipCalibration) { $null } else { $calibrationPath }
        probeCount = 0
    }
    errors = @()
}

if ($before.Count -gt 0) {
    $report.status = "blocked"
    $report.errors += [ordered]@{
        type = "existing_powerpoint_processes"
        message = "PowerPoint was already running. This tool did not attach to, close, or automate the user's instance."
        processIds = $before
    }
    $report.completedAt = (Get-Date).ToUniversalTime().ToString("o")
    Write-Json $reportPath $report
    Write-Output $reportPath
    exit 4
}

$application = $null
$createdProcessIds = @()
try {
    Copy-Item -LiteralPath $InputPptx -Destination $sourceCopy -ErrorAction Stop
    $application = New-Object -ComObject PowerPoint.Application
    Start-Sleep -Milliseconds 250
    $createdProcessIds = @(Get-PowerPointProcessIds | Where-Object { $_ -notin $before })
    $report.safety.createdPowerPointProcessIds = $createdProcessIds

    $presentation = $application.Presentations.Open($sourceCopy, $false, $false, $false)
    $report.ab.open = [ordered]@{
        passed = $true
        slideCount = [int]$presentation.Slides.Count
    }
    $presentation.SaveAs($desktopSaveAs, $ppSaveAsOpenXmlPresentation)
    $presentation.Close()
    $report.ab.saveAs = [ordered]@{
        passed = Test-Path -LiteralPath $desktopSaveAs -PathType Leaf
        bytes = if (Test-Path -LiteralPath $desktopSaveAs) { (Get-Item -LiteralPath $desktopSaveAs).Length } else { 0 }
    }
    if (-not $report.ab.saveAs.passed) { throw "PowerPoint SaveAs did not create B output." }

    $reopened = $application.Presentations.Open($desktopSaveAs, $true, $false, $false)
    $report.ab.reopen = [ordered]@{
        passed = $true
        slideCount = [int]$reopened.Slides.Count
    }
    $reopened.Close()

    if (-not $SkipCalibration) {
        $calibrationDeck = $application.Presentations.Add()
        $probes = New-CalibrationProbes
        $measurements = @()
        for ($index = 0; $index -lt $probes.Count; $index += 1) {
            if (($index % 18) -eq 0) {
                $slide = $calibrationDeck.Slides.Add($calibrationDeck.Slides.Count + 1, $ppLayoutBlank)
            }
            $measurements += Add-CalibrationProbe $slide $probes[$index] $index
        }
        $calibrationDeck.Close()
        $calibration = [ordered]@{
            schemaVersion = 1
            renderer = "PowerPoint Desktop COM"
            collectedAt = (Get-Date).ToUniversalTime().ToString("o")
            fontFamily = "Noto Sans KR"
            units = "points"
            dimensions = @("fontWeight", "fontSizePt", "lineMode", "horizontalAlignment", "widthMode")
            measurements = $measurements
        }
        Write-Json $calibrationPath $calibration
        $report.calibration.probeCount = $measurements.Count
    }
    $report.status = "passed"
} catch {
    $report.errors += Get-ComError $_.Exception
} finally {
    if ($null -ne $application) {
        $current = Get-PowerPointProcessIds
        $unexpected = @($current | Where-Object { $_ -notin $createdProcessIds })
        if ($unexpected.Count -eq 0 -and $createdProcessIds.Count -gt 0) {
            try {
                $application.Quit()
                $report.safety.comApplicationQuit = $true
            } catch {
                $report.errors += Get-ComError $_.Exception
            }
        } else {
            $report.safety.comApplicationQuit = $false
            $report.safety.cleanupSkippedReason = "A PowerPoint process was not created by this isolated run."
        }
        [void][Runtime.InteropServices.Marshal]::FinalReleaseComObject($application)
    }
    $report.completedAt = (Get-Date).ToUniversalTime().ToString("o")
    Write-Json $reportPath $report
}

Write-Output $reportPath
if ($report.status -ne "passed") { exit 1 }
