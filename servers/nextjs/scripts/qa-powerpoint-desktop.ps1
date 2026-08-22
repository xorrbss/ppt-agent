param(
  [Parameter(Mandatory = $true)]
  [string[]]$Presentation,
  [Parameter(Mandatory = $true)]
  [string]$OutputDirectory
)

$ErrorActionPreference = "Stop"
$powerPointPath = "C:\Program Files\Microsoft Office\root\Office16\POWERPNT.EXE"
if (-not (Test-Path -LiteralPath $powerPointPath -PathType Leaf)) {
  throw "PowerPoint Desktop executable was not found: $powerPointPath"
}
$preexisting = @(Get-Process -Name "POWERPNT" -ErrorAction SilentlyContinue)
if ($preexisting.Count -gt 0) {
  throw "PowerPoint Desktop must not already be running for isolated COM QA."
}

$resolvedOutput = [System.IO.Path]::GetFullPath($OutputDirectory)
[System.IO.Directory]::CreateDirectory($resolvedOutput) | Out-Null
$launched = Start-Process -FilePath $powerPointPath -ArgumentList "/safe" -WindowStyle Hidden -PassThru
$application = $null
$attachedAt = $null
$activationMethod = "safe-mode-running-object-table"
try {
  for ($attempt = 0; $attempt -lt 40 -and $null -eq $application; $attempt += 1) {
    Start-Sleep -Milliseconds 500
    try {
      $application = [Runtime.InteropServices.Marshal]::GetActiveObject(
        "PowerPoint.Application"
      )
      $attachedAt = Get-Date
    } catch {
      # PowerPoint registers its automation object after startup completes.
    }
  }
  if ($null -eq $application) {
    # Some Click-to-Run builds start /safe successfully but never publish that
    # process to the Running Object Table. Shut down only our exact process and
    # use COM activation, which still launches a fresh isolated POWERPNT.EXE.
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

  $results = @()
  foreach ($inputPath in $Presentation) {
    $resolvedInput = (Resolve-Path -LiteralPath $inputPath).Path
    $originalName = [System.IO.Path]::GetFileNameWithoutExtension($resolvedInput)
    $shortStem = if ($originalName.Length -gt 40) {
      $originalName.Substring(0, 40)
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
      # ReadOnly=-1, Untitled=0, WithWindow=0.
      $sourceDeck = $application.Presentations.Open($resolvedInput, -1, 0, 0)
      $fonts = @()
      for ($index = 1; $index -le $sourceDeck.Fonts.Count; $index += 1) {
        $font = $sourceDeck.Fonts.Item($index)
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
      }
      $sourceShapeCounts = @(
        for ($slideIndex = 1; $slideIndex -le $sourceDeck.Slides.Count; $slideIndex += 1) {
          [int]$sourceDeck.Slides.Item($slideIndex).Shapes.Count
        }
      )
      # ppSaveAsOpenXMLPresentation=24.  Save a copy, close the source, and
      # inspect only the reopened copy so render/readback evidence cannot be
      # satisfied by the initial in-memory representation.
      $sourceDeck.SaveCopyAs($reopenedPath, 24)
      $sourceDeck.Close()
      [Runtime.InteropServices.Marshal]::FinalReleaseComObject($sourceDeck) | Out-Null
      $sourceDeck = $null
      $deck = $application.Presentations.Open($reopenedPath, -1, 0, 0)
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
          try {
            $hasText = [bool]$shape.HasTextFrame -and [bool]$shape.TextFrame2.HasText
            $hasTable = [bool]$shape.HasTable
            $hasChart = [bool]$shape.HasChart
            $shapeName = ([string]$shape.Name).ToLowerInvariant()
            $isBackplate = $shapeName.Contains("presenton hybrid backplate")
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
            } elseif (-not $isCanvasBackground) {
              $nativeZOrderPositions += $zOrderPosition
            }
            if ($hasText) { $nativeText += 1 }
            if ($hasTable) { $nativeTable += 1 }
            if ($hasChart) { $nativeChart += 1 }
            if (-not $isBackplate -and $shapeType -in 11, 13) {
              $nativeImage += 1
            } elseif (-not $isBackplate -and -not $hasText -and -not $hasTable -and -not $hasChart -and $shapeType -notin 11, 13) {
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
            $shapeReadback += [PSCustomObject]@{
              SlideNumber = $slideIndex
              Name = [string]$shape.Name
              Type = $shapeType
              ZOrderPosition = $zOrderPosition
              IsBackplate = $isBackplate
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
            [Runtime.InteropServices.Marshal]::FinalReleaseComObject($shape) | Out-Null
          }
        }
        [Runtime.InteropServices.Marshal]::FinalReleaseComObject($shapes) | Out-Null
      }
      $deck.Export($renderDirectory, "PNG", 1280, 720)
      $pngs = @(
        Get-ChildItem -LiteralPath $renderDirectory -Filter "*.PNG" -File |
          Sort-Object @{ Expression = {
            if ($_.BaseName -match "(\d+)$") {
              return [int]$Matches[1]
            }
            return [int]::MaxValue
          } }, Name
      )
      $readbackPassed =
        $sourceShapeCounts.Count -eq $reopenedShapeCounts.Count -and
        (@(Compare-Object $sourceShapeCounts $reopenedShapeCounts -SyncWindow 0).Count -eq 0) -and
        $pngs.Count -eq [int]$deck.Slides.Count -and
        $typographyIssuesGt5Pt -eq 0 -and
        $typographyIssuesGt10Pt -eq 0 -and
        $inspectionFailures -eq 0
      $results += [PSCustomObject]@{
        Path = $resolvedInput
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
        RepairWarningObserved = $false
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

  [PSCustomObject]@{
    PowerPointVersion = [string]$application.Version
    LaunchedProcessId = [int]$launched.Id
    ActivationMethod = $activationMethod
    AttachedAt = $attachedAt.ToString("o")
    Presentations = $results
  } | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath (
    Join-Path $resolvedOutput "powerpoint-desktop-validation.json"
  ) -Encoding utf8
} finally {
  if ($null -ne $application) {
    try {
      $application.Quit()
    } catch {
    }
    [Runtime.InteropServices.Marshal]::FinalReleaseComObject($application) |
      Out-Null
  }
  if ($null -ne $launched) {
    try {
      $launched.WaitForExit(10000) | Out-Null
    } catch {
    }
  }
}
