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
    $name = [System.IO.Path]::GetFileNameWithoutExtension($resolvedInput)
    $renderDirectory = Join-Path $resolvedOutput $name
    [System.IO.Directory]::CreateDirectory($renderDirectory) | Out-Null
    $deck = $null
    try {
      # ReadOnly=-1, Untitled=0, WithWindow=0.
      $deck = $application.Presentations.Open($resolvedInput, -1, 0, 0)
      $fonts = @()
      for ($index = 1; $index -le $deck.Fonts.Count; $index += 1) {
        $font = $deck.Fonts.Item($index)
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
      $deck.Export($renderDirectory, "PNG", 1280, 720)
      $results += [PSCustomObject]@{
        Path = $resolvedInput
        OpenedReadOnly = [bool]$deck.ReadOnly
        SlideCount = [int]$deck.Slides.Count
        FontCount = [int]$deck.Fonts.Count
        Fonts = $fonts
        ExportedPngs = @(
          Get-ChildItem -LiteralPath $renderDirectory -Filter "*.PNG" -File
        ).Count
        RepairWarningObserved = $false
      }
    } finally {
      if ($null -ne $deck) {
        $deck.Close()
        [Runtime.InteropServices.Marshal]::FinalReleaseComObject($deck) | Out-Null
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
