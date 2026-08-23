param(
  [Parameter(Mandatory = $true)]
  [string[]]$Presentation,
  [Parameter(Mandatory = $true)]
  [string]$OutputDirectory,
  [string]$PresentationId,
  [int]$PresentationRevision = -1,
  [ValidateSet("fidelity", "hybrid")]
  [string[]]$ExportMode = @(),
  [string[]]$ExportAttemptId = @()
)

$ErrorActionPreference = "Stop"
$semanticOwnershipPrefix = "presenton-semantic-raster-ownership-v1:"

function Get-Sha256Text([string]$Value) {
  $bytes = [Text.Encoding]::UTF8.GetBytes($Value)
  $hash = [Security.Cryptography.SHA256]::Create().ComputeHash($bytes)
  "sha256:" + (($hash | ForEach-Object { $_.ToString("x2") }) -join "")
}

Add-Type @'
using System;
using System.Runtime.InteropServices;
public static class PresentonPowerPointApplicationWindowIdentity {
  [DllImport("user32.dll")]
  private static extern uint GetWindowThreadProcessId(IntPtr hwnd, out uint processId);
  public static int ProcessId(long hwnd) {
    if (hwnd == 0) return 0;
    uint processId;
    GetWindowThreadProcessId(new IntPtr(hwnd), out processId);
    return (int)processId;
  }
}
'@

function Get-PowerPointApplicationIdentity($Application) {
  $hwnd = [int64]$Application.HWND
  $processId = [PresentonPowerPointApplicationWindowIdentity]::ProcessId($hwnd)
  if ($hwnd -eq 0 -or $processId -eq 0) {
    return $null
  }
  [PSCustomObject]@{
    Hwnd = $hwnd
    ProcessId = [int]$processId
  }
}

function Get-PowerPointProcessCensus {
  @(
    Get-CimInstance Win32_Process -Filter "Name = 'POWERPNT.EXE'" |
      Sort-Object ProcessId |
      ForEach-Object {
        [PSCustomObject]@{
          ProcessId = [int]$_.ProcessId
          ParentProcessId = [int]$_.ParentProcessId
          ExecutablePath = [string]$_.ExecutablePath
          CommandLine = [string]$_.CommandLine
        }
      }
  )
}

function Read-SemanticOwnershipAlternativeText(
  [string]$AlternativeText,
  [int]$ExpectedSlideNumber
) {
  if (-not $AlternativeText.StartsWith($semanticOwnershipPrefix)) {
    return $null
  }
  $encoded = $AlternativeText.Substring($semanticOwnershipPrefix.Length)
  $base64 = $encoded.Replace('-', '+').Replace('_', '/')
  switch ($base64.Length % 4) {
    2 { $base64 += '==' }
    3 { $base64 += '=' }
    1 { throw "Semantic ownership AlternativeText has invalid base64url length." }
  }
  try {
    $json = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($base64))
    $payload = $json | ConvertFrom-Json
  } catch {
    throw "Semantic ownership AlternativeText is not a valid encoded payload."
  }
  $qids = @($payload.qids)
  if (
    $payload.schemaVersion -ne "presenton-semantic-raster-ownership/v1" -or
    [int]$payload.slideNumber -ne $ExpectedSlideNumber -or
    $qids.Count -lt 1 -or
    @($qids | Where-Object {
      $_ -isnot [string] -or $_.Length -lt 1 -or $_.Length -gt 512 -or
      $_ -ne $_.Trim() -or $_ -match '[\x00-\x1f\x7f]'
    }).Count -ne 0 -or
    @($qids | Sort-Object -Unique).Count -ne $qids.Count -or
    $payload.qidSetDigest -ne (Get-Sha256Text ($qids -join "`n"))
  ) {
    throw "Semantic ownership AlternativeText payload failed validation."
  }
  [PSCustomObject]@{
    SchemaVersion = [string]$payload.schemaVersion
    SlideNumber = [int]$payload.slideNumber
    Qids = @($qids)
    QidSetDigest = [string]$payload.qidSetDigest
  }
}

function Start-PowerPointWindowObserver([string]$StopSignalPath, [string]$ReadySignalPath, [string]$EpochId) {
  Start-Job -ArgumentList $StopSignalPath, $ReadySignalPath, $EpochId -ScriptBlock {
    param([string]$StopPath, [string]$ReadyPath, [string]$ObserverEpochId)
    Add-Type @'
using System;
using System.Collections.Generic;
using System.Runtime.InteropServices;
using System.Text;
public sealed class PresentonPowerPointWindow {
  public long Hwnd; public int ProcessId; public long OwnerHwnd;
  public string ClassName; public string Title; public bool Visible;
}
public static class PresentonPowerPointWindowCensus {
  private delegate bool EnumWindowsProc(IntPtr hwnd, IntPtr lParam);
  [DllImport("user32.dll")] private static extern bool EnumWindows(EnumWindowsProc callback, IntPtr lParam);
  [DllImport("user32.dll")] private static extern uint GetWindowThreadProcessId(IntPtr hwnd, out uint processId);
  [DllImport("user32.dll")] private static extern bool IsWindowVisible(IntPtr hwnd);
  [DllImport("user32.dll")] private static extern IntPtr GetWindow(IntPtr hwnd, uint command);
  [DllImport("user32.dll", CharSet=CharSet.Unicode)] private static extern int GetClassName(IntPtr hwnd, StringBuilder value, int count);
  [DllImport("user32.dll", CharSet=CharSet.Unicode)] private static extern int GetWindowText(IntPtr hwnd, StringBuilder value, int count);
  public static PresentonPowerPointWindow[] Snapshot() {
    var result = new List<PresentonPowerPointWindow>();
    EnumWindows((hwnd, state) => {
      uint processId; GetWindowThreadProcessId(hwnd, out processId);
      if (!IsWindowVisible(hwnd)) return true;
      try {
        var process = System.Diagnostics.Process.GetProcessById((int)processId);
        if (!String.Equals(process.ProcessName, "POWERPNT", StringComparison.OrdinalIgnoreCase)) return true;
      } catch { return true; }
      var className = new StringBuilder(512); GetClassName(hwnd, className, className.Capacity);
      var title = new StringBuilder(2048); GetWindowText(hwnd, title, title.Capacity);
      result.Add(new PresentonPowerPointWindow {
        Hwnd=hwnd.ToInt64(), ProcessId=(int)processId, OwnerHwnd=GetWindow(hwnd, 4).ToInt64(),
        ClassName=className.ToString(), Title=title.ToString(), Visible=true
      });
      return true;
    }, IntPtr.Zero);
    return result.ToArray();
  }
}
'@
    $observed = @{}
    $observedProcesses = @{}
    Set-Content -LiteralPath $ReadyPath -Value "ready" -Encoding ascii
    while (-not (Test-Path -LiteralPath $StopPath)) {
      $now = (Get-Date).ToUniversalTime().ToString("o")
      foreach ($process in [Diagnostics.Process]::GetProcessesByName("POWERPNT")) {
        $key = [string]$process.Id
        if ($observedProcesses.ContainsKey($key)) {
          $observedProcesses[$key].LastSeenAt = $now
          $observedProcesses[$key].ObservationCount += 1
        } else {
          $observedProcesses[$key] = [PSCustomObject]@{
            EpochId = $ObserverEpochId
            ProcessId = [int]$process.Id
            FirstSeenAt = $now
            LastSeenAt = $now
            ObservationCount = 1
          }
        }
        $process.Dispose()
      }
      foreach ($window in [PresentonPowerPointWindowCensus]::Snapshot()) {
        $key = [string]$window.Hwnd
        if ($observed.ContainsKey($key)) {
          $observed[$key].LastSeenAt = $now
          $observed[$key].ObservationCount += 1
          $observed[$key].Title = [string]$window.Title
        } else {
          $observed[$key] = [PSCustomObject]@{
            EpochId = $ObserverEpochId
            Hwnd = [int64]$window.Hwnd
            ProcessId = [int]$window.ProcessId
            OwnerHwnd = [int64]$window.OwnerHwnd
            ClassName = [string]$window.ClassName
            Title = [string]$window.Title
            Visible = [bool]$window.Visible
            IsDialog = $window.ClassName -eq '#32770' -or $window.OwnerHwnd -ne 0
            FirstSeenAt = $now
            LastSeenAt = $now
            ObservationCount = 1
          }
        }
      }
      Start-Sleep -Milliseconds 100
    }
    [PSCustomObject]@{
      EpochId = $ObserverEpochId
      Processes = @($observedProcesses.Values | Sort-Object ProcessId)
      Windows = @($observed.Values | Sort-Object ProcessId, Hwnd)
    }
  }
}

function Wait-PowerPointWindowObserverReady([string]$ReadySignalPath) {
  $deadline = (Get-Date).AddSeconds(10)
  while (-not (Test-Path -LiteralPath $ReadySignalPath) -and (Get-Date) -lt $deadline) {
    Start-Sleep -Milliseconds 50
  }
  if (-not (Test-Path -LiteralPath $ReadySignalPath)) {
    throw "PowerPoint attended observer did not become ready before launch."
  }
}

function Stop-PowerPointWindowObserverEpoch($Observer, [string]$StopSignalPath, [string]$EpochId) {
  Set-Content -LiteralPath $StopSignalPath -Value "stop" -Encoding ascii
  Wait-Job -Job $Observer -Timeout 10 | Out-Null
  if ($Observer.State -ne "Completed") {
    throw "PowerPoint attended window observer did not stop normally."
  }
  $result = @(Receive-Job -Job $Observer)
  Remove-Job -Job $Observer
  if ($result.Count -ne 1 -or [string]$result[0].EpochId -ne $EpochId) {
    throw "PowerPoint attended observer returned an invalid epoch envelope."
  }
  $result[0]
}

function Get-UnexpectedPowerPointObservation(
  [int]$ExpectedProcessId,
  [object[]]$Processes,
  [object[]]$Windows
) {
  $unexpectedProcesses = @($Processes | Where-Object { [int]$_.ProcessId -ne $ExpectedProcessId })
  $unexpectedWindows = @($Windows | Where-Object { [int]$_.ProcessId -ne $ExpectedProcessId })
  $unexpectedProcessIds = @(
    @($unexpectedProcesses | ForEach-Object { $_.ProcessId }) +
      @($unexpectedWindows | ForEach-Object { $_.ProcessId }) |
      Where-Object { [int]$_ -gt 0 } |
      Sort-Object -Unique
  )
  [PSCustomObject]@{
    UnexpectedPowerPointProcessCount = $unexpectedProcessIds.Count
    UnexpectedPowerPointProcessIds = $unexpectedProcessIds
    UnexpectedProcesses = $unexpectedProcesses
    UnexpectedWindows = $unexpectedWindows
  }
}

function Get-PowerPointObserverEpochGuarantee(
  $Observation,
  [string]$ExpectedEpochId,
  [int]$ExpectedProcessId,
  [bool]$RequireWindow
) {
  $processes = @($Observation.Processes)
  $windows = @($Observation.Windows)
  if (
    [string]$Observation.EpochId -ne $ExpectedEpochId -or
    $processes.Count -eq 0 -or
    @($processes | Where-Object { [string]$_.EpochId -ne $ExpectedEpochId }).Count -ne 0 -or
    @($windows | Where-Object { [string]$_.EpochId -ne $ExpectedEpochId }).Count -ne 0 -or
    ($RequireWindow -and $windows.Count -eq 0)
  ) {
    throw "PowerPoint observer epoch identity is invalid."
  }
  $guarantee = Get-UnexpectedPowerPointObservation $ExpectedProcessId $processes $windows
  if ($guarantee.UnexpectedPowerPointProcessCount -ne 0) {
    throw "PowerPoint observer epoch contains an unexpected process or window."
  }
  $guarantee
}

$powerPointPath = "C:\Program Files\Microsoft Office\root\Office16\POWERPNT.EXE"
if (-not (Test-Path -LiteralPath $powerPointPath -PathType Leaf)) {
  throw "PowerPoint Desktop executable was not found: $powerPointPath"
}
$openingProcessCensus = @(Get-PowerPointProcessCensus)
$preexisting = @(Get-Process -Name "POWERPNT" -ErrorAction SilentlyContinue)
if ($preexisting.Count -gt 0) {
  throw "PowerPoint Desktop must not already be running for isolated COM QA."
}

$anyAuthorityPinProvided =
  -not [string]::IsNullOrWhiteSpace($PresentationId) -or
  $PresentationRevision -ge 0 -or
  $ExportMode.Count -gt 0 -or
  $ExportAttemptId.Count -gt 0
$authorityPinsProvided =
  -not [string]::IsNullOrWhiteSpace($PresentationId) -and
  $PresentationRevision -ge 0 -and
  $ExportMode.Count -eq $Presentation.Count -and
  $ExportAttemptId.Count -eq $Presentation.Count
if (
  $anyAuthorityPinProvided -and (
    -not $authorityPinsProvided -or
    @($ExportMode | Sort-Object -Unique).Count -ne $ExportMode.Count -or
    @($ExportAttemptId | Where-Object { [string]::IsNullOrWhiteSpace($_) }).Count -ne 0
  )
) {
  throw "Actual-product COM authority requires presentation/revision and one mode/attempt ID per PPTX."
}

$resolvedOutput = [System.IO.Path]::GetFullPath($OutputDirectory)
[System.IO.Directory]::CreateDirectory($resolvedOutput) | Out-Null
$launched = $null
$application = $null
$applicationIdentity = $null
$attachedAt = $null
$activationMethod = "safe-mode-running-object-table"
$windowObserver = $null
$observerEpochId = [guid]::NewGuid().ToString("D")
$windowStopSignal = Join-Path $resolvedOutput (".powerpoint-window-observer-" + [guid]::NewGuid().ToString("N") + ".stop")
$windowReadySignal = Join-Path $resolvedOutput (".powerpoint-window-observer-" + [guid]::NewGuid().ToString("N") + ".ready")
$discardedObserverEpochs = @()
$windowEvents = @()
$powerPointProcessEvents = @()
$unexpectedPowerPointObservation = $null
$report = $null
try {
  $windowObserver = Start-PowerPointWindowObserver $windowStopSignal $windowReadySignal $observerEpochId
  Wait-PowerPointWindowObserverReady $windowReadySignal
  $launched = Start-Process -FilePath $powerPointPath -ArgumentList "/safe" -WindowStyle Hidden -PassThru
  for ($attempt = 0; $attempt -lt 40 -and $null -eq $application; $attempt += 1) {
    Start-Sleep -Milliseconds 500
    $candidate = $null
    try {
      $candidate = [Runtime.InteropServices.Marshal]::GetActiveObject(
        "PowerPoint.Application"
      )
      $candidateIdentity = Get-PowerPointApplicationIdentity $candidate
      if ($null -eq $candidateIdentity) {
        [Runtime.InteropServices.Marshal]::FinalReleaseComObject($candidate) | Out-Null
        $candidate = $null
        continue
      }
      if ($candidateIdentity.ProcessId -ne [int]$launched.Id) {
        [Runtime.InteropServices.Marshal]::FinalReleaseComObject($candidate) | Out-Null
        $candidate = $null
        throw "PowerPoint ROT application HWND belongs to a process other than the launched process."
      }
      $application = $candidate
      $applicationIdentity = $candidateIdentity
      $attachedAt = Get-Date
    } catch {
      if ($null -ne $candidate) {
        [Runtime.InteropServices.Marshal]::FinalReleaseComObject($candidate) | Out-Null
        $candidate = $null
      }
      if ($_.Exception.Message -match "ROT application HWND") {
        throw
      }
      # PowerPoint registers its automation object after startup completes.
    }
  }
  if ($null -eq $application) {
    # Some Click-to-Run builds start /safe successfully but never publish that
    # process to the Running Object Table. Shut down only our exact process and
    # use COM activation, which still launches a fresh isolated POWERPNT.EXE.
    $safeModeProcessId = [int]$launched.Id
    if (-not $launched.HasExited) {
      Stop-Process -Id $launched.Id -Force
      Wait-Process -Id $launched.Id -Timeout 10 -ErrorAction SilentlyContinue
    }
    $shutdownDeadline = (Get-Date).AddSeconds(15)
    do {
      $remainingPowerPoint = @(
        Get-Process -Name "POWERPNT" -ErrorAction SilentlyContinue
      )
      if ($remainingPowerPoint.Count -eq 0) {
        break
      }
      Start-Sleep -Milliseconds 250
    } while ((Get-Date) -lt $shutdownDeadline)
    if ($remainingPowerPoint.Count -ne 0) {
      throw "Safe-mode PowerPoint did not fully exit before COM activation."
    }
    $discardedObservation = Stop-PowerPointWindowObserverEpoch `
      $windowObserver $windowStopSignal $observerEpochId
    $windowObserver = $null
    $discardedGuarantee = Get-PowerPointObserverEpochGuarantee `
      $discardedObservation $observerEpochId $safeModeProcessId $false
    $discardedObserverEpochs += [PSCustomObject]@{
      EpochId = $observerEpochId
      Reason = "safe-mode-rot-unavailable"
      ExpectedProcessId = $safeModeProcessId
      ObservedProcessIds = @($discardedObservation.Processes.ProcessId | Sort-Object -Unique)
      UnexpectedPowerPointProcessCount = [int]$discardedGuarantee.UnexpectedPowerPointProcessCount
      CleanupConfirmed = $remainingPowerPoint.Count -eq 0
    }
    foreach ($signalPath in @($windowStopSignal, $windowReadySignal)) {
      if (Test-Path -LiteralPath $signalPath) {
        Remove-Item -LiteralPath $signalPath -Force
      }
    }
    $observerEpochId = [guid]::NewGuid().ToString("D")
    $windowStopSignal = Join-Path $resolvedOutput (".powerpoint-window-observer-" + [guid]::NewGuid().ToString("N") + ".stop")
    $windowReadySignal = Join-Path $resolvedOutput (".powerpoint-window-observer-" + [guid]::NewGuid().ToString("N") + ".ready")
    $windowObserver = Start-PowerPointWindowObserver $windowStopSignal $windowReadySignal $observerEpochId
    Wait-PowerPointWindowObserverReady $windowReadySignal
    # Click-to-Run releases the COM class factory shortly after the process exits.
    Start-Sleep -Seconds 3
    $application = New-Object -ComObject "PowerPoint.Application"
    $attachedAt = Get-Date
    $activationMethod = "com-create-instance"
    $freshProcesses = @(
      Get-Process -Name "POWERPNT" -ErrorAction SilentlyContinue |
        Where-Object { $_.StartTime -ge $attachedAt.AddSeconds(-10) }
    )
    if ($freshProcesses.Count -ne 1) {
      throw "COM activation did not create exactly one fresh PowerPoint process."
    }
    $launched = $freshProcesses[0]
  }

  $application.Visible = -1
  if ($null -eq $applicationIdentity) {
    for ($identityAttempt = 0; $identityAttempt -lt 20 -and $null -eq $applicationIdentity; $identityAttempt += 1) {
      $applicationIdentity = Get-PowerPointApplicationIdentity $application
      if ($null -eq $applicationIdentity) { Start-Sleep -Milliseconds 250 }
    }
  }
  if ($null -eq $applicationIdentity -or $applicationIdentity.ProcessId -ne [int]$launched.Id) {
    throw "PowerPoint Application HWND/PID does not match the launched process."
  }
  Start-Sleep -Milliseconds 500
  $results = @()
  for ($presentationOrdinal = 0; $presentationOrdinal -lt $Presentation.Count; $presentationOrdinal += 1) {
    $inputPath = $Presentation[$presentationOrdinal]
    $currentMode = if ($authorityPinsProvided) { $ExportMode[$presentationOrdinal] } else { $null }
    $currentExportAttemptId = if ($authorityPinsProvided) { $ExportAttemptId[$presentationOrdinal] } else { $null }
    $resolvedInput = (Resolve-Path -LiteralPath $inputPath).Path
    $originalName = [System.IO.Path]::GetFileNameWithoutExtension($resolvedInput)
    # Keep the two nested render directories and reopened copy below the
    # Office COM 255-character filename boundary on Windows worktrees.
    $shortStem = if ($originalName.Length -gt 24) {
      $originalName.Substring(0, 24)
    } else {
      $originalName
    }
    $inputDigestPrefix = (
      Get-FileHash -Algorithm SHA256 -LiteralPath $resolvedInput
    ).Hash.Substring(0, 12).ToLowerInvariant()
    $name = "$shortStem-$inputDigestPrefix"
    $renderDirectory = Join-Path $resolvedOutput $name
    [System.IO.Directory]::CreateDirectory($renderDirectory) | Out-Null
    $reopenedPath = Join-Path $renderDirectory "$name-reopened.pptx"
    $sourceDeck = $null
    $deck = $null
    try {
      # ReadOnly=-1, Untitled=0, WithWindow=-1.  The attended window is required
      # so the dialog observer can prove that no repair UI appeared.
      $firstOpenCallStartedAt = (Get-Date).ToUniversalTime().ToString("o")
      $sourceDeck = $application.Presentations.Open($resolvedInput, -1, 0, -1)
      $firstOpenCallReturnedAt = (Get-Date).ToUniversalTime().ToString("o")
      $fonts = @()
      $sourceFonts = $sourceDeck.Fonts
      try {
        for ($index = 1; $index -le $sourceFonts.Count; $index += 1) {
          $font = $sourceFonts.Item($index)
          try {
            $embedded = $null
            try {
              $embedded = [bool]$font.Embedded
            } catch {
              $embedded = $null
            }
            $fonts += [PSCustomObject]@{
              Name = [string]$font.Name
              Embedded = $embedded
            }
          } finally {
            [Runtime.InteropServices.Marshal]::FinalReleaseComObject($font) | Out-Null
            $font = $null
          }
        }
      } finally {
        [Runtime.InteropServices.Marshal]::FinalReleaseComObject($sourceFonts) | Out-Null
        $sourceFonts = $null
      }
      $sourceShapeCounts = @(
        for ($slideIndex = 1; $slideIndex -le $sourceDeck.Slides.Count; $slideIndex += 1) {
          [int]$sourceDeck.Slides.Item($slideIndex).Shapes.Count
        }
      )
      $sourceShapeReadback = @()
      for ($slideIndex = 1; $slideIndex -le $sourceDeck.Slides.Count; $slideIndex += 1) {
        $sourceShapes = $sourceDeck.Slides.Item($slideIndex).Shapes
        for ($shapeIndex = 1; $shapeIndex -le $sourceShapes.Count; $shapeIndex += 1) {
          $sourceShape = $sourceShapes.Item($shapeIndex)
          try {
            $sourceHasText = [bool]$sourceShape.HasTextFrame -and [bool]$sourceShape.TextFrame2.HasText
            $sourceText = if ($sourceHasText) {
              [string]$sourceShape.TextFrame2.TextRange.Text
            } else { $null }
            $sourceAlternativeText = try {
              [string]$sourceShape.AlternativeText
            } catch { "" }
            $sourceSemanticOwnership = Read-SemanticOwnershipAlternativeText `
              $sourceAlternativeText $slideIndex
            $sourceShapeReadback += [PSCustomObject]@{
              SlideNumber = $slideIndex
              Name = [string]$sourceShape.Name
              AlternativeText = $sourceAlternativeText
              Qids = if ($null -ne $sourceSemanticOwnership) {
                @($sourceSemanticOwnership.Qids)
              } else { @() }
              SemanticOwnershipQidSetDigest = if ($null -ne $sourceSemanticOwnership) {
                [string]$sourceSemanticOwnership.QidSetDigest
              } else { $null }
              Type = [int]$sourceShape.Type
              ZOrderPosition = [int]$sourceShape.ZOrderPosition
              HasText = $sourceHasText
              Text = $sourceText
              TextLineBreakCount = if ($null -ne $sourceText) {
                [regex]::Matches($sourceText, "[`r`n`v]").Count
              } else { 0 }
              Typeface = if ($sourceHasText) {
                [string]$sourceShape.TextFrame2.TextRange.Font.Name
              } else { $null }
              EastAsianTypeface = if ($sourceHasText) {
                [string]$sourceShape.TextFrame2.TextRange.Font.NameFarEast
              } else { $null }
              LeftPt = [double]$sourceShape.Left
              TopPt = [double]$sourceShape.Top
              WidthPt = [double]$sourceShape.Width
              HeightPt = [double]$sourceShape.Height
            }
          } finally {
            [Runtime.InteropServices.Marshal]::FinalReleaseComObject($sourceShape) | Out-Null
          }
        }
        [Runtime.InteropServices.Marshal]::FinalReleaseComObject($sourceShapes) | Out-Null
      }
      $firstOpenRenderDirectory = Join-Path $renderDirectory "first-open"
      [System.IO.Directory]::CreateDirectory($firstOpenRenderDirectory) | Out-Null
      $sourceDeck.Export($firstOpenRenderDirectory, "PNG", 1280, 720)
      $firstOpenPngs = @(
        Get-ChildItem -LiteralPath $firstOpenRenderDirectory -Filter "*.PNG" -File |
          Sort-Object @{ Expression = {
            if ($_.BaseName -match "(\d+)$") { return [int]$Matches[1] }
            return [int]::MaxValue
          } }, Name
      )
      $firstOpenRenderedPngSha256 = @($firstOpenPngs | ForEach-Object {
        (Get-FileHash -Algorithm SHA256 -LiteralPath $_.FullName).Hash.ToLowerInvariant()
      })
      # ppSaveAsOpenXMLPresentation=24.  Save a copy, close the source, and
      # inspect only the reopened copy so render/readback evidence cannot be
      # satisfied by the initial in-memory representation.
      $sourceDeck.SaveCopyAs($reopenedPath, 24)
      $sourceDeck.Close()
      [Runtime.InteropServices.Marshal]::FinalReleaseComObject($sourceDeck) | Out-Null
      $sourceDeck = $null
      $reopenCallStartedAt = (Get-Date).ToUniversalTime().ToString("o")
      $deck = $application.Presentations.Open($reopenedPath, -1, 0, -1)
      $reopenCallReturnedAt = (Get-Date).ToUniversalTime().ToString("o")
      $reopenedShapeCounts = @(
        for ($slideIndex = 1; $slideIndex -le $deck.Slides.Count; $slideIndex += 1) {
          [int]$deck.Slides.Item($slideIndex).Shapes.Count
        }
      )
      $nativeText = 0
      $nativeImage = 0
      $nativeShape = 0
      $nativeTable = 0
      $nativeChart = 0
      $typographyIssuesGt5Pt = 0
      $typographyIssuesGt10Pt = 0
      $inspectionFailures = 0
      $shapeReadback = @()
      $backplateZOrderPositions = @()
      $nativeZOrderPositions = @()
      for ($slideIndex = 1; $slideIndex -le $deck.Slides.Count; $slideIndex += 1) {
        $shapes = $deck.Slides.Item($slideIndex).Shapes
        for ($shapeIndex = 1; $shapeIndex -le $shapes.Count; $shapeIndex += 1) {
          $shape = $shapes.Item($shapeIndex)
          $textRange = $null
          try {
            $hasText = [bool]$shape.HasTextFrame -and [bool]$shape.TextFrame2.HasText
            $hasTable = [bool]$shape.HasTable
            $hasChart = [bool]$shape.HasChart
            $shapeName = ([string]$shape.Name).ToLowerInvariant()
            $isBackplate = $shapeName.Contains("presenton hybrid backplate")
            $isSemanticOwnershipCarrier =
              $shapeName.StartsWith("presenton raster visual fragment ") -and
              $shapeName.EndsWith(" semantic-owner")
            $shapeType = [int]$shape.Type
            $zOrderPosition = [int]$shape.ZOrderPosition
            $isCanvasBackground =
              -not $isBackplate -and
              -not $hasText -and
              -not $hasTable -and
              -not $hasChart -and
              $shapeType -notin 11, 13 -and
              [Math]::Abs([double]$shape.Left) -lt 0.01 -and
              [Math]::Abs([double]$shape.Top) -lt 0.01 -and
              [Math]::Abs([double]$shape.Width - 960.0) -lt 0.01 -and
              [Math]::Abs([double]$shape.Height - 540.0) -lt 0.01
            if ($isBackplate) {
              $backplateZOrderPositions += $zOrderPosition
            } elseif (-not $isCanvasBackground -and -not $isSemanticOwnershipCarrier) {
              $nativeZOrderPositions += $zOrderPosition
            }
            if ($hasText) { $nativeText += 1 }
            if ($hasTable) { $nativeTable += 1 }
            if ($hasChart) { $nativeChart += 1 }
            if (-not $isBackplate -and $shapeType -in 11, 13) {
              $nativeImage += 1
            } elseif (-not $isBackplate -and -not $isSemanticOwnershipCarrier -and -not $hasText -and -not $hasTable -and -not $hasChart -and $shapeType -notin 11, 13) {
              $nativeShape += 1
            }
            if ($hasText) {
              $textRange = $shape.TextFrame2.TextRange
              $overflow = [Math]::Max(
                [double]$textRange.BoundHeight - [double]$shape.Height,
                [double]$textRange.BoundWidth - [double]$shape.Width
              )
              if ($overflow -gt 5) { $typographyIssuesGt5Pt += 1 }
              if ($overflow -gt 10) { $typographyIssuesGt10Pt += 1 }
            }
            $textValue = if ($hasText) { [string]$shape.TextFrame2.TextRange.Text } else { $null }
            $alternativeText = try {
              [string]$shape.AlternativeText
            } catch { "" }
            $semanticOwnership = Read-SemanticOwnershipAlternativeText `
              $alternativeText $slideIndex
            $shapeReadback += [PSCustomObject]@{
              SlideNumber = $slideIndex
              Name = [string]$shape.Name
              AlternativeText = $alternativeText
              Qids = if ($null -ne $semanticOwnership) {
                @($semanticOwnership.Qids)
              } else { @() }
              SemanticOwnershipQidSetDigest = if ($null -ne $semanticOwnership) {
                [string]$semanticOwnership.QidSetDigest
              } else { $null }
              Type = $shapeType
              ZOrderPosition = $zOrderPosition
              IsBackplate = $isBackplate
              IsSemanticOwnershipCarrier = $isSemanticOwnershipCarrier
              IsCanvasBackground = $isCanvasBackground
              HasText = $hasText
              Text = $textValue
              TextLineBreakCount = if ($null -ne $textValue) {
                [regex]::Matches($textValue, "[`r`n`v]").Count
              } else { 0 }
              Typeface = if ($hasText) { [string]$shape.TextFrame2.TextRange.Font.Name } else { $null }
              EastAsianTypeface = if ($hasText) { [string]$shape.TextFrame2.TextRange.Font.NameFarEast } else { $null }
              LeftPt = [double]$shape.Left
              TopPt = [double]$shape.Top
              WidthPt = [double]$shape.Width
              HeightPt = [double]$shape.Height
            }
          } catch {
            $inspectionFailures += 1
          } finally {
            if ($null -ne $textRange) {
              [Runtime.InteropServices.Marshal]::FinalReleaseComObject($textRange) | Out-Null
              $textRange = $null
            }
            [Runtime.InteropServices.Marshal]::FinalReleaseComObject($shape) | Out-Null
          }
        }
        [Runtime.InteropServices.Marshal]::FinalReleaseComObject($shapes) | Out-Null
      }
      $reopenedRenderDirectory = Join-Path $renderDirectory "reopened-render"
      [System.IO.Directory]::CreateDirectory($reopenedRenderDirectory) | Out-Null
      $deck.Export($reopenedRenderDirectory, "PNG", 1280, 720)
      $pngs = @(
        Get-ChildItem -LiteralPath $reopenedRenderDirectory -Filter "*.PNG" -File |
          Sort-Object @{ Expression = {
            if ($_.BaseName -match "(\d+)$") {
              return [int]$Matches[1]
            }
            return [int]::MaxValue
          } }, Name
      )
      $reopenedShapeReadback = @($shapeReadback | Select-Object `
        SlideNumber, Name, AlternativeText, Qids, SemanticOwnershipQidSetDigest, `
        Type, ZOrderPosition, HasText, Text, `
        TextLineBreakCount, Typeface, EastAsianTypeface, `
        LeftPt, TopPt, WidthPt, HeightPt)
      $shapeReadbackPreserved =
        ($sourceShapeReadback | ConvertTo-Json -Depth 4 -Compress) -eq
        ($reopenedShapeReadback | ConvertTo-Json -Depth 4 -Compress)
      $sourceSemanticQids = @(
        $sourceShapeReadback | ForEach-Object { @($_.Qids) } | Sort-Object -Unique
      )
      $reopenedSemanticQids = @(
        $shapeReadback | ForEach-Object { @($_.Qids) } | Sort-Object -Unique
      )
      $semanticOwnershipPreserved =
        ($sourceSemanticQids.Count -gt 0 -or -not $authorityPinsProvided) -and
        ($sourceSemanticQids | ConvertTo-Json -Compress) -eq
        ($reopenedSemanticQids | ConvertTo-Json -Compress)
      $renderHashesPreserved =
        $firstOpenRenderedPngSha256.Count -eq $pngs.Count -and
        (@(Compare-Object $firstOpenRenderedPngSha256 @($pngs | ForEach-Object {
          (Get-FileHash -Algorithm SHA256 -LiteralPath $_.FullName).Hash.ToLowerInvariant()
        }) -SyncWindow 0).Count -eq 0)
      $readbackPassed =
        $sourceShapeCounts.Count -eq $reopenedShapeCounts.Count -and
        (@(Compare-Object $sourceShapeCounts $reopenedShapeCounts -SyncWindow 0).Count -eq 0) -and
        $pngs.Count -eq [int]$deck.Slides.Count -and
        $firstOpenPngs.Count -eq [int]$deck.Slides.Count -and
        $shapeReadbackPreserved -and
        $semanticOwnershipPreserved -and
        $renderHashesPreserved -and
        $typographyIssuesGt5Pt -eq 0 -and
        $typographyIssuesGt10Pt -eq 0 -and
        $inspectionFailures -eq 0
      $results += [PSCustomObject]@{
        Path = $resolvedInput
        ExportMode = $currentMode
        ExportAttemptId = $currentExportAttemptId
        FirstOpenCallStartedAt = $firstOpenCallStartedAt
        FirstOpenCallReturnedAt = $firstOpenCallReturnedAt
        ReopenCallStartedAt = $reopenCallStartedAt
        ReopenCallReturnedAt = $reopenCallReturnedAt
        ReopenedPath = $reopenedPath
        OpenedReadOnly = [bool]$deck.ReadOnly
        SlideCount = [int]$deck.Slides.Count
        FontCount = [int]$deck.Fonts.Count
        Fonts = $fonts
        SourceShapeCounts = $sourceShapeCounts
        ReopenedShapeCounts = $reopenedShapeCounts
        Reopened = $true
        ReadbackPassed = $readbackPassed
        ExportedPngs = $pngs.Count
        RenderedPngSha256 = @($pngs | ForEach-Object {
          (Get-FileHash -Algorithm SHA256 -LiteralPath $_.FullName).Hash.ToLowerInvariant()
        })
        FirstOpenRenderedPngSha256 = $firstOpenRenderedPngSha256
        FirstOpenReopenedRenderHashesMatch = $renderHashesPreserved
        ShapeReadbackPreserved = $shapeReadbackPreserved
        SemanticOwnershipPreserved = $semanticOwnershipPreserved
        SourceSemanticQids = $sourceSemanticQids
        ReopenedSemanticQids = $reopenedSemanticQids
        SourceShapeReadback = $sourceShapeReadback
        NativeText = $nativeText
        NativeImage = $nativeImage
        NativeShape = $nativeShape
        NativeTable = $nativeTable
        NativeChart = $nativeChart
        TypographyIssuesGt5Pt = $typographyIssuesGt5Pt
        TypographyIssuesGt10Pt = $typographyIssuesGt10Pt
        InspectionFailures = $inspectionFailures
        ShapeReadback = $shapeReadback
        BackplateCount = $backplateZOrderPositions.Count
        NativeBackplateZOrderViolations = @(
          $nativeZOrderPositions | Where-Object {
            $backplateZOrderPositions.Count -gt 0 -and
            $_ -le ($backplateZOrderPositions | Measure-Object -Maximum).Maximum
          }
        ).Count
        RepairWarningObserved = $null
      }
    } finally {
      if ($null -ne $deck) {
        $deck.Close()
        [Runtime.InteropServices.Marshal]::FinalReleaseComObject($deck) | Out-Null
      }
      if ($null -ne $sourceDeck) {
        $sourceDeck.Close()
        [Runtime.InteropServices.Marshal]::FinalReleaseComObject($sourceDeck) | Out-Null
      }
    }
  }

  $observerResult = Stop-PowerPointWindowObserverEpoch `
    $windowObserver $windowStopSignal $observerEpochId
  $windowObserver = $null
  $windowEvents = @($observerResult.Windows)
  $powerPointProcessEvents = @($observerResult.Processes)
  $unexpectedPowerPointObservation = Get-PowerPointObserverEpochGuarantee `
    $observerResult $observerEpochId $applicationIdentity.ProcessId $true
  $dialogEvents = @($windowEvents | Where-Object { $_.IsDialog -eq $true })
  if ($dialogEvents.Count -ne 0) {
    throw "PowerPoint attended window observer captured an unexpected dialog."
  }
  foreach ($result in $results) {
    $result.RepairWarningObserved = $false
  }

  $report = [PSCustomObject]@{
    PowerPointVersion = [string]$application.Version
    LaunchedProcessId = [int]$launched.Id
    AttachedApplicationHwnd = [int64]$applicationIdentity.Hwnd
    AttachedApplicationProcessId = [int]$applicationIdentity.ProcessId
    ObservedPowerPointProcesses = $powerPointProcessEvents
    UnexpectedPowerPointProcessCount = [int]$unexpectedPowerPointObservation.UnexpectedPowerPointProcessCount
    UnexpectedPowerPointProcessIds = $unexpectedPowerPointObservation.UnexpectedPowerPointProcessIds
    UnexpectedPowerPointProcesses = $unexpectedPowerPointObservation.UnexpectedProcesses
    UnexpectedPowerPointWindows = $unexpectedPowerPointObservation.UnexpectedWindows
    ObserverEpoch = [PSCustomObject]@{
      EpochId = $observerEpochId
      ExpectedProcessId = [int]$applicationIdentity.ProcessId
      ActivationMethod = $activationMethod
      StartedBeforeActivation = $true
    }
    DiscardedObserverEpochs = $discardedObserverEpochs
    ActivationMethod = $activationMethod
    AttachedAt = $attachedAt.ToString("o")
    AttendedWindowEvents = $windowEvents
    RepairDialogObserved = $false
    Presentations = $results
  }
} finally {
  if ($null -ne $windowObserver) {
    try {
      Set-Content -LiteralPath $windowStopSignal -Value "stop" -Encoding ascii
      Wait-Job -Job $windowObserver -Timeout 5 | Out-Null
      Remove-Job -Job $windowObserver -Force -ErrorAction SilentlyContinue
    } catch {
    }
  }
  foreach ($signalPath in @($windowStopSignal, $windowReadySignal)) {
    if (Test-Path -LiteralPath $signalPath) {
      Remove-Item -LiteralPath $signalPath -Force -ErrorAction SilentlyContinue
    }
  }
  if ($null -ne $application) {
    try {
      $application.Quit()
    } catch {
    }
    [Runtime.InteropServices.Marshal]::FinalReleaseComObject($application) |
      Out-Null
    # PowerPoint can remain alive until transient RCWs created by collection
    # property access (Presentations/Slides/Shapes) are finalized.  Drain both
    # finalizer generations before the authoritative process census so a
    # script-host lifetime artifact is not reported as a desktop survivor.
    [GC]::Collect()
    [GC]::WaitForPendingFinalizers()
    [GC]::Collect()
    [GC]::WaitForPendingFinalizers()
  }
  if ($null -ne $launched) {
    try {
      $launched.WaitForExit(10000) | Out-Null
    } catch {
    }
  }
}

$shutdownDeadline = (Get-Date).AddSeconds(15)
do {
  $finalProcessCensus = @(Get-PowerPointProcessCensus)
  if ($finalProcessCensus.Count -eq 0) { break }
  Start-Sleep -Milliseconds 250
} while ((Get-Date) -lt $shutdownDeadline)
if ($finalProcessCensus.Count -ne 0) {
  throw "PowerPoint Desktop process census found a survivor after COM QA."
}

$report | Add-Member -NotePropertyName PowerPointProcessCensusBefore `
  -NotePropertyValue $openingProcessCensus
$report | Add-Member -NotePropertyName PowerPointProcessCensusAfter `
  -NotePropertyValue $finalProcessCensus
$report | ConvertTo-Json -Depth 10 | Set-Content -LiteralPath (
  Join-Path $resolvedOutput "powerpoint-desktop-validation.json"
) -Encoding utf8

if ($authorityPinsProvided) {
  [PSCustomObject]@{
    schemaVersion = "authored-powerpoint-attended-repair-observation/v1"
    authorityDomain = "actual_product"
    presentationId = $PresentationId
    presentationRevision = $PresentationRevision
    repairDialogObserved = $false
    observedAt = (Get-Date).ToUniversalTime().ToString("o")
    windowEvents = $windowEvents
    presentations = @(
      for ($index = 0; $index -lt $Presentation.Count; $index += 1) {
        [PSCustomObject]@{
          mode = $ExportMode[$index]
          exportAttemptId = $ExportAttemptId[$index]
          path = (Resolve-Path -LiteralPath $Presentation[$index]).Path
          firstOpenCallStartedAt = $results[$index].FirstOpenCallStartedAt
          firstOpenCallReturnedAt = $results[$index].FirstOpenCallReturnedAt
          reopenCallStartedAt = $results[$index].ReopenCallStartedAt
          reopenCallReturnedAt = $results[$index].ReopenCallReturnedAt
        }
      }
    )
  } | ConvertTo-Json -Depth 10 | Set-Content -LiteralPath (
    Join-Path $resolvedOutput "powerpoint-attended-repair-observation.json"
  ) -Encoding utf8

  [PSCustomObject]@{
    schemaVersion = "authored-powerpoint-process-census/v1"
    authorityDomain = "actual_product"
    presentationId = $PresentationId
    presentationRevision = $PresentationRevision
    censusCompletedAt = (Get-Date).ToUniversalTime().ToString("o")
    openingProcesses = $openingProcessCensus
    observedProcesses = $powerPointProcessEvents
    observedWindows = $windowEvents
    expectedApplicationProcessId = [int]$applicationIdentity.ProcessId
    observerEpoch = $report.ObserverEpoch
    discardedObserverEpochs = $discardedObserverEpochs
    unexpectedPowerPointProcessCount = [int]$unexpectedPowerPointObservation.UnexpectedPowerPointProcessCount
    unexpectedPowerPointProcessIds = $unexpectedPowerPointObservation.UnexpectedPowerPointProcessIds
    unexpectedProcesses = $unexpectedPowerPointObservation.UnexpectedProcesses
    unexpectedWindows = $unexpectedPowerPointObservation.UnexpectedWindows
    finalProcesses = $finalProcessCensus
    finalPowerPointProcessCount = $finalProcessCensus.Count
    modes = @(
      for ($index = 0; $index -lt $Presentation.Count; $index += 1) {
        [PSCustomObject]@{
          mode = $ExportMode[$index]
          exportAttemptId = $ExportAttemptId[$index]
          launchedPowerPointProcessId = [int]$launched.Id
          unexpectedPowerPointProcessCount = [int]$unexpectedPowerPointObservation.UnexpectedPowerPointProcessCount
          unexpectedPowerPointProcessIds = $unexpectedPowerPointObservation.UnexpectedPowerPointProcessIds
          cleanupConfirmed = $true
        }
      }
    )
  } | ConvertTo-Json -Depth 10 | Set-Content -LiteralPath (
    Join-Path $resolvedOutput "powerpoint-process-census.json"
  ) -Encoding utf8
}

if (Test-Path -LiteralPath $windowStopSignal) {
  Remove-Item -LiteralPath $windowStopSignal -Force
}
if (Test-Path -LiteralPath $windowReadySignal) {
  Remove-Item -LiteralPath $windowReadySignal -Force
}
