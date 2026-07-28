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

$resolvedOutput = [System.IO.Path]::GetFullPath($OutputDirectory)
[System.IO.Directory]::CreateDirectory($resolvedOutput) | Out-Null
$launched = Start-Process -FilePath $powerPointPath -ArgumentList "/safe" -WindowStyle Hidden -PassThru
$application = $null
$attachedAt = $null
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
    throw "The isolated PowerPoint instance did not register for COM automation."
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
