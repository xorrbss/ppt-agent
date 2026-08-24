param(
  [Parameter(Mandatory = $true)]
  [string]$Presentation,
  [Parameter(Mandatory = $true)]
  [string]$OutputDirectory,
  [Parameter(Mandatory = $true)]
  [string]$PresentationId,
  [Parameter(Mandatory = $true)]
  [ValidateRange(0, [int]::MaxValue)]
  [int]$PresentationRevision,
  [Parameter(Mandatory = $true)]
  [string]$HybridExportAttemptId,
  [string]$BaseWorkerPath = (Join-Path $PSScriptRoot "..\..\servers\nextjs\scripts\qa-powerpoint-desktop.ps1")
)

$ErrorActionPreference = "Stop"

Add-Type @'
using System;
using System.Collections.Generic;
using System.Runtime.InteropServices;
using System.Text;
public sealed class AuthoredEditableHybridPptWindow {
  public long Hwnd; public int ProcessId; public string ClassName; public string Title;
}
public static class AuthoredEditableHybridPptWindowCensus {
  private delegate bool EnumWindowsProc(IntPtr hwnd, IntPtr lParam);
  [DllImport("user32.dll")] private static extern bool EnumWindows(EnumWindowsProc callback, IntPtr lParam);
  [DllImport("user32.dll")] private static extern uint GetWindowThreadProcessId(IntPtr hwnd, out uint processId);
  [DllImport("user32.dll")] private static extern bool IsWindowVisible(IntPtr hwnd);
  [DllImport("user32.dll", CharSet=CharSet.Unicode)] private static extern int GetClassName(IntPtr hwnd, StringBuilder value, int count);
  [DllImport("user32.dll", CharSet=CharSet.Unicode)] private static extern int GetWindowText(IntPtr hwnd, StringBuilder value, int count);
  public static AuthoredEditableHybridPptWindow[] Snapshot() {
    var result = new List<AuthoredEditableHybridPptWindow>();
    EnumWindows((hwnd, state) => {
      if (!IsWindowVisible(hwnd)) return true;
      uint processId; GetWindowThreadProcessId(hwnd, out processId);
      var className = new StringBuilder(512); GetClassName(hwnd, className, className.Capacity);
      if (!String.Equals(className.ToString(), "PPTFrameClass", StringComparison.Ordinal)) return true;
      var title = new StringBuilder(2048); GetWindowText(hwnd, title, title.Capacity);
      result.Add(new AuthoredEditableHybridPptWindow {
        Hwnd=hwnd.ToInt64(), ProcessId=(int)processId,
        ClassName=className.ToString(), Title=title.ToString()
      });
      return true;
    }, IntPtr.Zero);
    return result.ToArray();
  }
}
'@

function Get-PowerPointProcessCensus {
  @(
    Get-CimInstance Win32_Process -Filter "Name = 'POWERPNT.EXE'" |
      Sort-Object ProcessId |
      ForEach-Object {
        [PSCustomObject]@{
          processId = [int]$_.ProcessId
          parentProcessId = [int]$_.ParentProcessId
          creationDate = if ($null -ne $_.CreationDate) { ([datetime]$_.CreationDate).ToUniversalTime().ToString("o") } else { $null }
          executablePath = [string]$_.ExecutablePath
          commandLine = [string]$_.CommandLine
        }
      }
  )
}

function Get-BaselineSurface([int[]]$ProcessIds) {
  @(
    [AuthoredEditableHybridPptWindowCensus]::Snapshot() |
      Where-Object { [int]$_.ProcessId -in $ProcessIds } |
      Sort-Object ProcessId, Hwnd |
      ForEach-Object {
        [PSCustomObject]@{
          processId = [int]$_.ProcessId
          hwnd = [int64]$_.Hwnd
          className = [string]$_.ClassName
          title = [string]$_.Title
        }
      }
  )
}

function Get-Sha256Text([string]$Value) {
  $bytes = [Text.Encoding]::UTF8.GetBytes($Value)
  $hash = [Security.Cryptography.SHA256]::Create().ComputeHash($bytes)
  "sha256:" + (($hash | ForEach-Object { $_.ToString("x2") }) -join "")
}

function Assert-ReplaceOnce([string]$Source, [string]$Needle, [string]$Replacement, [string]$Label) {
  $first = $Source.IndexOf($Needle, [StringComparison]::Ordinal)
  if ($first -lt 0 -or $Source.IndexOf($Needle, $first + $Needle.Length, [StringComparison]::Ordinal) -ge 0) {
    throw "base-worker-sentinel-mismatch:$Label"
  }
  $Source.Substring(0, $first) + $Replacement + $Source.Substring($first + $Needle.Length)
}

$resolvedPresentation = (Resolve-Path -LiteralPath $Presentation).Path
$resolvedWorker = (Resolve-Path -LiteralPath $BaseWorkerPath).Path
$resolvedOutput = [IO.Path]::GetFullPath($OutputDirectory)
if (Test-Path -LiteralPath $resolvedOutput) {
  throw "output-directory-must-be-fresh:$resolvedOutput"
}
[IO.Directory]::CreateDirectory($resolvedOutput) | Out-Null
$workerSourceDirectory = Join-Path $resolvedOutput "worker-source"
[IO.Directory]::CreateDirectory($workerSourceDirectory) | Out-Null
$generatedWorker = Join-Path $workerSourceDirectory "qa-powerpoint-owned-delta.generated.ps1"
$ownedPidMarker = Join-Path $resolvedOutput ".owned-powerpoint-launch-pid"

$baselineProcesses = @(Get-PowerPointProcessCensus)
$baselineProcessIds = @($baselineProcesses | ForEach-Object { [int]$_.processId })
$baselineSurfaceBefore = @(Get-BaselineSurface $baselineProcessIds)
$baselineProcessJsonBefore = $baselineProcesses | ConvertTo-Json -Depth 5 -Compress
$baselineSurfaceJsonBefore = $baselineSurfaceBefore | ConvertTo-Json -Depth 5 -Compress
$baselineSurfaceDigestBefore = Get-Sha256Text $baselineSurfaceJsonBefore

$source = [IO.File]::ReadAllText($resolvedWorker)
$source = Assert-ReplaceOnce $source @'
$openingProcessCensus = @(Get-PowerPointProcessCensus)
$preexisting = @(Get-Process -Name "POWERPNT" -ErrorAction SilentlyContinue)
if ($preexisting.Count -gt 0) {
  throw "PowerPoint Desktop must not already be running for isolated COM QA."
}
'@ @'
$openingProcessCensus = @(Get-PowerPointProcessCensus)
$baselineProcessIds = @($openingProcessCensus | ForEach-Object { [int]$_.ProcessId })
'@ "opening-census"
$source = Assert-ReplaceOnce $source @'
  $unexpectedProcesses = @($Processes | Where-Object { [int]$_.ProcessId -ne $ExpectedProcessId })
  $unexpectedWindows = @($Windows | Where-Object { [int]$_.ProcessId -ne $ExpectedProcessId })
'@ @'
  $unexpectedProcesses = @($Processes | Where-Object {
    [int]$_.ProcessId -ne $ExpectedProcessId -and [int]$_.ProcessId -notin $baselineProcessIds
  })
  $unexpectedWindows = @($Windows | Where-Object {
    [int]$_.ProcessId -ne $ExpectedProcessId -and [int]$_.ProcessId -notin $baselineProcessIds
  })
'@ "observer-baseline-filter"
$source = Assert-ReplaceOnce $source @'
  $launched = Start-Process -FilePath $powerPointPath -ArgumentList "/safe" -WindowStyle Hidden -PassThru
'@ @'
  $ownedInitialPresentation = (Resolve-Path -LiteralPath $Presentation[0]).Path
  $preopenedPresentation = $null
  $launched = Start-Process -FilePath $powerPointPath -ArgumentList @("/x", "`"$ownedInitialPresentation`"") -WindowStyle Hidden -PassThru
  Set-Content -LiteralPath (Join-Path $resolvedOutput ".owned-powerpoint-launch-pid") -Value ([string]$launched.Id) -Encoding ascii
'@ "force-new-instance"
$source = Assert-ReplaceOnce $source @'
      if ($candidateIdentity.ProcessId -ne [int]$launched.Id) {
        [Runtime.InteropServices.Marshal]::FinalReleaseComObject($candidate) | Out-Null
        $candidate = $null
        throw "PowerPoint ROT application HWND belongs to a process other than the launched process."
      }
'@ @'
      if ($candidateIdentity.ProcessId -ne [int]$launched.Id) {
        [Runtime.InteropServices.Marshal]::FinalReleaseComObject($candidate) | Out-Null
        $candidate = $null
        $boundPresentation = $null
        $boundApplication = $null
        try {
          $boundPresentation = [Runtime.InteropServices.Marshal]::BindToMoniker($ownedInitialPresentation)
          $boundFullName = [System.IO.Path]::GetFullPath([string]$boundPresentation.FullName)
          if ($boundFullName -ne $ownedInitialPresentation) {
            throw "PowerPoint file moniker resolved a different presentation."
          }
          $boundApplication = $boundPresentation.Application
          $boundIdentity = Get-PowerPointApplicationIdentity $boundApplication
          if ($null -eq $boundIdentity -or $boundIdentity.ProcessId -ne [int]$launched.Id) {
            throw "PowerPoint file moniker belongs to a process other than the launched process."
          }
          $application = $boundApplication
          $applicationIdentity = $boundIdentity
          $preopenedPresentation = $boundPresentation
          $boundApplication = $null
          $boundPresentation = $null
          $attachedAt = Get-Date
          $activationMethod = "safe-mode-file-moniker"
          break
        } catch {
          if ($null -ne $boundApplication) {
            [Runtime.InteropServices.Marshal]::FinalReleaseComObject($boundApplication) | Out-Null
          }
          if ($null -ne $boundPresentation) {
            [Runtime.InteropServices.Marshal]::FinalReleaseComObject($boundPresentation) | Out-Null
          }
        }
        continue
      }
'@ "reject-baseline-rot"
$source = Assert-ReplaceOnce $source @'
  if ($null -eq $application) {
    # Some Click-to-Run builds start /safe successfully but never publish that
'@ @'
  if ($null -eq $application) {
    if ($baselineProcessIds.Count -gt 0) {
      $ownedLaunchPid = [int]$launched.Id
      if (-not $launched.HasExited) {
        Stop-Process -Id $ownedLaunchPid -Force
        Wait-Process -Id $ownedLaunchPid -Timeout 10 -ErrorAction SilentlyContinue
      }
      throw "owned-pid-safe-mode-rot-unavailable-with-baseline"
    }
    # Some Click-to-Run builds start /safe successfully but never publish that
'@ "block-ambiguous-com-fallback"
$source = Assert-ReplaceOnce $source @'
      $firstOpenCallStartedAt = (Get-Date).ToUniversalTime().ToString("o")
      $sourceDeck = $application.Presentations.Open($resolvedInput, -1, 0, -1)
      $firstOpenCallReturnedAt = (Get-Date).ToUniversalTime().ToString("o")
'@ @'
      $firstOpenCallStartedAt = (Get-Date).ToUniversalTime().ToString("o")
      if ($presentationOrdinal -eq 0 -and $null -ne $preopenedPresentation) {
        $sourceDeck = $preopenedPresentation
        $preopenedPresentation = $null
        if ([System.IO.Path]::GetFullPath([string]$sourceDeck.FullName) -ne $resolvedInput) {
          throw "owned-file-moniker-presentation-mismatch"
        }
      } else {
        $sourceDeck = $application.Presentations.Open($resolvedInput, -1, 0, -1)
      }
      $firstOpenCallReturnedAt = (Get-Date).ToUniversalTime().ToString("o")
'@ "consume-owned-file-moniker"
$source = Assert-ReplaceOnce $source @'
      $sourceDeck.SaveCopyAs($reopenedPath, 24)
'@ @'
      $sourceDeck.SaveCopyAs($reopenedPath, 24)
      $sourceDeck.Saved = -1
'@ "close-preopened-source-without-prompt"
$source = Assert-ReplaceOnce $source @'
      $sourceShapeCounts = @(
'@ @'
      $nativeTextEdit = $null
      $nativeShapeEdit = $null
      for ($editSlideIndex = 1; $editSlideIndex -le $sourceDeck.Slides.Count; $editSlideIndex += 1) {
        $editShapes = $sourceDeck.Slides.Item($editSlideIndex).Shapes
        try {
          for ($editShapeIndex = 1; $editShapeIndex -le $editShapes.Count; $editShapeIndex += 1) {
            $editShape = $editShapes.Item($editShapeIndex)
            try {
              $editName = [string]$editShape.Name
              if ($null -eq $nativeTextEdit -and $editName -eq "Presenton hybrid text s5.statement.headline") {
                if ([bool]$editShape.HasTextFrame -and [bool]$editShape.TextFrame2.HasText) {
                  $textRange = $editShape.TextFrame2.TextRange
                  try {
                    $beforeText = [string]$textRange.Text
                    $beforeFontSize = [double]$textRange.Font.Size
                    $beforeFontName = [string]$textRange.Font.Name
                    $beforeFontNameFarEast = [string]$textRange.Font.NameFarEast
                    $beforeSpaceWithin = [double]$textRange.ParagraphFormat.SpaceWithin
                    $beforeLineRuleWithin = [int]$textRange.ParagraphFormat.LineRuleWithin
                    $afterText = $beforeText.Replace(", 복합 효과만 ", ",`r복합 효과만`v") + " [PP16 native edit]"
                    if (
                      -not $afterText.Contains("`r") -or
                      -not $afterText.Contains("`v")
                    ) {
                      throw "powerpoint-native-text-edit-break-anchor-not-found"
                    }
                    $textRange.Text = $afterText
                    $textRange.Font.Size = $beforeFontSize
                    $textRange.Font.Name = $beforeFontName
                    $textRange.Font.NameFarEast = $beforeFontNameFarEast
                    $textRange.ParagraphFormat.LineRuleWithin = $beforeLineRuleWithin
                    $textRange.ParagraphFormat.SpaceWithin = $beforeSpaceWithin
                    $nativeTextEdit = [PSCustomObject]@{
                      slideNumber = $editSlideIndex
                      shapeName = $editName
                      qid = $editName.Substring("Presenton hybrid text ".Length)
                      beforeText = $beforeText
                      afterText = $afterText
                      beforeParagraphBreakCount = [regex]::Matches($beforeText, "`r").Count
                      afterParagraphBreakCount = [regex]::Matches($afterText, "`r").Count
                      beforeSoftBreakCount = [regex]::Matches($beforeText, "`v").Count
                      afterSoftBreakCount = [regex]::Matches($afterText, "`v").Count
                      beforeFontSizePt = $beforeFontSize
                      afterFontSizePt = [double]$textRange.Font.Size
                      beforeFontName = $beforeFontName
                      afterFontName = [string]$textRange.Font.Name
                      beforeFontNameFarEast = $beforeFontNameFarEast
                      afterFontNameFarEast = [string]$textRange.Font.NameFarEast
                      beforeSpaceWithin = $beforeSpaceWithin
                      afterSpaceWithin = [double]$textRange.ParagraphFormat.SpaceWithin
                      beforeLineRuleWithin = $beforeLineRuleWithin
                      afterLineRuleWithin = [int]$textRange.ParagraphFormat.LineRuleWithin
                    }
                  } finally {
                    [Runtime.InteropServices.Marshal]::FinalReleaseComObject($textRange) | Out-Null
                  }
                }
              }
              if ($null -eq $nativeShapeEdit -and $editName -eq "Presenton hybrid shape cover.hero_field") {
                $beforeLeft = [double]$editShape.Left
                $beforeWidth = [double]$editShape.Width
                $editShape.Left = $beforeLeft + 6.0
                $editShape.Width = $beforeWidth + 6.0
                $nativeShapeEdit = [PSCustomObject]@{
                  slideNumber = $editSlideIndex
                  shapeName = $editName
                  qid = $editName.Substring("Presenton hybrid shape ".Length)
                  beforeLeftPt = $beforeLeft
                  afterLeftPt = [double]$editShape.Left
                  beforeWidthPt = $beforeWidth
                  afterWidthPt = [double]$editShape.Width
                }
              }
            } finally {
              [Runtime.InteropServices.Marshal]::FinalReleaseComObject($editShape) | Out-Null
            }
          }
        } finally {
          [Runtime.InteropServices.Marshal]::FinalReleaseComObject($editShapes) | Out-Null
        }
      }
      if ($null -eq $nativeTextEdit) { throw "powerpoint-native-text-edit-owner-not-found" }
      if ($null -eq $nativeShapeEdit) { throw "powerpoint-native-shape-edit-owner-not-found" }
      [PSCustomObject]@{
        schemaVersion = "authored-editable-hybrid-powerpoint-native-edits/v1"
        presentationId = $PresentationId
        presentationRevision = $PresentationRevision
        hybridExportAttemptId = $currentExportAttemptId
        nativeTextEdit = $nativeTextEdit
        nativeShapeEdit = $nativeShapeEdit
      } | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath (
        Join-Path $resolvedOutput "native-edit-observation.json"
      ) -Encoding utf8
      $sourceShapeCounts = @(
'@ "native-text-shape-edit"
$source = Assert-ReplaceOnce $source @'
$shutdownDeadline = (Get-Date).AddSeconds(15)
do {
  $finalProcessCensus = @(Get-PowerPointProcessCensus)
  if ($finalProcessCensus.Count -eq 0) { break }
  Start-Sleep -Milliseconds 250
} while ((Get-Date) -lt $shutdownDeadline)
if ($finalProcessCensus.Count -ne 0) {
  throw "PowerPoint Desktop process census found a survivor after COM QA."
}
'@ @'
$forcedOwnedProcessCleanup = $false
$shutdownDeadline = (Get-Date).AddSeconds(15)
do {
  $finalProcessCensus = @(Get-PowerPointProcessCensus)
  $unexpectedFinalProcessCensus = @($finalProcessCensus | Where-Object {
    [int]$_.ProcessId -notin $baselineProcessIds
  })
  if ($unexpectedFinalProcessCensus.Count -eq 0) { break }
  Start-Sleep -Milliseconds 250
} while ((Get-Date) -lt $shutdownDeadline)
if ($unexpectedFinalProcessCensus.Count -ne 0) {
  $onlyOwnedLaunchRemains =
    $unexpectedFinalProcessCensus.Count -eq 1 -and
    [int]$unexpectedFinalProcessCensus[0].ProcessId -eq [int]$launched.Id -and
    [int]$launched.Id -notin $baselineProcessIds
  if ($onlyOwnedLaunchRemains) {
    Wait-Process -Id ([int]$launched.Id) -Timeout 30 -ErrorAction SilentlyContinue
    $finalProcessCensus = @(Get-PowerPointProcessCensus)
    $unexpectedFinalProcessCensus = @($finalProcessCensus | Where-Object {
      [int]$_.ProcessId -notin $baselineProcessIds
    })
  }
  if (
    $unexpectedFinalProcessCensus.Count -eq 1 -and
    [int]$unexpectedFinalProcessCensus[0].ProcessId -eq [int]$launched.Id -and
    [int]$launched.Id -notin $baselineProcessIds
  ) {
    Stop-Process -Id ([int]$launched.Id) -Force
    Wait-Process -Id ([int]$launched.Id) -Timeout 10 -ErrorAction SilentlyContinue
    $forcedOwnedProcessCleanup = $true
    $finalProcessCensus = @(Get-PowerPointProcessCensus)
    $unexpectedFinalProcessCensus = @($finalProcessCensus | Where-Object {
      [int]$_.ProcessId -notin $baselineProcessIds
    })
  }
}
if ($unexpectedFinalProcessCensus.Count -ne 0) {
  throw "PowerPoint Desktop owned/unexpected process census found a survivor after COM QA."
}
$report | Add-Member -NotePropertyName ForcedOwnedProcessCleanup `
  -NotePropertyValue ([bool]$forcedOwnedProcessCleanup)
'@ "final-owned-census"
[IO.File]::WriteAllText($generatedWorker, $source, [Text.UTF8Encoding]::new($false))

$workerError = $null
try {
  & $generatedWorker `
    -Presentation @($resolvedPresentation) `
    -OutputDirectory $resolvedOutput `
    -PresentationId $PresentationId `
    -PresentationRevision $PresentationRevision `
    -ExportMode @("hybrid") `
    -ExportAttemptId @($HybridExportAttemptId)
} catch {
  $workerError = $_
}

$initialOwnedLaunchProcessIds = @()
if (Test-Path -LiteralPath $ownedPidMarker -PathType Leaf) {
  $ownedPidText = (Get-Content -LiteralPath $ownedPidMarker -Raw).Trim()
  if ($ownedPidText -match '^[1-9][0-9]*$') {
    $initialOwnedLaunchProcessIds = @([int]$ownedPidText)
  }
}

$workerReportPath = Join-Path $resolvedOutput "powerpoint-desktop-validation.json"
$workerReport = if (Test-Path -LiteralPath $workerReportPath -PathType Leaf) {
  Get-Content -LiteralPath $workerReportPath -Raw | ConvertFrom-Json
} else { $null }
$ownedProcessIds = @($initialOwnedLaunchProcessIds)
$discardedInitialLaunchesVerified = $true
if (
  $null -ne $workerReport -and
  [int]$workerReport.LaunchedProcessId -gt 0 -and
  [int]$workerReport.LaunchedProcessId -eq [int]$workerReport.AttachedApplicationProcessId -and
  [int]$workerReport.LaunchedProcessId -notin $baselineProcessIds
) {
  $reportedOwnedPid = [int]$workerReport.LaunchedProcessId
  $replacedInitialIds = @($initialOwnedLaunchProcessIds | Where-Object {
    $_ -ne $reportedOwnedPid
  })
  foreach ($initialPid in $replacedInitialIds) {
    $verifiedDiscard = @($workerReport.DiscardedObserverEpochs | Where-Object {
      [int]$_.ExpectedProcessId -eq $initialPid -and
      [bool]$_.CleanupConfirmed -eq $true
    })
    if ($verifiedDiscard.Count -ne 1) {
      $discardedInitialLaunchesVerified = $false
    }
  }
  if ($discardedInitialLaunchesVerified) {
    $ownedProcessIds = @($reportedOwnedPid)
  }
}

$cleanupProcessIds = @(
  $initialOwnedLaunchProcessIds + $ownedProcessIds | Sort-Object -Unique
)
foreach ($ownedPid in $cleanupProcessIds) {
  if ($ownedPid -in $baselineProcessIds) { throw "owned-pid-overlaps-baseline:$ownedPid" }
  $ownedProcess = Get-Process -Id $ownedPid -ErrorAction SilentlyContinue
  if ($null -ne $ownedProcess) {
    Stop-Process -Id $ownedPid -Force
    Wait-Process -Id $ownedPid -Timeout 10 -ErrorAction SilentlyContinue
  }
}

$finalProcesses = @(Get-PowerPointProcessCensus)
$finalProcessIds = @($finalProcesses | ForEach-Object { [int]$_.processId })
$baselineProcessesAfter = @($finalProcesses | Where-Object { [int]$_.processId -in $baselineProcessIds })
$baselineSurfaceAfter = @(Get-BaselineSurface $baselineProcessIds)
$baselineProcessJsonAfter = $baselineProcessesAfter | ConvertTo-Json -Depth 5 -Compress
$baselineSurfaceJsonAfter = $baselineSurfaceAfter | ConvertTo-Json -Depth 5 -Compress
$baselineSurfaceDigestAfter = Get-Sha256Text $baselineSurfaceJsonAfter
$baselineProcessIdentityPreserved = $baselineProcessJsonBefore -eq $baselineProcessJsonAfter
$baselineSurfacePreserved = $baselineSurfaceJsonBefore -eq $baselineSurfaceJsonAfter
$unexpectedSurvivorIds = @($finalProcessIds | Where-Object { $_ -notin $baselineProcessIds })
$ownedSurvivorIds = @($finalProcessIds | Where-Object { $_ -in $ownedProcessIds })
$bindingExact =
  $null -ne $workerReport -and
  $discardedInitialLaunchesVerified -and
  $ownedProcessIds.Count -eq 1 -and
  [int]$workerReport.LaunchedProcessId -eq $ownedProcessIds[0] -and
  [int]$workerReport.AttachedApplicationProcessId -eq $ownedProcessIds[0]
$cleanupConfirmed = $unexpectedSurvivorIds.Count -eq 0 -and $ownedSurvivorIds.Count -eq 0
$baselinePreserved = $baselineProcessIdentityPreserved -and $baselineSurfacePreserved

$delta = [PSCustomObject]@{
  schemaVersion = "authored-powerpoint-owned-process-delta/v1"
  authorityDomain = "actual_product"
  presentationId = $PresentationId
  presentationRevision = $PresentationRevision
  hybridExportAttemptId = $HybridExportAttemptId
  forcedNewInstanceSwitch = "/x"
  baselineBindingAttempted = $false
  ownedApplicationBindingExact = [bool]$bindingExact
  initialOwnedLaunchProcessIds = $initialOwnedLaunchProcessIds
  discardedInitialLaunchesVerified = [bool]$discardedInitialLaunchesVerified
  baselineProcesses = $baselineProcesses
  baselineVisiblePresentationWindowsBefore = $baselineSurfaceBefore
  baselineVisiblePresentationWindowsAfter = $baselineSurfaceAfter
  baselinePresentationSurfaceDigestBefore = $baselineSurfaceDigestBefore
  baselinePresentationSurfaceDigestAfter = $baselineSurfaceDigestAfter
  baselineProcessIdentityPreserved = [bool]$baselineProcessIdentityPreserved
  baselinePresentationWindowsPreserved = [bool]$baselineSurfacePreserved
  unrelatedBaselinePreserved = [bool]$baselinePreserved
  unrelatedBaselineTerminatedByWorker = if ($baselinePreserved) { $false } else { $null }
  ownedProcessIds = $ownedProcessIds
  adoptedProcessIds = @()
  finalProcesses = $finalProcesses
  unexpectedPowerPointSurvivorIds = $unexpectedSurvivorIds
  unexpectedPowerPointSurvivorCount = $unexpectedSurvivorIds.Count
  ownedPowerPointSurvivorIds = $ownedSurvivorIds
  ownedPowerPointSurvivorCount = $ownedSurvivorIds.Count
  cleanupConfirmed = [bool]$cleanupConfirmed
  workerSucceeded = $null -eq $workerError
  observedAt = (Get-Date).ToUniversalTime().ToString("o")
}
$delta | ConvertTo-Json -Depth 10 | Set-Content -LiteralPath (
  Join-Path $resolvedOutput "powerpoint-owned-process-delta.json"
) -Encoding utf8

if ($null -ne $workerError) { throw $workerError }
if (-not $bindingExact) { throw "owned-powerpoint-com-binding-not-exact" }
if (-not $baselinePreserved) { throw "baseline-powerpoint-process-or-presentation-window-changed" }
if (-not $cleanupConfirmed) { throw "owned-or-unexpected-powerpoint-survivor" }

$delta
