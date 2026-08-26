param(
  [Parameter(Mandatory = $true)]
  [string[]]$Presentation,
  [Parameter(Mandatory = $true)]
  [string]$OutputDirectory,
  [string]$PresentationId,
  [int]$PresentationRevision = -1,
  [ValidateSet("fidelity", "hybrid")]
  [string[]]$ExportMode = @(),
  [string[]]$ExportAttemptId = @(),
  [string]$ExpectedOwnershipManifest
)

$ErrorActionPreference = "Stop"
$semanticOwnershipPrefix = "presenton-semantic-raster-ownership-v1:"
$authoredNativeOwnershipPrefix = "authored-vnext:"
$authoredRasterIslandOwnershipPrefix = "authored-raster-island:"
$nativeDetailMaxTableRows = 64
$nativeDetailMaxTableColumns = 32
$nativeDetailMaxTableCells = 512
$nativeDetailMaxCellTextLength = 16384
$nativeDetailMaxChartSeries = 64
$nativeDetailMaxChartPoints = 512
$nativeDetailMaxChartLiteralTextLength = 4096

function Get-Sha256Text([string]$Value) {
  $bytes = [Text.Encoding]::UTF8.GetBytes($Value)
  $hash = [Security.Cryptography.SHA256]::Create().ComputeHash($bytes)
  "sha256:" + (($hash | ForEach-Object { $_.ToString("x2") }) -join "")
}

function Get-PowerPointModuleFileIdentity([string]$Path) {
  if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
    throw "PowerPoint module was not found: $Path"
  }
  $resolvedPath = [System.IO.Path]::GetFullPath(
    (Resolve-Path -LiteralPath $Path).Path
  )
  $fileVersion = [Diagnostics.FileVersionInfo]::GetVersionInfo(
    $resolvedPath
  ).FileVersion
  if ([string]::IsNullOrWhiteSpace($fileVersion)) {
    throw "PowerPoint module FileVersion is unavailable: $resolvedPath"
  }
  [PSCustomObject]@{
    resolvedAbsolutePath = $resolvedPath
    fileVersion = [string]$fileVersion
    sha256 = "sha256:" + (
      (Get-FileHash -LiteralPath $resolvedPath -Algorithm SHA256).Hash.ToLowerInvariant()
    )
  }
}

function Write-PowerPointModuleCompatibilityPreflight(
  $PowerPointExecutable,
  $ChartModule,
  [string]$OutputDirectory
) {
  $fileVersionsExact =
    [string]$PowerPointExecutable.fileVersion -ceq [string]$ChartModule.fileVersion
  $receipt = [PSCustomObject]@{
    schemaVersion = "authored-powerpoint-module-compatibility-preflight/v1"
    authorityDomain = "actual_product"
    state = $(if ($fileVersionsExact) { "compatible" } else { "rejected" })
    failureCode = $(
      if ($fileVersionsExact) { $null } else { "office_module_version_mismatch" }
    )
    observedAt = (Get-Date).ToUniversalTime().ToString("o")
    fileVersionsExact = $fileVersionsExact
    powerPointExecutable = $PowerPointExecutable
    chartModule = $ChartModule
  }
  $receiptPath = Join-Path $OutputDirectory `
    "powerpoint-module-compatibility-preflight.json"
  if (Test-Path -LiteralPath $receiptPath) {
    throw "PowerPoint module compatibility receipt already exists: $receiptPath"
  }
  $receipt | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath $receiptPath -Encoding utf8
  $receipt
}

function Assert-PowerPointModuleCompatibilityPreflight($Receipt) {
  if (
    [string]$Receipt.schemaVersion -ne
      "authored-powerpoint-module-compatibility-preflight/v1" -or
    [string]$Receipt.authorityDomain -ne "actual_product" -or
    -not [bool]$Receipt.fileVersionsExact -or
    [string]$Receipt.state -ne "compatible" -or
    $null -ne $Receipt.failureCode
  ) {
    throw [InvalidOperationException]::new("office_module_version_mismatch")
  }
}

function ConvertTo-CanonicalLayerJson($Value) {
  if ($null -eq $Value) { return "null" }
  if ($Value -is [string] -or $Value -is [char]) {
    return ([string]$Value | ConvertTo-Json -Compress)
  }
  if ($Value -is [bool]) { return $(if ($Value) { "true" } else { "false" }) }
  if (
    $Value -is [byte] -or $Value -is [sbyte] -or
    $Value -is [int16] -or $Value -is [uint16] -or
    $Value -is [int32] -or $Value -is [uint32] -or
    $Value -is [int64] -or $Value -is [uint64] -or
    $Value -is [decimal]
  ) {
    return ([Convert]::ToString($Value, [Globalization.CultureInfo]::InvariantCulture))
  }
  if ($Value -is [single] -or $Value -is [double]) {
    $number = [double]$Value
    if ([double]::IsNaN($number) -or [double]::IsInfinity($number)) {
      throw "Canonical JSON cannot contain a non-finite number."
    }
    return $number.ToString("R", [Globalization.CultureInfo]::InvariantCulture)
  }
  if ($Value -is [Collections.IDictionary]) {
    [string[]]$names = @($Value.Keys | ForEach-Object { [string]$_ })
    [Array]::Sort($names, [StringComparer]::Ordinal)
    return "{" + (($names | ForEach-Object {
      ($_ | ConvertTo-Json -Compress) + ":" + (ConvertTo-CanonicalLayerJson $Value[$_])
    }) -join ",") + "}"
  }
  if ($Value -is [Collections.IEnumerable]) {
    return "[" + ((@($Value) | ForEach-Object { ConvertTo-CanonicalLayerJson $_ }) -join ",") + "]"
  }
  [string[]]$properties = @($Value.PSObject.Properties.Name)
  [Array]::Sort($properties, [StringComparer]::Ordinal)
  return "{" + (($properties | ForEach-Object {
    ($_ | ConvertTo-Json -Compress) + ":" +
      (ConvertTo-CanonicalLayerJson $Value.PSObject.Properties[$_].Value)
  }) -join ",") + "}"
}

Add-Type @'
using System;
using System.Collections.Generic;
using System.Runtime.InteropServices;
using System.Text;
public static class PresentonPowerPointApplicationWindowIdentity {
  private delegate bool EnumWindowsProc(IntPtr hwnd, IntPtr lParam);
  [DllImport("user32.dll")]
  private static extern uint GetWindowThreadProcessId(IntPtr hwnd, out uint processId);
  [DllImport("user32.dll")] private static extern bool EnumWindows(EnumWindowsProc callback, IntPtr lParam);
  [DllImport("user32.dll")] private static extern bool IsWindowVisible(IntPtr hwnd);
  [DllImport("user32.dll", CharSet=CharSet.Unicode)] private static extern int GetClassName(IntPtr hwnd, StringBuilder value, int count);
  public static int ProcessId(long hwnd) {
    if (hwnd == 0) return 0;
    uint processId;
    GetWindowThreadProcessId(new IntPtr(hwnd), out processId);
    return (int)processId;
  }
  public static long[] VisiblePptFrameHwndsForProcess(int expectedProcessId) {
    var result = new List<long>();
    EnumWindows((hwnd, state) => {
      uint processId; GetWindowThreadProcessId(hwnd, out processId);
      if ((int)processId != expectedProcessId || !IsWindowVisible(hwnd)) return true;
      var className = new StringBuilder(512); GetClassName(hwnd, className, className.Capacity);
      if (String.Equals(className.ToString(), "PPTFrameClass", StringComparison.Ordinal)) result.Add(hwnd.ToInt64());
      return true;
    }, IntPtr.Zero);
    return result.ToArray();
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

function Get-PowerPointVisibleFrameIdentity([int]$ExpectedProcessId) {
  $hwnds = @([PresentonPowerPointApplicationWindowIdentity]::VisiblePptFrameHwndsForProcess($ExpectedProcessId))
  if ($hwnds.Count -eq 0) { return $null }
  if ($hwnds.Count -ne 1) {
    throw "PowerPoint expected process has more than one visible PPTFrameClass window."
  }
  $processId = [PresentonPowerPointApplicationWindowIdentity]::ProcessId([int64]$hwnds[0])
  if ($processId -ne $ExpectedProcessId) {
    throw "PowerPoint visible PPTFrameClass HWND/PID binding changed during enumeration."
  }
  [PSCustomObject]@{ Hwnd = [int64]$hwnds[0]; ProcessId = [int]$processId }
}

function Complete-PowerPointFallbackWindowBinding($ProvisionalIdentity, $PostOpenIdentity) {
  if (
    $null -eq $ProvisionalIdentity -or
    [int64]$ProvisionalIdentity.Hwnd -ne 0 -or
    [int]$ProvisionalIdentity.ProcessId -lt 1 -or
    $null -eq $PostOpenIdentity -or
    [int64]$PostOpenIdentity.Hwnd -eq 0 -or
    [int]$PostOpenIdentity.ProcessId -ne [int]$ProvisionalIdentity.ProcessId
  ) {
    throw "PowerPoint fallback post-open HWND/PID binding is invalid."
  }
  $PostOpenIdentity
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

function Read-AuthoredOwnershipAlternativeText(
  [string]$AlternativeText,
  [int]$ExpectedSlideNumber,
  [string]$ShapeName
) {
  $semanticOwnership = Read-SemanticOwnershipAlternativeText `
    $AlternativeText $ExpectedSlideNumber
  if ($null -ne $semanticOwnership) {
    return [PSCustomObject]@{
      SchemaVersion = $semanticOwnership.SchemaVersion
      SlideNumber = $semanticOwnership.SlideNumber
      Qids = @($semanticOwnership.Qids)
      QidSetDigest = $semanticOwnership.QidSetDigest
      Kind = $null
      SceneSlideId = $null
      CapabilityId = $null
      Disposition = "raster"
      Source = "semantic-raster-alternative-text"
    }
  }

  $prefix = $null
  $field = $null
  if ($AlternativeText.StartsWith($authoredNativeOwnershipPrefix)) {
    if ($AlternativeText.Length -gt 16384) {
      throw "Authored native ownership AlternativeText exceeds its bound."
    }
    $prefix = $authoredNativeOwnershipPrefix
    $field = "elementId"
  } elseif ($AlternativeText.StartsWith($authoredRasterIslandOwnershipPrefix)) {
    $prefix = $authoredRasterIslandOwnershipPrefix
    $field = "sourceOwnerIds"
  } else {
    return $null
  }
  try {
    $payload = $AlternativeText.Substring($prefix.Length) | ConvertFrom-Json
  } catch {
    throw "Authored ownership AlternativeText is not valid JSON."
  }
  $qids = @(if ($field -eq "elementId") {
    @($payload.elementId)
  } else {
    @($payload.sourceOwnerIds)
  })
  if (
    $qids.Count -lt 1 -or
    @($qids | Where-Object {
      $_ -isnot [string] -or $_.Length -lt 1 -or $_.Length -gt 512 -or
      $_ -ne $_.Trim() -or $_ -match '[\x00-\x1f\x7f]'
    }).Count -ne 0 -or
    @($qids | Sort-Object -Unique).Count -ne $qids.Count
  ) {
    throw "Authored ownership AlternativeText payload failed validation."
  }
  $kind = $null
  $disposition = if ($field -eq "elementId") { "native" } else { "raster" }
  if ($field -eq "elementId") {
    $basePropertyNames = @(
      "schemaVersion", "slideNumber", "slideId", "elementId", "kind",
      "capabilityId", "ownerDigest"
    )
    $advancedPropertyNames = @(
      "schemaVersion", "slideNumber", "slideId", "elementId", "kind",
      "capabilityId", "textRunMap", "textRunStyles", "autoFit",
      "advancedTextDigest", "ownerDigest"
    )
    $actualPropertyNames = @($payload.PSObject.Properties.Name)
    $hasAdvancedReceipt =
      ($actualPropertyNames | ConvertTo-Json -Compress) -eq
      ($advancedPropertyNames | ConvertTo-Json -Compress)
    $shapeNameValid = if ([string]$payload.kind -eq "chart") {
      $false
    } else {
      $ShapeName -eq
        ("Presenton hybrid {0} {1}" -f [string]$payload.kind, [string]$payload.elementId)
    }
    if (
      (
        ($actualPropertyNames | ConvertTo-Json -Compress) -ne
          ($basePropertyNames | ConvertTo-Json -Compress) -and
        -not $hasAdvancedReceipt
      ) -or
      $payload.schemaVersion -ne "authored-vnext-owner/v1" -or
      [int]$payload.slideNumber -ne $ExpectedSlideNumber -or
      $payload.slideId -notmatch '^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$' -or
      $payload.elementId -notmatch '^s\d{2}-[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$' -or
      -not @("text", "shape", "image", "table", "chart").Contains([string]$payload.kind) -or
      $payload.capabilityId -notmatch '^[A-Za-z][A-Za-z0-9._:-]{0,127}$' -or
      -not $shapeNameValid
    ) {
      throw "Authored native ownership AlternativeText payload failed validation."
    }
    if (($payload.capabilityId -eq "editable.text.advanced.v1") -ne $hasAdvancedReceipt) {
      throw "Authored advanced text ownership receipt is missing or unexpected."
    }
    $textRunMap = @()
    $textRunStyles = @()
    $autoFit = $null
    $advancedTextDigest = $null
    if ($hasAdvancedReceipt) {
      $textRunMap = @($payload.textRunMap)
      $textRunStyles = @($payload.textRunStyles)
      $autoFit = [string]$payload.autoFit
      $advancedTextDigest = [string]$payload.advancedTextDigest
      if (
        $textRunMap.Count -lt 1 -or $textRunMap.Count -gt 512 -or
        $textRunStyles.Count -ne $textRunMap.Count -or
        -not @("none", "shrink").Contains($autoFit) -or
        $advancedTextDigest -notmatch '^sha256:[0-9a-f]{64}$'
      ) {
        throw "Authored advanced text ownership receipt failed validation."
      }
      $expectedUtf16Start = 0
      $runIds = @{}
      for ($runIndex = 0; $runIndex -lt $textRunMap.Count; $runIndex += 1) {
        $identity = $textRunMap[$runIndex]
        $style = $textRunStyles[$runIndex]
        $identityProperties = @("runIndex", "utf16Start", "utf16Length", "textDigest", "runId")
        $styleProperties = @("runIndex", "runId", "fontFamily", "eastAsianTypeface", "fontSizePt", "bold", "italic", "underline", "color")
        $runId = [string]$identity.runId
        if (
          (@($identity.PSObject.Properties.Name) | ConvertTo-Json -Compress) -ne
            ($identityProperties | ConvertTo-Json -Compress) -or
          (@($style.PSObject.Properties.Name) | ConvertTo-Json -Compress) -ne
            ($styleProperties | ConvertTo-Json -Compress) -or
          [int]$identity.runIndex -ne $runIndex -or [int]$style.runIndex -ne $runIndex -or
          [int]$identity.utf16Start -ne $expectedUtf16Start -or
          [int]$identity.utf16Length -lt 0 -or
          $identity.textDigest -notmatch '^sha256:[0-9a-f]{64}$' -or
          $runId -notmatch '^[A-Za-z][A-Za-z0-9._:-]{0,127}$' -or
          $runIds.ContainsKey($runId) -or [string]$style.runId -ne $runId -or
          [string]::IsNullOrWhiteSpace([string]$style.fontFamily) -or
          [string]$style.eastAsianTypeface -ne [string]$style.fontFamily -or
          [double]$style.fontSizePt -le 0 -or [double]$style.fontSizePt -gt 400 -or
          $style.bold -isnot [bool] -or $style.italic -isnot [bool] -or
          $style.underline -isnot [bool] -or $style.color -notmatch '^#[0-9A-F]{6}$'
        ) {
          throw "Authored advanced text ownership run receipt failed validation."
        }
        $runIds[$runId] = $true
        $expectedUtf16Start += [int]$identity.utf16Length
      }
      $advancedCore = [ordered]@{
        autoFit = $autoFit
        textRunMap = @($textRunMap | ForEach-Object {
          [ordered]@{
            runId = [string]$_.runId
            runIndex = [int]$_.runIndex
            textDigest = [string]$_.textDigest
            utf16Length = [int]$_.utf16Length
            utf16Start = [int]$_.utf16Start
          }
        })
        textRunStyles = @($textRunStyles | ForEach-Object {
          [ordered]@{
            bold = [bool]$_.bold
            color = [string]$_.color
            eastAsianTypeface = [string]$_.eastAsianTypeface
            fontFamily = [string]$_.fontFamily
            fontSizePt = [double]$_.fontSizePt
            italic = [bool]$_.italic
            runId = [string]$_.runId
            runIndex = [int]$_.runIndex
            underline = [bool]$_.underline
          }
        })
      }
      if ($advancedTextDigest -ne (Get-Sha256Text ($advancedCore | ConvertTo-Json -Depth 12 -Compress))) {
        throw "Authored advanced text ownership digest is invalid."
      }
    }
    # presenton-canonical-json/v2 sorts object keys lexicographically.
    $ownerCore = if ($hasAdvancedReceipt) {
      [ordered]@{
        advancedTextDigest = $advancedTextDigest
        autoFit = $autoFit
        capabilityId = [string]$payload.capabilityId
        elementId = [string]$payload.elementId
        kind = [string]$payload.kind
        schemaVersion = [string]$payload.schemaVersion
        slideId = [string]$payload.slideId
        slideNumber = [int]$payload.slideNumber
        textRunMap = $advancedCore.textRunMap
        textRunStyles = $advancedCore.textRunStyles
      }
    } else {
      [ordered]@{
        capabilityId = [string]$payload.capabilityId
        elementId = [string]$payload.elementId
        kind = [string]$payload.kind
        schemaVersion = [string]$payload.schemaVersion
        slideId = [string]$payload.slideId
        slideNumber = [int]$payload.slideNumber
      }
    }
    $ownerCoreJson = $ownerCore | ConvertTo-Json -Compress
    if ($payload.ownerDigest -ne (Get-Sha256Text $ownerCoreJson)) {
      throw "Authored native ownership AlternativeText owner digest is invalid."
    }
    $canonicalCarrierPayload = if ($hasAdvancedReceipt) {
      [ordered]@{
        schemaVersion = [string]$payload.schemaVersion
        slideNumber = [int]$payload.slideNumber
        slideId = [string]$payload.slideId
        elementId = [string]$payload.elementId
        kind = [string]$payload.kind
        capabilityId = [string]$payload.capabilityId
        textRunMap = @($textRunMap)
        textRunStyles = @($textRunStyles)
        autoFit = $autoFit
        advancedTextDigest = $advancedTextDigest
        ownerDigest = [string]$payload.ownerDigest
      } | ConvertTo-Json -Depth 12 -Compress
    } else {
      [ordered]@{
        schemaVersion = [string]$payload.schemaVersion
        slideNumber = [int]$payload.slideNumber
        slideId = [string]$payload.slideId
        elementId = [string]$payload.elementId
        kind = [string]$payload.kind
        capabilityId = [string]$payload.capabilityId
        ownerDigest = [string]$payload.ownerDigest
      } | ConvertTo-Json -Compress
    }
    if ($AlternativeText.Substring($prefix.Length) -ne $canonicalCarrierPayload) {
      throw "Authored native ownership AlternativeText is not canonical compact JSON."
    }
    $kind = [string]$payload.kind
  }
  [PSCustomObject]@{
    SchemaVersion = if ($field -eq "elementId") {
      "authored-vnext-owner/v1"
    } else {
      "authored-raster-island-owner/v1"
    }
    SlideNumber = $ExpectedSlideNumber
    Qids = @($qids)
    QidSetDigest = Get-Sha256Text ($qids -join "`n")
    Kind = $kind
    SceneSlideId = if ($field -eq "elementId") {
      [string]$payload.slideId
    } else { $null }
    CapabilityId = if ($field -eq "elementId") {
      [string]$payload.capabilityId
    } else { $null }
    AdvancedTextAutoFit = if ($field -eq "elementId" -and $hasAdvancedReceipt) {
      $autoFit
    } else { $null }
    AdvancedTextRunMap = if ($field -eq "elementId" -and $hasAdvancedReceipt) {
      @($textRunMap)
    } else { @() }
    AdvancedTextRunStyles = if ($field -eq "elementId" -and $hasAdvancedReceipt) {
      @($textRunStyles)
    } else { @() }
    AdvancedTextDigest = if ($field -eq "elementId" -and $hasAdvancedReceipt) {
      $advancedTextDigest
    } else { $null }
    Disposition = $disposition
    Source = "alternative-text"
  }
}

function Read-BoundedNativeNameFallback(
  [string]$ShapeName,
  [int]$ExpectedSlideNumber
) {
  $match = [regex]::Match(
    $ShapeName,
    '^Presenton hybrid (text|shape|image|table|chart) (s\d{2}-[A-Za-z0-9][A-Za-z0-9._:-]{0,127})$'
  )
  if (-not $match.Success) {
    if ($ShapeName -match '^Presenton hybrid (text|shape|image|table|chart)\b') {
      throw "Authored native shape name is malformed."
    }
    return $null
  }
  $qid = $match.Groups[2].Value
  if ($qid.Substring(1, 2) -ne ("{0:d2}" -f $ExpectedSlideNumber)) {
    throw "Authored native shape name slide identity is invalid."
  }
  [PSCustomObject]@{
    SchemaVersion = "authored-vnext-owner/name-fallback-v1"
    SlideNumber = $ExpectedSlideNumber
    Qids = @($qid)
    QidSetDigest = Get-Sha256Text $qid
    Kind = $match.Groups[1].Value
    SceneSlideId = $null
    CapabilityId = $null
    Disposition = "native"
    Source = "strict-name-fallback"
  }
}

function ConvertFrom-PresentonBase32([string]$Value) {
  if ([string]::IsNullOrWhiteSpace($Value) -or $Value -notmatch '^[A-Z2-7]+$') {
    throw "Authored chart tag Base32 value is invalid."
  }
  $alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567"
  $bits = 0L
  $bitCount = 0
  $bytes = [Collections.Generic.List[byte]]::new()
  foreach ($character in $Value.ToCharArray()) {
    $index = $alphabet.IndexOf($character)
    if ($index -lt 0) { throw "Authored chart tag Base32 value is invalid." }
    $bits = ($bits -shl 5) -bor $index
    $bitCount += 5
    if ($bitCount -ge 8) {
      $bytes.Add([byte](($bits -shr ($bitCount - 8)) -band 255))
      $bitCount -= 8
      $bits = if ($bitCount -eq 0) { 0 } else { $bits -band ((1L -shl $bitCount) - 1) }
    }
  }
  if ($bitCount -gt 0 -and $bits -ne 0) {
    throw "Authored chart tag Base32 padding is invalid."
  }
  $decoded = [Text.Encoding]::UTF8.GetString($bytes.ToArray())
  if ([Convert]::ToHexString([Text.Encoding]::UTF8.GetBytes($decoded)) -ne
    [Convert]::ToHexString($bytes.ToArray())) {
    throw "Authored chart tag UTF-8 value is invalid."
  }
  $decoded
}

function Read-AuthoredChartTagMapOwnership(
  [Collections.IDictionary]$TagValues,
  [int]$ExpectedSlideNumber,
  [string]$ShapeName
) {
  $required = @(
    "PRESENTON_QID_V2", "PRESENTON_KIND_V2", "PRESENTON_OWNER_ROLE_V2"
  )
  if ($TagValues.Count -eq 0) { return $null }
  if ($TagValues.Count -ne $required.Count -or
    @($required | Where-Object { -not $TagValues.Contains($_) }).Count -ne 0) {
    throw "Authored chart tag set is not exact."
  }
  $decode = {
    param([string]$Name, [string]$Prefix)
    $value = [string]$TagValues[$Name]
    if (-not $value.StartsWith($Prefix)) {
      throw "Authored chart tag prefix is invalid."
    }
    ConvertFrom-PresentonBase32 $value.Substring($Prefix.Length)
  }
  $qid = & $decode "PRESENTON_QID_V2" "Q2_"
  $kind = & $decode "PRESENTON_KIND_V2" "K2_"
  $role = & $decode "PRESENTON_OWNER_ROLE_V2" "R2_"
  $carrier = [regex]::Match(
    $ShapeName,
    '^Presenton hybrid chart slide-3:chart:(line|pie)_01$'
  )
  if (-not $carrier.Success -or $kind -ne "chart" -or
    $role -ne "native-chart-object" -or
    $qid -notmatch '^s\d{2}-[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$' -or
    $qid.Substring(1, 2) -ne ("{0:d2}" -f $ExpectedSlideNumber)) {
    throw "Authored chart tag payload failed validation."
  }
  [PSCustomObject]@{
    SchemaVersion = "authored-vnext-owner/v1"
    SlideNumber = $ExpectedSlideNumber
    Qids = @($qid)
    QidSetDigest = Get-Sha256Text $qid
    Kind = "chart"
    SceneSlideId = $null
    CapabilityId = "editable.chart.line-pie.v1"
    Disposition = "native"
    Source = "powerpoint-shape-tags"
    ChartCarrierType = $carrier.Groups[1].Value
  }
}

function Read-PowerPointChartTagsOwnership(
  $Shape,
  [int]$ExpectedSlideNumber,
  [string]$ShapeName
) {
  if ($null -eq $Shape) { return $null }
  # Shape.Tags is also used as a redundant exact-QID carrier by the native
  # Scene infrastructure for text, image, shape, and table owners.  Only a
  # real COM chart may enter the bounded chart tag contract; every non-chart
  # keeps the existing AlternativeText/name validation path unchanged.
  if ([int]$Shape.HasChart -ne -1) { return $null }
  $tags = $null
  try {
    $tags = $Shape.Tags
    $values = [ordered]@{}
    for ($index = 1; $index -le [int]$tags.Count; $index += 1) {
      $name = [string]$tags.Name($index)
      if ($values.Contains($name)) { throw "Authored chart tag name is duplicated." }
      $values[$name] = [string]$tags.Value($index)
    }
    Read-AuthoredChartTagMapOwnership $values $ExpectedSlideNumber $ShapeName
  } finally {
    Release-PowerPointComObject $tags
  }
}

function Read-AuthoredShapeOwnership(
  [string]$AlternativeText,
  [int]$ExpectedSlideNumber,
  [string]$ShapeName,
  $Shape = $null
) {
  $tagOwnership = Read-PowerPointChartTagsOwnership `
    $Shape $ExpectedSlideNumber $ShapeName
  if ($null -ne $tagOwnership) {
    if (-not [string]::IsNullOrWhiteSpace($AlternativeText)) {
      throw "Authored chart has duplicate tag and AlternativeText owners."
    }
    return $tagOwnership
  }
  $ownership = Read-AuthoredOwnershipAlternativeText `
    $AlternativeText $ExpectedSlideNumber $ShapeName
  if ($null -ne $ownership) { return $ownership }
  Read-BoundedNativeNameFallback $ShapeName $ExpectedSlideNumber
}

function Read-ExpectedOwnershipManifest(
  [string]$ManifestPath,
  [string]$ExpectedPresentationId,
  [int]$ExpectedPresentationRevision
) {
  if ([string]::IsNullOrWhiteSpace($ManifestPath)) { return $null }
  $resolved = (Resolve-Path -LiteralPath $ManifestPath).Path
  $bytesDigest = "sha256:" + (
    Get-FileHash -Algorithm SHA256 -LiteralPath $resolved
  ).Hash.ToLowerInvariant()
  try {
    $manifest = Get-Content -Raw -LiteralPath $resolved | ConvertFrom-Json
  } catch {
    throw "Expected ownership manifest is not valid JSON."
  }
  $expectedRootProperties = @(
    "schemaVersion", "presentationId", "presentationRevision",
    "activeManifestDigest", "sceneDigest", "editableHybridProfileId",
    "expectedCount", "entries"
  )
  if (
    (@($manifest.PSObject.Properties.Name) | ConvertTo-Json -Compress) -ne
      ($expectedRootProperties | ConvertTo-Json -Compress) -or
    $manifest.schemaVersion -ne "authored-powerpoint-qid-expectation/v2" -or
    $manifest.presentationId -ne $ExpectedPresentationId -or
    [int]$manifest.presentationRevision -ne $ExpectedPresentationRevision -or
    $manifest.activeManifestDigest -notmatch '^sha256:[0-9a-f]{64}$' -or
    $manifest.sceneDigest -notmatch '^sha256:[0-9a-f]{64}$' -or
    $manifest.editableHybridProfileId -ne
      "authored-editable-hybrid-ko-en-basic-v1" -or
    [int]$manifest.expectedCount -ne 27 -or
    @($manifest.entries).Count -ne 27
  ) {
    throw "Expected ownership manifest root failed validation."
  }
  $entries = @(
    foreach ($entry in @($manifest.entries)) {
      $expectedEntryProperties = @(
        "slideNumber", "slideId", "qid", "kind", "capabilityId",
        "hybridDisposition", "advancedTextExpectation", "tableExpectation",
        "chartExpectation"
      )
      if (
        (@($entry.PSObject.Properties.Name) | ConvertTo-Json -Compress) -ne
          ($expectedEntryProperties | ConvertTo-Json -Compress) -or
        [int]$entry.slideNumber -lt 1 -or [int]$entry.slideNumber -gt 8 -or
        $entry.slideId -notmatch '^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$' -or
        $entry.qid -notmatch '^s\d{2}-[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$' -or
        $entry.qid.Substring(1, 2) -ne ("{0:d2}" -f [int]$entry.slideNumber) -or
        -not @("text", "shape", "image", "table", "chart").Contains(
          [string]$entry.kind
        ) -or
        $entry.capabilityId -notmatch '^[A-Za-z][A-Za-z0-9._:-]{0,127}$' -or
        -not @("native", "raster").Contains([string]$entry.hybridDisposition)
      ) {
        throw "Expected ownership manifest entry failed validation."
      }
      $advancedExpectation = $entry.advancedTextExpectation
      $tableExpectation = $entry.tableExpectation
      $requiresAdvanced =
        $entry.hybridDisposition -eq "native" -and
        $entry.capabilityId -eq "editable.text.advanced.v1"
      $requiresTable =
        $entry.hybridDisposition -eq "native" -and
        $entry.capabilityId -eq "editable.table.advanced.v1"
      if (($null -ne $advancedExpectation) -ne $requiresAdvanced) {
        throw "Expected ownership manifest advanced text projection is missing or unexpected."
      }
      if (($null -ne $tableExpectation) -ne $requiresTable) {
        throw "Expected ownership manifest table projection is missing or unexpected."
      }
      if ($requiresAdvanced) {
        $advancedKeys = @(
          "schemaVersion", "textRunMap", "textRunStyles", "autoFit",
          "advancedTextDigest"
        )
        $advancedProjection = [ordered]@{
          textRunMap = @($advancedExpectation.textRunMap)
          textRunStyles = @($advancedExpectation.textRunStyles)
          autoFit = [string]$advancedExpectation.autoFit
        }
        if (
          (@($advancedExpectation.PSObject.Properties.Name) | ConvertTo-Json -Compress) -ne
            ($advancedKeys | ConvertTo-Json -Compress) -or
          $advancedExpectation.schemaVersion -ne "authored-advanced-text-com-expectation/v1" -or
          -not @("none", "shrink").Contains([string]$advancedExpectation.autoFit) -or
          @($advancedExpectation.textRunMap).Count -lt 1 -or
          @($advancedExpectation.textRunMap).Count -ne @($advancedExpectation.textRunStyles).Count -or
          $advancedExpectation.advancedTextDigest -ne
            (Get-Sha256Text (ConvertTo-CanonicalLayerJson $advancedProjection))
        ) {
          throw "Expected ownership manifest advanced text projection failed validation."
        }
      }
      if ($requiresTable) {
        $tableKeys = @(
          "schemaVersion", "rowCount", "columnCount", "cells",
          "rowHeightsMicroPoint", "columnWidthsMicroPoint", "mergedCells",
          "cellStyles", "tableDataDigest", "tableExpectationDigest"
        )
        $tableProjection = [ordered]@{
          rowCount = [int]$tableExpectation.rowCount
          columnCount = [int]$tableExpectation.columnCount
          cells = @($tableExpectation.cells)
          rowHeightsMicroPoint = @($tableExpectation.rowHeightsMicroPoint)
          columnWidthsMicroPoint = @($tableExpectation.columnWidthsMicroPoint)
          mergedCells = @($tableExpectation.mergedCells)
          cellStyles = @($tableExpectation.cellStyles)
          tableDataDigest = [string]$tableExpectation.tableDataDigest
        }
        if (
          (@($tableExpectation.PSObject.Properties.Name) | ConvertTo-Json -Compress) -ne
            ($tableKeys | ConvertTo-Json -Compress) -or
          $tableExpectation.schemaVersion -ne "authored-table-com-expectation/v1" -or
          [int]$tableExpectation.rowCount -lt 1 -or
          [int]$tableExpectation.columnCount -lt 1 -or
          @($tableExpectation.cells).Count -ne [int]$tableExpectation.rowCount -or
          @($tableExpectation.rowHeightsMicroPoint).Count -ne [int]$tableExpectation.rowCount -or
          @($tableExpectation.columnWidthsMicroPoint).Count -ne [int]$tableExpectation.columnCount -or
          $tableExpectation.tableDataDigest -notmatch '^sha256:[0-9a-f]{64}$' -or
          $tableExpectation.tableExpectationDigest -ne
            (Get-Sha256Text (ConvertTo-CanonicalLayerJson $tableProjection))
        ) {
          throw "Expected ownership manifest table projection failed validation."
        }
      }
      [PSCustomObject]@{
        SlideNumber = [int]$entry.slideNumber
        SlideId = [string]$entry.slideId
        Qid = [string]$entry.qid
        Kind = [string]$entry.kind
        CapabilityId = [string]$entry.capabilityId
        HybridDisposition = [string]$entry.hybridDisposition
        AdvancedTextExpectation = $advancedExpectation
        TableExpectation = $tableExpectation
        ChartExpectation = $entry.chartExpectation
      }
    }
  )
  if (@($entries.Qid | Sort-Object -Unique).Count -ne 27) {
    throw "Expected ownership manifest contains duplicate QIDs."
  }
  [PSCustomObject]@{
    Path = $resolved
    Sha256 = $bytesDigest
    ActiveManifestDigest = [string]$manifest.activeManifestDigest
    SceneDigest = [string]$manifest.sceneDigest
    EditableHybridProfileId = [string]$manifest.editableHybridProfileId
    Entries = @($entries | Sort-Object SlideNumber, Qid)
  }
}

function Compare-ExpectedOwnership(
  [object[]]$ShapeReadback,
  [object[]]$ExpectedEntries,
  [string]$Mode
) {
  $expectedByQid = @{}
  foreach ($entry in $ExpectedEntries) { $expectedByQid[$entry.Qid] = $entry }
  $observations = @(
    foreach ($shape in $ShapeReadback) {
      foreach ($qid in @($shape.Qids)) {
        $expected = $expectedByQid[$qid]
        $expectedDisposition = if ($Mode -eq "fidelity") {
          "raster"
        } elseif ($null -ne $expected) {
          [string]$expected.HybridDisposition
        } else { $null }
        $observedKind = if ($null -ne $shape.OwnershipKind) {
          [string]$shape.OwnershipKind
        } else { $null }
        $boundKind = if ($null -ne $observedKind) {
          $observedKind
        } elseif (
          $null -ne $expected -and
          [string]$shape.OwnershipDisposition -eq "raster"
        ) {
          [string]$expected.Kind
        } else { $null }
        $nativeCarrierExact =
          $expectedDisposition -ne "native" -or
          (
            [string]$shape.OwnershipSource -eq "alternative-text" -and
            [string]$shape.OwnershipSceneSlideId -eq [string]$expected.SlideId -and
            [string]$shape.OwnershipCapabilityId -eq [string]$expected.CapabilityId
          )
        $advancedTextExact = $true
        $tableSemanticExact = $true
        if ($expectedDisposition -eq "native" -and $null -ne $expected.AdvancedTextExpectation) {
          $actualAdvanced = $shape.NativeDetail.AdvancedText
          $expectedAdvanced = $expected.AdvancedTextExpectation
          $actualAdvancedProjection = if ($null -ne $actualAdvanced) {
            [ordered]@{
              textRunMap = @($actualAdvanced.TextRunMap)
              textRunStyles = @($actualAdvanced.TextRunStyles)
              autoFit = [string]$actualAdvanced.AutoFit
            }
          } else { $null }
          $expectedAdvancedProjection = [ordered]@{
            textRunMap = @($expectedAdvanced.textRunMap)
            textRunStyles = @($expectedAdvanced.textRunStyles)
            autoFit = [string]$expectedAdvanced.autoFit
          }
          $advancedTextExact =
            $null -ne $actualAdvanced -and
            [string]$actualAdvanced.AdvancedTextDigest -eq
              [string]$expectedAdvanced.advancedTextDigest -and
            [int]$actualAdvanced.ComAutoSize -eq
              $(if ($expectedAdvanced.autoFit -eq "shrink") { 2 } else { 0 }) -and
            (ConvertTo-CanonicalLayerJson $actualAdvancedProjection) -eq
              (ConvertTo-CanonicalLayerJson $expectedAdvancedProjection)
        }
        if ($expectedDisposition -eq "native" -and $null -ne $expected.TableExpectation) {
          $actualTable = $shape.NativeDetail.Table
          $expectedTable = $expected.TableExpectation
          $expectedTableProjection = [ordered]@{
            rowCount = [int]$expectedTable.rowCount
            columnCount = [int]$expectedTable.columnCount
            cells = @($expectedTable.cells)
            rowHeightsMicroPoint = @($expectedTable.rowHeightsMicroPoint)
            columnWidthsMicroPoint = @($expectedTable.columnWidthsMicroPoint)
            mergedCells = @($expectedTable.mergedCells)
            cellStyles = @($expectedTable.cellStyles)
          }
          $tableSemanticExact = $false
          if ($null -ne $actualTable) {
            $actualTableProjection =
              Convert-PowerPointTableDetailToSemanticProjection $actualTable
            $mergedTopologyExact =
              (ConvertTo-CanonicalLayerJson @($actualTableProjection.mergedCells)) -eq
              (ConvertTo-CanonicalLayerJson @($expectedTableProjection.mergedCells))
            if ($mergedTopologyExact) {
              $actualTableProjection =
                Convert-PowerPointTableProjectionToPhysicalEdges `
                  $actualTableProjection
              $expectedTableProjection =
                Convert-PowerPointTableProjectionToPhysicalEdges `
                  $expectedTableProjection
              if ($null -ne $actualTableProjection -and
                $null -ne $expectedTableProjection) {
                $actualTableProjection =
                  Convert-PowerPointTableProjectionMergedCoverageToNull `
                    $actualTableProjection
                $expectedTableProjection =
                  Convert-PowerPointTableProjectionMergedCoverageToNull `
                    $expectedTableProjection
                $tableSemanticExact =
                  (ConvertTo-CanonicalLayerJson $actualTableProjection) -eq
                  (ConvertTo-CanonicalLayerJson $expectedTableProjection)
              }
            }
          }
        }
        [PSCustomObject]@{
          SlideNumber = [int]$shape.SlideNumber
          SceneSlideId = if ($null -ne $shape.OwnershipSceneSlideId) {
            [string]$shape.OwnershipSceneSlideId
          } else { [string]$expected.SlideId }
          Qid = [string]$qid
          Kind = $boundKind
          ObservedKind = $observedKind
          Disposition = [string]$shape.OwnershipDisposition
          OwnershipSource = [string]$shape.OwnershipSource
          CapabilityId = if ($null -ne $shape.OwnershipCapabilityId) {
            [string]$shape.OwnershipCapabilityId
          } else { [string]$expected.CapabilityId }
          ExpectedSlideNumber = if ($null -ne $expected) {
            [int]$expected.SlideNumber
          } else { $null }
          ExpectedSlideId = if ($null -ne $expected) {
            [string]$expected.SlideId
          } else { $null }
          ExpectedKind = if ($null -ne $expected) {
            [string]$expected.Kind
          } else { $null }
          ExpectedCapabilityId = if ($null -ne $expected) {
            [string]$expected.CapabilityId
          } else { $null }
          ExpectedDisposition = $expectedDisposition
          NativeCarrierExact = $nativeCarrierExact
          AdvancedTextExact = $advancedTextExact
          TableSemanticExact = $tableSemanticExact
          TupleExact =
            $null -ne $expected -and
            [int]$shape.SlideNumber -eq [int]$expected.SlideNumber -and
            $boundKind -eq [string]$expected.Kind -and
            [string]$shape.OwnershipDisposition -eq $expectedDisposition -and
            $nativeCarrierExact -and $advancedTextExact -and $tableSemanticExact
        }
      }
    }
  )
  $duplicateQids = @(
    $observations | Group-Object Qid | Where-Object Count -ne 1 |
      ForEach-Object Name | Sort-Object -Unique
  )
  $observedQids = @($observations.Qid | Sort-Object -Unique)
  $missingQids = @(
    $ExpectedEntries.Qid | Where-Object { $_ -notin $observedQids } |
      Sort-Object -Unique
  )
  $ghostQids = @(
    $observedQids | Where-Object { -not $expectedByQid.ContainsKey($_) } |
      Sort-Object -Unique
  )
  $tupleMismatches = @(
    $observations | Where-Object { -not $_.TupleExact }
  )
  [PSCustomObject]@{
    ExpectedCount = $ExpectedEntries.Count
    ObservedCount = $observations.Count
    MissingQids = $missingQids
    GhostQids = $ghostQids
    DuplicateQids = $duplicateQids
    TupleMismatches = $tupleMismatches
    Observations = @($observations | Sort-Object SlideNumber, Qid)
    Exact =
      $observations.Count -eq $ExpectedEntries.Count -and
      $missingQids.Count -eq 0 -and
      $ghostQids.Count -eq 0 -and
      $duplicateQids.Count -eq 0 -and
      $tupleMismatches.Count -eq 0
  }
}

function Release-PowerPointComObject($Value) {
  if (
    $null -ne $Value -and
    [Runtime.InteropServices.Marshal]::IsComObject($Value)
  ) {
    [Runtime.InteropServices.Marshal]::FinalReleaseComObject($Value) | Out-Null
  }
}

function Get-BoundedPowerPointCellText([string]$Value) {
  if ($null -eq $Value) { return "" }
  if ($Value.Length -gt $nativeDetailMaxCellTextLength) {
    throw "PowerPoint table cell text exceeds the native readback bound."
  }
  $Value
}

function Convert-PowerPointChartCachedLiterals($Value, [string]$Label) {
  if ($null -eq $Value) {
    throw "PowerPoint chart $Label cached literals are missing."
  }
  $rawItems = if ($Value -is [Array]) { @($Value) } else { @($Value) }
  if ($rawItems.Count -eq 0 -or $rawItems.Count -gt $nativeDetailMaxChartPoints) {
    throw "PowerPoint chart $Label cached literal count is outside the bounded range."
  }
  @(
    foreach ($rawItem in $rawItems) {
      if ($null -eq $rawItem) {
        [PSCustomObject]@{ Kind = "null"; Value = $null }
      } elseif ($rawItem -is [bool]) {
        [PSCustomObject]@{ Kind = "boolean"; Value = [bool]$rawItem }
      } elseif (
        $rawItem -is [byte] -or $rawItem -is [sbyte] -or
        $rawItem -is [int16] -or $rawItem -is [uint16] -or
        $rawItem -is [int32] -or $rawItem -is [uint32] -or
        $rawItem -is [int64] -or $rawItem -is [uint64] -or
        $rawItem -is [single] -or $rawItem -is [double] -or
        $rawItem -is [decimal]
      ) {
        $number = [double]$rawItem
        if ([double]::IsNaN($number) -or [double]::IsInfinity($number)) {
          throw "PowerPoint chart $Label contains a non-finite cached literal."
        }
        [PSCustomObject]@{
          Kind = "number"
          Value = $number.ToString("R", [Globalization.CultureInfo]::InvariantCulture)
        }
      } elseif ($rawItem -is [string]) {
        $text = [string]$rawItem
        if ($text.Length -gt $nativeDetailMaxChartLiteralTextLength) {
          throw "PowerPoint chart $Label cached literal text exceeds the bound."
        }
        if ($text.StartsWith("=")) {
          throw "PowerPoint chart $Label returned a formula instead of cached literals."
        }
        [PSCustomObject]@{ Kind = "string"; Value = $text }
      } else {
        throw "PowerPoint chart $Label contains an unsupported cached literal type."
      }
    }
  )
}

function Assert-PowerPointTableNativeDetail($Detail) {
  if (
    $null -eq $Detail -or $Detail.RowCount -lt 1 -or $Detail.ColumnCount -lt 1 -or
    @($Detail.RowHeightsMicroPoint).Count -ne [int]$Detail.RowCount -or
    @($Detail.ColumnWidthsMicroPoint).Count -ne [int]$Detail.ColumnCount -or
    @($Detail.Cells).Count -ne ([int]$Detail.RowCount * [int]$Detail.ColumnCount) -or
    @($Detail.RowHeightsMicroPoint | Where-Object { [int64]$_ -le 0 }).Count -ne 0 -or
    @($Detail.ColumnWidthsMicroPoint | Where-Object { [int64]$_ -le 0 }).Count -ne 0
  ) {
    throw "PowerPoint table row/column geometry readback is incomplete."
  }
  foreach ($cell in @($Detail.Cells)) {
    $borders = @($cell.Borders)
    if (
      $borders.Count -ne 6 -or
      (@($borders.BorderIndex | Sort-Object) -join ",") -ne "1,2,3,4,5,6" -or
      @($borders | Where-Object { $null -eq $_.Paint }).Count -ne 0
    ) {
      throw "PowerPoint table border readback is incomplete."
    }
  }
  $projection = [ordered]@{
    RowHeightsMicroPoint = @($Detail.RowHeightsMicroPoint)
    ColumnWidthsMicroPoint = @($Detail.ColumnWidthsMicroPoint)
    Cells = @($Detail.Cells | ForEach-Object {
      [ordered]@{ Row = $_.Row; Column = $_.Column; Borders = @($_.Borders) }
    })
    MergedTopology = @($Detail.MergedTopology)
  }
  $digest = Get-Sha256Text ($projection | ConvertTo-Json -Depth 12 -Compress)
  if ($Detail.BorderRowHeightDigest -ne $digest) {
    throw "PowerPoint table border/row-height digest is invalid."
  }
}

function Assert-PowerPointChartNativeDetail($Detail) {
  if (
    $null -eq $Detail -or $Detail.SeriesCount -lt 1 -or
    @($Detail.Series).Count -ne [int]$Detail.SeriesCount -or
    $null -eq $Detail.ChartStyle -or $null -eq $Detail.ChartType
  ) {
    throw "PowerPoint chart native readback is incomplete."
  }
  foreach ($series in @($Detail.Series)) {
    $categories = @($series.Categories)
    $values = @($series.Values)
    $points = @($series.Points)
    if (
      $categories.Count -eq 0 -or $categories.Count -ne $values.Count -or
      $values.Count -ne $points.Count -or $null -eq $series.Paint -or
      @($points | Where-Object { $null -eq $_.Paint }).Count -ne 0
    ) {
      throw "PowerPoint chart category/value/point readback is incomplete."
    }
    $digest = Get-Sha256Text (([ordered]@{
      Categories = $categories
      Values = $values
    }) | ConvertTo-Json -Depth 8 -Compress)
    if ($series.CachedLiteralDigest -ne $digest) {
      throw "PowerPoint chart cached literal digest is invalid."
    }
  }
}

function Read-PowerPointCellBorders($Cell) {
  $borders = $null
  try {
    $borders = $Cell.Borders
    @(
      for ($borderIndex = 1; $borderIndex -le 6; $borderIndex += 1) {
        $border = $null
        try {
          $border = $borders.Item($borderIndex)
          [PSCustomObject]@{
            BorderIndex = $borderIndex
            Paint = Read-PowerPointSolidLinePaint $border
          }
        } finally {
          Release-PowerPointComObject $border
        }
      }
    )
  } finally {
    Release-PowerPointComObject $borders
  }
}

function Read-PowerPointSolidFillPaint($FillFormat) {
  if ($null -eq $FillFormat) { return $null }
  $foreColor = $null
  try {
    $visible = [int]$FillFormat.Visible
    $fillType = [int]$FillFormat.Type
    $isSolid = $visible -eq -1 -and $fillType -eq 1
    if ($isSolid) { $foreColor = $FillFormat.ForeColor }
    [PSCustomObject]@{
      Visible = $visible
      Type = $fillType
      IsSolid = $isSolid
      ForeColorRgb = if ($null -ne $foreColor) {
        [int]$foreColor.RGB
      } else { $null }
      Transparency = if ($visible -eq -1) {
        [double]$FillFormat.Transparency
      } else { $null }
    }
  } finally {
    Release-PowerPointComObject $foreColor
  }
}

function Read-PowerPointSolidLinePaint($LineFormat) {
  if ($null -eq $LineFormat) { return $null }
  $foreColor = $null
  try {
    $visible = [int]$LineFormat.Visible
    $dashStyle = if ($visible -eq -1) {
      [int]$LineFormat.DashStyle
    } else { $null }
    if ($visible -eq -1) { $foreColor = $LineFormat.ForeColor }
    [PSCustomObject]@{
      Visible = $visible
      IsSolid = $visible -eq -1 -and $dashStyle -eq 1
      DashStyle = $dashStyle
      ForeColorRgb = if ($null -ne $foreColor) {
        [int]$foreColor.RGB
      } else { $null }
      Transparency = if ($visible -eq -1) {
        [double]$LineFormat.Transparency
      } else { $null }
      WeightPt = if ($visible -eq -1) {
        [double]$LineFormat.Weight
      } else { $null }
    }
  } finally {
    Release-PowerPointComObject $foreColor
  }
}

function Convert-PowerPointRgbToHex($Rgb) {
  $value = [uint32]([int64]$Rgb -band 0xFFFFFF)
  $red = $value -band 0xFF
  $green = ($value -shr 8) -band 0xFF
  $blue = ($value -shr 16) -band 0xFF
  "#{0:X2}{1:X2}{2:X2}" -f $red, $green, $blue
}

function Convert-PowerPointTableProjectionToPhysicalEdges($Projection) {
  $rowCount = [int]$Projection.rowCount
  $columnCount = [int]$Projection.columnCount
  $cellStyles = [object[]]::new($rowCount)
  for ($row = 0; $row -lt $rowCount; $row += 1) {
    $cellStyles[$row] = [object[]]::new($columnCount)
    for ($column = 0; $column -lt $columnCount; $column += 1) {
      $style = $Projection.cellStyles[$row][$column]
      $cellStyles[$row][$column] = [PSCustomObject][ordered]@{
        fill = $style.fill
        horizontalAlign = $style.horizontalAlign
        verticalAlign = $style.verticalAlign
        borders = [PSCustomObject][ordered]@{
          top = $style.borders.top
          right = $style.borders.right
          bottom = $style.borders.bottom
          left = $style.borders.left
        }
      }
    }
  }
  foreach ($merge in @($Projection.mergedCells)) {
    $anchorRow = [int]$merge.row
    $anchorColumn = [int]$merge.column
    $lastRow = $anchorRow + [int]$merge.rowSpan - 1
    $lastColumn = $anchorColumn + [int]$merge.columnSpan - 1
    $anchorTop = $cellStyles[$anchorRow][$anchorColumn].borders.top
    $anchorRight = $cellStyles[$anchorRow][$anchorColumn].borders.right
    $anchorBottom = $cellStyles[$anchorRow][$anchorColumn].borders.bottom
    $anchorLeft = $cellStyles[$anchorRow][$anchorColumn].borders.left
    for ($row = $anchorRow; $row -le $lastRow; $row += 1) {
      for ($column = $anchorColumn; $column -le $lastColumn; $column += 1) {
        $cellStyles[$row][$column].borders.top =
          $(if ($row -eq $anchorRow) { $anchorTop } else { $null })
        $cellStyles[$row][$column].borders.right =
          $(if ($column -eq $lastColumn) { $anchorRight } else { $null })
        $cellStyles[$row][$column].borders.bottom =
          $(if ($row -eq $lastRow) { $anchorBottom } else { $null })
        $cellStyles[$row][$column].borders.left =
          $(if ($column -eq $anchorColumn) { $anchorLeft } else { $null })
      }
    }
  }
  for ($row = 0; $row -lt $rowCount - 1; $row += 1) {
    for ($column = 0; $column -lt $columnCount; $column += 1) {
      $upper = $cellStyles[$row][$column].borders.bottom
      $lower = $cellStyles[$row + 1][$column].borders.top
      if ($null -ne $upper -and $null -ne $lower -and
        (ConvertTo-CanonicalLayerJson $upper) -ne
        (ConvertTo-CanonicalLayerJson $lower)) {
        return $null
      }
      $resolved = if ($null -ne $upper) { $upper } else { $lower }
      $cellStyles[$row][$column].borders.bottom = $resolved
      $cellStyles[$row + 1][$column].borders.top = $resolved
    }
  }
  for ($row = 0; $row -lt $rowCount; $row += 1) {
    for ($column = 0; $column -lt $columnCount - 1; $column += 1) {
      $left = $cellStyles[$row][$column].borders.right
      $right = $cellStyles[$row][$column + 1].borders.left
      if ($null -ne $left -and $null -ne $right -and
        (ConvertTo-CanonicalLayerJson $left) -ne
        (ConvertTo-CanonicalLayerJson $right)) {
        return $null
      }
      $resolved = if ($null -ne $left) { $left } else { $right }
      $cellStyles[$row][$column].borders.right = $resolved
      $cellStyles[$row][$column + 1].borders.left = $resolved
    }
  }
  [ordered]@{
    rowCount = $rowCount
    columnCount = $columnCount
    cells = @($Projection.cells)
    rowHeightsMicroPoint = @($Projection.rowHeightsMicroPoint)
    columnWidthsMicroPoint = @($Projection.columnWidthsMicroPoint)
    mergedCells = @($Projection.mergedCells)
    cellStyles = $cellStyles
  }
}

function Convert-PowerPointTableProjectionMergedCoverageToNull($Projection) {
  $rowCount = [int]$Projection.rowCount
  $columnCount = [int]$Projection.columnCount
  $cells = [object[]]::new($rowCount)
  $cellStyles = [object[]]::new($rowCount)
  for ($row = 0; $row -lt $rowCount; $row += 1) {
    $cells[$row] = [object[]]::new($columnCount)
    $cellStyles[$row] = [object[]]::new($columnCount)
    for ($column = 0; $column -lt $columnCount; $column += 1) {
      $cells[$row][$column] = $Projection.cells[$row][$column]
      $cellStyles[$row][$column] = $Projection.cellStyles[$row][$column]
    }
  }
  foreach ($merge in @($Projection.mergedCells)) {
    $anchorRow = [int]$merge.row
    $anchorColumn = [int]$merge.column
    $rowLimit = $anchorRow + [int]$merge.rowSpan
    $columnLimit = $anchorColumn + [int]$merge.columnSpan
    # Preserve the four observable merged-table perimeter edges on the anchor
    # before covered aliases are removed from the semantic comparison.
    $cellStyles[$anchorRow][$anchorColumn].borders.right =
      $cellStyles[$anchorRow][$columnLimit - 1].borders.right
    $cellStyles[$anchorRow][$anchorColumn].borders.bottom =
      $cellStyles[$rowLimit - 1][$anchorColumn].borders.bottom
    for ($row = $anchorRow; $row -lt $rowLimit; $row += 1) {
      for ($column = $anchorColumn; $column -lt $columnLimit; $column += 1) {
        if ($row -eq $anchorRow -and $column -eq $anchorColumn) { continue }
        $cells[$row][$column] = $null
        $cellStyles[$row][$column] = $null
      }
    }
  }
  [ordered]@{
    rowCount = $rowCount
    columnCount = $columnCount
    cells = $cells
    rowHeightsMicroPoint = @($Projection.rowHeightsMicroPoint)
    columnWidthsMicroPoint = @($Projection.columnWidthsMicroPoint)
    mergedCells = @($Projection.mergedCells)
    cellStyles = $cellStyles
  }
}

function Convert-PowerPointTableDetailToSemanticProjection($Detail) {
  Assert-PowerPointTableNativeDetail $Detail
  $horizontal = @{ 1 = "left"; 2 = "center"; 3 = "right"; 4 = "justify" }
  $vertical = @{ 1 = "top"; 3 = "middle"; 4 = "bottom" }
  $edgeByIndex = @{ 1 = "top"; 2 = "left"; 3 = "bottom"; 4 = "right" }
  $cells = @(
    for ($row = 1; $row -le [int]$Detail.RowCount; $row += 1) {
      ,@(
        for ($column = 1; $column -le [int]$Detail.ColumnCount; $column += 1) {
          $cell = @($Detail.Cells | Where-Object {
            [int]$_.Row -eq $row -and [int]$_.Column -eq $column
          })
          if ($cell.Count -ne 1) { throw "PowerPoint table cell identity is not unique." }
          [string]$cell[0].Text
        }
      )
    }
  )
  $cellStyles = @(
    for ($row = 1; $row -le [int]$Detail.RowCount; $row += 1) {
      ,@(
        for ($column = 1; $column -le [int]$Detail.ColumnCount; $column += 1) {
          $cell = @($Detail.Cells | Where-Object {
            [int]$_.Row -eq $row -and [int]$_.Column -eq $column
          })[0]
          $borderMap = [ordered]@{ top = $null; right = $null; bottom = $null; left = $null }
          foreach ($border in @($cell.Borders | Where-Object { [int]$_.BorderIndex -le 4 })) {
            $edge = $edgeByIndex[[int]$border.BorderIndex]
            if ($null -eq $edge) { throw "PowerPoint table border index is unsupported." }
            if ([int]$border.Paint.Visible -eq -1) {
              $isNoFillSentinel =
                [int64]$border.Paint.ForeColorRgb -eq [int64][int]::MinValue -and
                [double]$border.Paint.Transparency -eq [double][int]::MinValue
              if ($isNoFillSentinel) { continue }
              if (-not [bool]$border.Paint.IsSolid -or $null -eq $border.Paint.ForeColorRgb -or
                $null -eq $border.Paint.WeightPt -or [double]$border.Paint.Transparency -ne 0) {
                throw "PowerPoint table visible border is not an exact solid semantic border."
              }
              $borderMap[$edge] = [ordered]@{
                color = Convert-PowerPointRgbToHex $border.Paint.ForeColorRgb
                widthMicroPoint = [int64][Math]::Round([double]$border.Paint.WeightPt * 1000000)
              }
            }
          }
          if (-not $horizontal.ContainsKey([int]$cell.ParagraphAlignment) -or
            -not $vertical.ContainsKey([int]$cell.VerticalAnchor)) {
            throw "PowerPoint table alignment is outside the bounded semantic mapping."
          }
          $fill = $null
          if ([int]$cell.Fill.Visible -eq -1) {
            if (-not [bool]$cell.Fill.IsSolid -or $null -eq $cell.Fill.ForeColorRgb -or
              [double]$cell.Fill.Transparency -ne 0) {
              throw "PowerPoint table fill is not an exact solid semantic fill."
            }
            $fill = Convert-PowerPointRgbToHex $cell.Fill.ForeColorRgb
          }
          [ordered]@{
            fill = $fill
            horizontalAlign = $horizontal[[int]$cell.ParagraphAlignment]
            verticalAlign = $vertical[[int]$cell.VerticalAnchor]
            borders = $borderMap
          }
        }
      )
    }
  )
  $mergedCells = @(
    foreach ($topology in @($Detail.MergedTopology)) {
      $members = @($topology.Cells)
      $minRow = [int]($members.Row | Measure-Object -Minimum).Minimum
      $maxRow = [int]($members.Row | Measure-Object -Maximum).Maximum
      $minColumn = [int]($members.Column | Measure-Object -Minimum).Minimum
      $maxColumn = [int]($members.Column | Measure-Object -Maximum).Maximum
      if ($members.Count -ne (($maxRow - $minRow + 1) * ($maxColumn - $minColumn + 1))) {
        throw "PowerPoint table merged topology is not rectangular."
      }
      [ordered]@{
        row = $minRow - 1
        column = $minColumn - 1
        rowSpan = $maxRow - $minRow + 1
        columnSpan = $maxColumn - $minColumn + 1
      }
    }
  )
  [ordered]@{
    rowCount = [int]$Detail.RowCount
    columnCount = [int]$Detail.ColumnCount
    cells = $cells
    rowHeightsMicroPoint = @($Detail.RowHeightsMicroPoint)
    columnWidthsMicroPoint = @($Detail.ColumnWidthsMicroPoint)
    mergedCells = $mergedCells
    cellStyles = $cellStyles
  }
}

function Read-PowerPointAdvancedTextNativeDetail($Shape, $Ownership) {
  if ($null -eq $Ownership -or $Ownership.CapabilityId -ne "editable.text.advanced.v1") {
    return $null
  }
  $frame = $null
  $textRange = $null
  try {
    $frame = $Shape.TextFrame2
    $textRange = $frame.TextRange
    $actualStyles = @(
      for ($index = 0; $index -lt @($Ownership.AdvancedTextRunMap).Count; $index += 1) {
        $identity = @($Ownership.AdvancedTextRunMap)[$index]
        $sealedStyle = @($Ownership.AdvancedTextRunStyles)[$index]
        if ([int]$identity.utf16Length -eq 0) {
          $sealedStyle
          continue
        }
        $runRange = $null
        $font = $null
        $fill = $null
        try {
          $runRange = $textRange.Characters([int]$identity.utf16Start + 1, [int]$identity.utf16Length)
          $font = $runRange.Font
          $fill = $font.Fill
          [PSCustomObject]@{
            runIndex = [int]$identity.runIndex
            runId = [string]$identity.runId
            fontFamily = [string]$font.Name
            eastAsianTypeface = [string]$font.NameFarEast
            fontSizePt = [double]$font.Size
            bold = [int]$font.Bold -ne 0
            italic = [int]$font.Italic -ne 0
            underline = [int]$font.UnderlineStyle -ne 0
            color = Convert-PowerPointRgbToHex $fill.ForeColor.RGB
          }
        } finally {
          Release-PowerPointComObject $fill
          Release-PowerPointComObject $font
          Release-PowerPointComObject $runRange
        }
      }
    )
    [PSCustomObject]@{
      AutoFit = [string]$Ownership.AdvancedTextAutoFit
      ComAutoSize = [int]$frame.AutoSize
      TextRunMap = @($Ownership.AdvancedTextRunMap)
      TextRunStyles = $actualStyles
      AdvancedTextDigest = [string]$Ownership.AdvancedTextDigest
    }
  } finally {
    Release-PowerPointComObject $textRange
    Release-PowerPointComObject $frame
  }
}

function Read-PowerPointTableNativeDetail($Shape) {
  $table = $null
  $rows = $null
  $columns = $null
  try {
    $table = $Shape.Table
    $rows = $table.Rows
    $columns = $table.Columns
    $rowCount = [int]$rows.Count
    $columnCount = [int]$columns.Count
    if (
      $rowCount -gt $nativeDetailMaxTableRows -or
      $columnCount -gt $nativeDetailMaxTableColumns -or
      ($rowCount * $columnCount) -gt $nativeDetailMaxTableCells
    ) {
      throw "PowerPoint table exceeds the native readback bounds."
    }

    $rowHeightsPt = @(
      for ($rowIndex = 1; $rowIndex -le $rowCount; $rowIndex += 1) {
        $row = $null
        try {
          $row = $rows.Item($rowIndex)
          [double]$row.Height
        } finally {
          Release-PowerPointComObject $row
        }
      }
    )
    $columnWidthsPt = @(
      for ($columnIndex = 1; $columnIndex -le $columnCount; $columnIndex += 1) {
        $column = $null
        try {
          $column = $columns.Item($columnIndex)
          [double]$column.Width
        } finally {
          Release-PowerPointComObject $column
        }
      }
    )
    $cells = @(
      for ($rowIndex = 1; $rowIndex -le $rowCount; $rowIndex += 1) {
        for ($columnIndex = 1; $columnIndex -le $columnCount; $columnIndex += 1) {
          $cell = $null
          $cellShape = $null
          $textFrame = $null
          $textRange = $null
          $font = $null
          $fontFill = $null
          $paragraphFormat = $null
          $cellFill = $null
          try {
            $cell = $table.Cell($rowIndex, $columnIndex)
            $cellShape = $cell.Shape
            $textFrame = $cellShape.TextFrame2
            $textRange = $textFrame.TextRange
            $font = $textRange.Font
            $fontFill = $font.Fill
            $paragraphFormat = $textRange.ParagraphFormat
            $cellFill = $cellShape.Fill
            [PSCustomObject]@{
              Row = $rowIndex
              Column = $columnIndex
              AnchorShapeId = [int]$cellShape.Id
              AnchorShapeName = [string]$cellShape.Name
              AnchorLeftMicroPoint = [int64][Math]::Round([double]$cellShape.Left * 1000000)
              AnchorTopMicroPoint = [int64][Math]::Round([double]$cellShape.Top * 1000000)
              AnchorWidthMicroPoint = [int64][Math]::Round([double]$cellShape.Width * 1000000)
              AnchorHeightMicroPoint = [int64][Math]::Round([double]$cellShape.Height * 1000000)
              Text = Get-BoundedPowerPointCellText ([string]$textRange.Text)
              TextAutoSize = [int]$textFrame.AutoSize
              VerticalAnchor = [int]$textFrame.VerticalAnchor
              MarginsPt = [PSCustomObject]@{
                Left = [double]$textFrame.MarginLeft
                Top = [double]$textFrame.MarginTop
                Right = [double]$textFrame.MarginRight
                Bottom = [double]$textFrame.MarginBottom
              }
              ParagraphAlignment = [int]$paragraphFormat.Alignment
              Font = [PSCustomObject]@{
                Name = [string]$font.Name
                NameFarEast = [string]$font.NameFarEast
                SizePt = [double]$font.Size
                Bold = [int]$font.Bold
                Italic = [int]$font.Italic
                UnderlineStyle = [int]$font.UnderlineStyle
                Fill = Read-PowerPointSolidFillPaint $fontFill
              }
              Fill = Read-PowerPointSolidFillPaint $cellFill
              Borders = Read-PowerPointCellBorders $cell
            }
          } finally {
            Release-PowerPointComObject $cellFill
            Release-PowerPointComObject $paragraphFormat
            Release-PowerPointComObject $fontFill
            Release-PowerPointComObject $font
            Release-PowerPointComObject $textRange
            Release-PowerPointComObject $textFrame
            Release-PowerPointComObject $cellShape
            Release-PowerPointComObject $cell
          }
        }
      }
    )
    $rowHeightsMicroPoint = @($rowHeightsPt | ForEach-Object {
      [int64][Math]::Round([double]$_ * 1000000)
    })
    $columnWidthsMicroPoint = @($columnWidthsPt | ForEach-Object {
      [int64][Math]::Round([double]$_ * 1000000)
    })
    $mergedTopology = @(
      $cells |
        Group-Object { "$($_.AnchorShapeId):$($_.AnchorLeftMicroPoint):$($_.AnchorTopMicroPoint):$($_.AnchorWidthMicroPoint):$($_.AnchorHeightMicroPoint)" } |
        Where-Object { $_.Count -gt 1 } |
        ForEach-Object {
          $members = @($_.Group | Sort-Object Row, Column)
          [PSCustomObject]@{
            AnchorShapeId = [int]$members[0].AnchorShapeId
            AnchorShapeName = [string]$members[0].AnchorShapeName
            AnchorLeftMicroPoint = [int64]$members[0].AnchorLeftMicroPoint
            AnchorTopMicroPoint = [int64]$members[0].AnchorTopMicroPoint
            AnchorWidthMicroPoint = [int64]$members[0].AnchorWidthMicroPoint
            AnchorHeightMicroPoint = [int64]$members[0].AnchorHeightMicroPoint
            AnchorRow = [int]$members[0].Row
            AnchorColumn = [int]$members[0].Column
            Cells = @($members | ForEach-Object {
              [PSCustomObject]@{ Row = [int]$_.Row; Column = [int]$_.Column }
            })
          }
        }
    )
    $borderRowHeightProjection = [ordered]@{
      RowHeightsMicroPoint = $rowHeightsMicroPoint
      ColumnWidthsMicroPoint = $columnWidthsMicroPoint
      Cells = @($cells | ForEach-Object {
        [ordered]@{ Row = $_.Row; Column = $_.Column; Borders = @($_.Borders) }
      })
      MergedTopology = $mergedTopology
    }
    $detail = [PSCustomObject]@{
      RowCount = $rowCount
      ColumnCount = $columnCount
      RowHeightsPt = $rowHeightsPt
      RowHeightsMicroPoint = $rowHeightsMicroPoint
      ColumnWidthsPt = $columnWidthsPt
      ColumnWidthsMicroPoint = $columnWidthsMicroPoint
      Cells = $cells
      MergedTopology = $mergedTopology
      BorderRowHeightDigest = Get-Sha256Text `
        ($borderRowHeightProjection | ConvertTo-Json -Depth 12 -Compress)
    }
    Assert-PowerPointTableNativeDetail $detail
    $detail
  } finally {
    Release-PowerPointComObject $columns
    Release-PowerPointComObject $rows
    Release-PowerPointComObject $table
  }
}

function Read-PowerPointChartFormatPaint($Format) {
  if ($null -eq $Format) { return $null }
  $fill = $null
  $line = $null
  try {
    $fill = try { $Format.Fill } catch { $null }
    $line = try { $Format.Line } catch { $null }
    [PSCustomObject]@{
      Fill = Read-PowerPointSolidFillPaint $fill
      Line = Read-PowerPointSolidLinePaint $line
    }
  } finally {
    Release-PowerPointComObject $line
    Release-PowerPointComObject $fill
  }
}

function Read-PowerPointChartNativeDetail($Shape) {
  $chart = $null
  $seriesCollection = $null
  try {
    $chart = $Shape.Chart
    $seriesCollection = $chart.SeriesCollection()
    $seriesCount = [int]$seriesCollection.Count
    if ($seriesCount -gt $nativeDetailMaxChartSeries) {
      throw "PowerPoint chart exceeds the bounded series readback count."
    }
    $series = @(
      for ($seriesIndex = 1; $seriesIndex -le $seriesCount; $seriesIndex += 1) {
        $item = $null
        $format = $null
        $points = $null
        try {
          $item = $seriesCollection.Item($seriesIndex)
          $format = try { $item.Format } catch { $null }
          $points = $item.Points()
          $pointCount = [int]$points.Count
          if ($pointCount -gt $nativeDetailMaxChartPoints) {
            throw "PowerPoint chart exceeds the bounded point readback count."
          }
          $pointPaints = @(
            for ($pointIndex = 1; $pointIndex -le $pointCount; $pointIndex += 1) {
              $point = $null
              $pointFormat = $null
              try {
                $point = $points.Item($pointIndex)
                $pointFormat = try { $point.Format } catch { $null }
                [PSCustomObject]@{
                  PointIndex = $pointIndex
                  Paint = Read-PowerPointChartFormatPaint $pointFormat
                }
              } finally {
                Release-PowerPointComObject $pointFormat
                Release-PowerPointComObject $point
              }
            }
          )
          $categories = Convert-PowerPointChartCachedLiterals `
            $item.XValues "categories"
          $values = Convert-PowerPointChartCachedLiterals `
            $item.Values "values"
          $cachedLiteralProjection = [ordered]@{
            Categories = $categories
            Values = $values
          }
          [PSCustomObject]@{
            SeriesIndex = $seriesIndex
            Name = [string]$item.Name
            Formula = $(try { [string]$item.Formula } catch { $null })
            ChartType = [int]$item.ChartType
            MarkerStyle = $(try { [int]$item.MarkerStyle } catch { $null })
            MarkerSize = $(try { [int]$item.MarkerSize } catch { $null })
            MarkerForegroundColorRgb = $(try { [int]$item.MarkerForegroundColor } catch { $null })
            MarkerBackgroundColorRgb = $(try { [int]$item.MarkerBackgroundColor } catch { $null })
            Paint = Read-PowerPointChartFormatPaint $format
            Categories = $categories
            Values = $values
            CachedLiteralDigest = Get-Sha256Text `
              ($cachedLiteralProjection | ConvertTo-Json -Depth 8 -Compress)
            Points = $pointPaints
          }
        } finally {
          Release-PowerPointComObject $points
          Release-PowerPointComObject $format
          Release-PowerPointComObject $item
        }
      }
    )
    $detail = [PSCustomObject]@{
      ChartStyle = [int]$chart.ChartStyle
      ChartType = [int]$chart.ChartType
      HasLegend = [bool]$chart.HasLegend
      SeriesCount = $seriesCount
      Series = $series
    }
    Assert-PowerPointChartNativeDetail $detail
    $detail
  } finally {
    Release-PowerPointComObject $seriesCollection
    Release-PowerPointComObject $chart
  }
}

function Read-PowerPointNativeShapeDetail(
  $Shape,
  [bool]$HasText,
  [bool]$HasTable,
  [bool]$HasChart,
  [int]$ShapeType,
  $TextAutoSize,
  $Ownership
) {
  $fill = $null
  $line = $null
  $pictureFormat = $null
  $chartDetail = $null
  try {
    $fill = try { $Shape.Fill } catch { $null }
    $line = try { $Shape.Line } catch { $null }
    if ($ShapeType -in 11, 13) {
      $pictureFormat = $Shape.PictureFormat
    }
    if ($HasChart) {
      $chartDetail = Read-PowerPointChartNativeDetail $Shape
      if ($null -ne $Ownership -and
        $Ownership.PSObject.Properties.Name -contains "ChartCarrierType") {
        $expectedChartType = if ($Ownership.ChartCarrierType -eq "line") { 4 } else { 5 }
        if ([int]$chartDetail.ChartType -ne $expectedChartType) {
          throw "Authored chart tag carrier subtype does not match COM ChartType."
        }
      }
    }
    [PSCustomObject]@{
      TextAutoSize = if ($HasText) { [int]$TextAutoSize } else { $null }
      AdvancedText = if ($HasText) {
        Read-PowerPointAdvancedTextNativeDetail $Shape $Ownership
      } else { $null }
      SolidFill = Read-PowerPointSolidFillPaint $fill
      SolidLine = Read-PowerPointSolidLinePaint $line
      PictureCrop = if ($null -ne $pictureFormat) {
        [PSCustomObject]@{
          LeftPt = [double]$pictureFormat.CropLeft
          TopPt = [double]$pictureFormat.CropTop
          RightPt = [double]$pictureFormat.CropRight
          BottomPt = [double]$pictureFormat.CropBottom
        }
      } else { $null }
      Table = if ($HasTable) {
        Read-PowerPointTableNativeDetail $Shape
      } else { $null }
      Chart = $chartDetail
    }
  } finally {
    Release-PowerPointComObject $pictureFormat
    Release-PowerPointComObject $line
    Release-PowerPointComObject $fill
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

$resolvedOutput = [System.IO.Path]::GetFullPath($OutputDirectory)
[System.IO.Directory]::CreateDirectory($resolvedOutput) | Out-Null
$powerPointPath = "C:\Program Files\Microsoft Office\root\Office16\POWERPNT.EXE"
$powerPointExecutableIdentity = Get-PowerPointModuleFileIdentity $powerPointPath
$powerPointPath = $powerPointExecutableIdentity.resolvedAbsolutePath
$chartModulePath = Join-Path (Split-Path -Parent $powerPointPath) "chart.dll"
$chartModuleIdentity = Get-PowerPointModuleFileIdentity $chartModulePath
$moduleCompatibilityPreflight = Write-PowerPointModuleCompatibilityPreflight `
  $powerPointExecutableIdentity $chartModuleIdentity $resolvedOutput
Assert-PowerPointModuleCompatibilityPreflight $moduleCompatibilityPreflight
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
$expectedOwnership = Read-ExpectedOwnershipManifest `
  $ExpectedOwnershipManifest $PresentationId $PresentationRevision
if ($authorityPinsProvided -and $null -eq $expectedOwnership) {
  throw "Actual-product COM authority requires the exact expected ownership manifest."
}

$launched = $null
$application = $null
$applicationIdentity = $null
$provisionalApplicationIdentity = $null
$requiresPostOpenWindowBinding = $false
$applicationWindowBindingPhase = "rot-application-hwnd"
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
    $provisionalApplicationIdentity = [PSCustomObject]@{
      Hwnd = [int64]0
      ProcessId = [int]$launched.Id
    }
    $requiresPostOpenWindowBinding = $true
    $applicationWindowBindingPhase = "post-first-open-enumerated-frame-hwnd"
  }

  $application.Visible = -1
  if (-not $requiresPostOpenWindowBinding -and ($null -eq $applicationIdentity -or $applicationIdentity.Hwnd -eq 0 -or $applicationIdentity.ProcessId -ne [int]$launched.Id)) {
    throw "PowerPoint Application HWND/PID does not match the launched process."
  }
  if (-not $requiresPostOpenWindowBinding) {
    $rotFrameIdentity = $null
    for ($identityAttempt = 0; $identityAttempt -lt 20 -and $null -eq $rotFrameIdentity; $identityAttempt += 1) {
      $rotFrameIdentity = Get-PowerPointVisibleFrameIdentity ([int]$launched.Id)
      if ($null -eq $rotFrameIdentity) { Start-Sleep -Milliseconds 250 }
    }
    if ($null -eq $rotFrameIdentity -or $rotFrameIdentity.Hwnd -ne $applicationIdentity.Hwnd -or $rotFrameIdentity.ProcessId -ne $applicationIdentity.ProcessId) {
      throw "PowerPoint ROT Application HWND does not match the unique visible PPTFrameClass window."
    }
    $applicationIdentity = $rotFrameIdentity
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
      if ($requiresPostOpenWindowBinding) {
        $postOpenIdentity = $null
        for ($identityAttempt = 0; $identityAttempt -lt 20 -and $null -eq $postOpenIdentity; $identityAttempt += 1) {
          $postOpenIdentity = Get-PowerPointVisibleFrameIdentity ([int]$launched.Id)
          if ($null -eq $postOpenIdentity) { Start-Sleep -Milliseconds 250 }
        }
        $applicationIdentity = Complete-PowerPointFallbackWindowBinding `
          $provisionalApplicationIdentity $postOpenIdentity
        $requiresPostOpenWindowBinding = $false
        $attachedAt = Get-Date
      }
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
          $sourceTextFrame = $null
          $sourceTextRange = $null
          $sourceTextFont = $null
          $sourceTextLines = $null
          try {
            $sourceHasTextFrame = [bool]$sourceShape.HasTextFrame
            if ($sourceHasTextFrame) {
              $sourceTextFrame = $sourceShape.TextFrame2
            }
            $sourceHasText = $sourceHasTextFrame -and [bool]$sourceTextFrame.HasText
            $sourceHasTable = [bool]$sourceShape.HasTable
            $sourceHasChart = [bool]$sourceShape.HasChart
            $sourceShapeType = [int]$sourceShape.Type
            if ($sourceHasText) {
              $sourceTextRange = $sourceTextFrame.TextRange
              $sourceTextFont = $sourceTextRange.Font
              $sourceTextLines = $sourceTextRange.Lines()
            }
            $sourceTextAutoSize = if ($sourceHasText) {
              [int]$sourceTextFrame.AutoSize
            } else { $null }
            $sourceText = if ($sourceHasText) { [string]$sourceTextRange.Text } else { $null }
            $sourceAlternativeText = try {
              [string]$sourceShape.AlternativeText
            } catch { "" }
            $sourceSemanticOwnership = Read-AuthoredShapeOwnership `
              $sourceAlternativeText $slideIndex ([string]$sourceShape.Name) $sourceShape
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
              OwnershipKind = if ($null -ne $sourceSemanticOwnership) {
                $sourceSemanticOwnership.Kind
              } else { $null }
              OwnershipSceneSlideId = if ($null -ne $sourceSemanticOwnership) {
                $sourceSemanticOwnership.SceneSlideId
              } else { $null }
              OwnershipCapabilityId = if ($null -ne $sourceSemanticOwnership) {
                $sourceSemanticOwnership.CapabilityId
              } else { $null }
              OwnershipDisposition = if ($null -ne $sourceSemanticOwnership) {
                $sourceSemanticOwnership.Disposition
              } else { $null }
              OwnershipSource = if ($null -ne $sourceSemanticOwnership) {
                $sourceSemanticOwnership.Source
              } else { $null }
              Type = $sourceShapeType
              ZOrderPosition = [int]$sourceShape.ZOrderPosition
              HasText = $sourceHasText
              Text = $sourceText
              TextLineBreakCount = if ($null -ne $sourceText) {
                [regex]::Matches($sourceText, "[`r`n`v]").Count
              } else { 0 }
              TextRenderedLineCount = if ($sourceHasText) { [int]$sourceTextLines.Count } else { 0 }
              TextBoundWidthPt = if ($sourceHasText) { [double]$sourceTextRange.BoundWidth } else { $null }
              TextBoundHeightPt = if ($sourceHasText) { [double]$sourceTextRange.BoundHeight } else { $null }
              Typeface = if ($sourceHasText) {
                [string]$sourceTextFont.Name
              } else { $null }
              EastAsianTypeface = if ($sourceHasText) {
                [string]$sourceTextFont.NameFarEast
              } else { $null }
              LeftPt = [double]$sourceShape.Left
              TopPt = [double]$sourceShape.Top
              WidthPt = [double]$sourceShape.Width
              HeightPt = [double]$sourceShape.Height
              NativeDetail = Read-PowerPointNativeShapeDetail `
                $sourceShape $sourceHasText $sourceHasTable $sourceHasChart `
                $sourceShapeType $sourceTextAutoSize $sourceSemanticOwnership
            }
          } finally {
            Release-PowerPointComObject $sourceTextLines
            Release-PowerPointComObject $sourceTextFont
            if ($null -ne $sourceTextRange) {
              [Runtime.InteropServices.Marshal]::FinalReleaseComObject($sourceTextRange) | Out-Null
              $sourceTextRange = $null
            }
            Release-PowerPointComObject $sourceTextFrame
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
      $typographyIssueObservations = @()
      $inspectionFailures = 0
      $shapeReadback = @()
      $backplateZOrderPositions = @()
      $nativeZOrderPositions = @()
      for ($slideIndex = 1; $slideIndex -le $deck.Slides.Count; $slideIndex += 1) {
        $shapes = $deck.Slides.Item($slideIndex).Shapes
        for ($shapeIndex = 1; $shapeIndex -le $shapes.Count; $shapeIndex += 1) {
          $shape = $shapes.Item($shapeIndex)
          $textFrame = $null
          $textRange = $null
          $textFont = $null
          $textLines = $null
          try {
            $hasTextFrame = [bool]$shape.HasTextFrame
            if ($hasTextFrame) { $textFrame = $shape.TextFrame2 }
            $hasText = $hasTextFrame -and [bool]$textFrame.HasText
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
              $textRange = $textFrame.TextRange
              $textFont = $textRange.Font
              $textLines = $textRange.Lines()
              $boundHeight = [double]$textRange.BoundHeight
              $boundWidth = [double]$textRange.BoundWidth
              $heightOverflow = $boundHeight - [double]$shape.Height
              $widthOverflow = $boundWidth - [double]$shape.Width
              $overflow = [Math]::Max($heightOverflow, $widthOverflow)
              if ($overflow -gt 5) { $typographyIssuesGt5Pt += 1 }
              if ($overflow -gt 10) { $typographyIssuesGt10Pt += 1 }
              if ($overflow -gt 5) {
                $typographyIssueObservations += [PSCustomObject]@{
                  SlideNumber = $slideIndex
                  Name = [string]$shape.Name
                  Text = [string]$textRange.Text
                  ShapeWidthPt = [double]$shape.Width
                  ShapeHeightPt = [double]$shape.Height
                  BoundWidthPt = $boundWidth
                  BoundHeightPt = $boundHeight
                  WidthOverflowPt = $widthOverflow
                  HeightOverflowPt = $heightOverflow
                  MaximumOverflowPt = $overflow
                }
              }
            }
            $textAutoSize = if ($hasText) {
              [int]$textFrame.AutoSize
            } else { $null }
            $textValue = if ($hasText) { [string]$textRange.Text } else { $null }
            $alternativeText = try {
              [string]$shape.AlternativeText
            } catch { "" }
            $semanticOwnership = Read-AuthoredShapeOwnership `
              $alternativeText $slideIndex ([string]$shape.Name) $shape
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
              OwnershipKind = if ($null -ne $semanticOwnership) {
                $semanticOwnership.Kind
              } else { $null }
              OwnershipSceneSlideId = if ($null -ne $semanticOwnership) {
                $semanticOwnership.SceneSlideId
              } else { $null }
              OwnershipCapabilityId = if ($null -ne $semanticOwnership) {
                $semanticOwnership.CapabilityId
              } else { $null }
              OwnershipDisposition = if ($null -ne $semanticOwnership) {
                $semanticOwnership.Disposition
              } else { $null }
              OwnershipSource = if ($null -ne $semanticOwnership) {
                $semanticOwnership.Source
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
              TextRenderedLineCount = if ($hasText) { [int]$textLines.Count } else { 0 }
              TextBoundWidthPt = if ($hasText) { [double]$textRange.BoundWidth } else { $null }
              TextBoundHeightPt = if ($hasText) { [double]$textRange.BoundHeight } else { $null }
              Typeface = if ($hasText) { [string]$textFont.Name } else { $null }
              EastAsianTypeface = if ($hasText) { [string]$textFont.NameFarEast } else { $null }
              LeftPt = [double]$shape.Left
              TopPt = [double]$shape.Top
              WidthPt = [double]$shape.Width
              HeightPt = [double]$shape.Height
              NativeDetail = Read-PowerPointNativeShapeDetail `
                $shape $hasText $hasTable $hasChart $shapeType $textAutoSize `
                $semanticOwnership
            }
          } catch {
            $inspectionFailures += 1
          } finally {
            Release-PowerPointComObject $textLines
            Release-PowerPointComObject $textFont
            if ($null -ne $textRange) {
              [Runtime.InteropServices.Marshal]::FinalReleaseComObject($textRange) | Out-Null
              $textRange = $null
            }
            Release-PowerPointComObject $textFrame
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
        OwnershipKind, OwnershipSceneSlideId, OwnershipCapabilityId, `
        OwnershipDisposition, OwnershipSource, `
        Type, ZOrderPosition, HasText, Text, `
        TextLineBreakCount, TextRenderedLineCount, TextBoundWidthPt, `
        TextBoundHeightPt, Typeface, EastAsianTypeface, `
        LeftPt, TopPt, WidthPt, HeightPt, NativeDetail)
      $shapeReadbackPreserved =
        ($sourceShapeReadback | ConvertTo-Json -Depth 12 -Compress) -eq
        ($reopenedShapeReadback | ConvertTo-Json -Depth 12 -Compress)
      $sourceChartReadback = @(
        $sourceShapeReadback |
          Where-Object { $null -ne $_.NativeDetail.Chart } |
          Select-Object SlideNumber, Name, @{ Name = "Chart"; Expression = { $_.NativeDetail.Chart } }
      )
      $reopenedChartReadback = @(
        $reopenedShapeReadback |
          Where-Object { $null -ne $_.NativeDetail.Chart } |
          Select-Object SlideNumber, Name, @{ Name = "Chart"; Expression = { $_.NativeDetail.Chart } }
      )
      $nativeChartReadbackPreserved =
        ($sourceChartReadback | ConvertTo-Json -Depth 12 -Compress) -eq
        ($reopenedChartReadback | ConvertTo-Json -Depth 12 -Compress)
      $sourceTableReadback = @(
        $sourceShapeReadback |
          Where-Object { $null -ne $_.NativeDetail.Table } |
          Select-Object SlideNumber, Name, @{ Name = "Table"; Expression = { $_.NativeDetail.Table } }
      )
      $reopenedTableReadback = @(
        $reopenedShapeReadback |
          Where-Object { $null -ne $_.NativeDetail.Table } |
          Select-Object SlideNumber, Name, @{ Name = "Table"; Expression = { $_.NativeDetail.Table } }
      )
      $nativeTableReadbackPreserved =
        ($sourceTableReadback | ConvertTo-Json -Depth 12 -Compress) -eq
        ($reopenedTableReadback | ConvertTo-Json -Depth 12 -Compress)
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
      $sourceOwnershipComparison = if ($null -ne $expectedOwnership) {
        Compare-ExpectedOwnership `
          $sourceShapeReadback $expectedOwnership.Entries $currentMode
      } else { $null }
      $reopenedOwnershipComparison = if ($null -ne $expectedOwnership) {
        Compare-ExpectedOwnership `
          $shapeReadback $expectedOwnership.Entries $currentMode
      } else { $null }
      $ownershipExpectationExact =
        $null -eq $expectedOwnership -or (
          $sourceOwnershipComparison.Exact -and
          $reopenedOwnershipComparison.Exact -and
          ($sourceOwnershipComparison.Observations | ConvertTo-Json -Depth 8 -Compress) -eq
          ($reopenedOwnershipComparison.Observations | ConvertTo-Json -Depth 8 -Compress)
        )
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
        $nativeChartReadbackPreserved -and
        $nativeTableReadbackPreserved -and
        $semanticOwnershipPreserved -and
        $ownershipExpectationExact -and
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
        NativeChartReadbackPreserved = $nativeChartReadbackPreserved
        NativeTableReadbackPreserved = $nativeTableReadbackPreserved
        SourceChartReadback = $sourceChartReadback
        ReopenedChartReadback = $reopenedChartReadback
        SourceTableReadback = $sourceTableReadback
        ReopenedTableReadback = $reopenedTableReadback
        SemanticOwnershipPreserved = $semanticOwnershipPreserved
        OwnershipExpectationExact = $ownershipExpectationExact
        ExpectedOwnershipManifestPath = if ($null -ne $expectedOwnership) {
          $expectedOwnership.Path
        } else { $null }
        ExpectedOwnershipManifestSha256 = if ($null -ne $expectedOwnership) {
          $expectedOwnership.Sha256
        } else { $null }
        FirstOpenOwnership = $sourceOwnershipComparison
        ReopenOwnership = $reopenedOwnershipComparison
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
        TypographyIssueObservations = $typographyIssueObservations
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
    ApplicationWindowBindingPhase = $applicationWindowBindingPhase
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
$report | ConvertTo-Json -Depth 14 | Set-Content -LiteralPath (
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
