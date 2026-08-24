param(
  [Parameter(Mandatory = $true)][string]$OutputPath,
  [Parameter(Mandatory = $true)][string]$StopSignalPath,
  [Parameter(Mandatory = $true)][string]$StartedSignalPath,
  [int]$PollMilliseconds = 50
)

$ErrorActionPreference = 'Stop'
Add-Type @'
using System;
using System.Collections.Generic;
using System.Runtime.InteropServices;
using System.Text;

public static class GateMinus1WindowProbe {
  public delegate bool EnumWindowsProc(IntPtr hwnd, IntPtr lParam);
  [DllImport("user32.dll")] public static extern bool EnumWindows(EnumWindowsProc callback, IntPtr lParam);
  [DllImport("user32.dll")] public static extern bool IsWindowVisible(IntPtr hwnd);
  [DllImport("user32.dll", CharSet = CharSet.Unicode)] public static extern int GetWindowText(IntPtr hwnd, StringBuilder text, int maxCount);
  [DllImport("user32.dll", CharSet = CharSet.Unicode)] public static extern int GetClassName(IntPtr hwnd, StringBuilder text, int maxCount);
  [DllImport("user32.dll")] public static extern uint GetWindowThreadProcessId(IntPtr hwnd, out uint processId);

  public static List<string[]> Snapshot() {
    var rows = new List<string[]>();
    EnumWindows(delegate(IntPtr hwnd, IntPtr state) {
      if (!IsWindowVisible(hwnd)) return true;
      var title = new StringBuilder(1024);
      var cls = new StringBuilder(256);
      GetWindowText(hwnd, title, title.Capacity);
      GetClassName(hwnd, cls, cls.Capacity);
      uint pid;
      GetWindowThreadProcessId(hwnd, out pid);
      rows.Add(new [] { hwnd.ToInt64().ToString(), pid.ToString(), cls.ToString(), title.ToString() });
      return true;
    }, IntPtr.Zero);
    return rows;
  }
}
'@

$startedAt = (Get-Date).ToUniversalTime().ToString('yyyy-MM-ddTHH:mm:ss.fffZ')
$desktopIdentity = [ordered]@{
  computerName = [Environment]::MachineName
  userName = [Environment]::UserName
  sessionId = [System.Diagnostics.Process]::GetCurrentProcess().SessionId
  sessionName = [Environment]::GetEnvironmentVariable('SESSIONNAME')
  userInteractive = [Environment]::UserInteractive
}
$events = [System.Collections.Generic.List[object]]::new()
$seen = @{}
$pollCount = 0
$startedAt | Set-Content -LiteralPath $StartedSignalPath -Encoding ascii

while (-not (Test-Path -LiteralPath $StopSignalPath)) {
  $pollCount += 1
  $now = (Get-Date).ToUniversalTime().ToString('yyyy-MM-ddTHH:mm:ss.fffZ')
  $powerPointIds = @(Get-Process POWERPNT -ErrorAction SilentlyContinue | ForEach-Object { $_.Id })
  foreach ($row in [GateMinus1WindowProbe]::Snapshot()) {
    $windowHandle = [long]$row[0]
    $processId = [int]$row[1]
    $className = $row[2]
    $title = $row[3]
    $isPowerPoint = $powerPointIds -contains $processId
    $isDialog = $className -eq '#32770'
    $repairSuspect = $isDialog -and ($isPowerPoint -or $title -match '(?i)repair|recovered|복구|문제가|읽을 수|오류|PowerPoint')
    if (($isPowerPoint -or $repairSuspect) -and -not $seen.ContainsKey($windowHandle)) {
      $seen[$windowHandle] = $true
      $events.Add([ordered]@{
        observedAt = $now
        hwnd = $windowHandle
        processId = $processId
        className = $className
        title = $title
        isPowerPointProcess = $isPowerPoint
        isDialog = $isDialog
        repairSuspect = $repairSuspect
      })
    }
  }
  Start-Sleep -Milliseconds $PollMilliseconds
}

$payload = [ordered]@{
  schemaVersion = 'attended-window-event-log/v1'
  watcherStartedAt = $startedAt
  watcherStoppedAt = (Get-Date).ToUniversalTime().ToString('yyyy-MM-ddTHH:mm:ss.fffZ')
  desktopIdentity = $desktopIdentity
  pollMilliseconds = $PollMilliseconds
  pollCount = $pollCount
  events = @($events)
  repairDialogEvents = @($events | Where-Object { $_.repairSuspect })
  autoDismissAttempted = $false
}
$parent = Split-Path -Parent $OutputPath
if ($parent) { New-Item -ItemType Directory -Force -Path $parent | Out-Null }
$payload | ConvertTo-Json -Depth 10 | Set-Content -LiteralPath $OutputPath -Encoding utf8
