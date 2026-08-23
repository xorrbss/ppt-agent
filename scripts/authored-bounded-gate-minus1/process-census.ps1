param(
  [Parameter(Mandatory = $true)][string]$OutputPath,
  [int[]]$OwnedProcessIds = @()
)

$ErrorActionPreference = 'Stop'
$processes = @(Get-CimInstance Win32_Process -Filter "Name='POWERPNT.EXE'" -ErrorAction SilentlyContinue | ForEach-Object {
  [ordered]@{
    processId = [int]$_.ProcessId
    parentProcessId = [int]$_.ParentProcessId
    creationDate = if ($_.CreationDate -is [datetime]) { $_.CreationDate.ToUniversalTime().ToString('yyyy-MM-ddTHH:mm:ss.fffZ') } elseif ($_.CreationDate) { ([Management.ManagementDateTimeConverter]::ToDateTime([string]$_.CreationDate)).ToUniversalTime().ToString('yyyy-MM-ddTHH:mm:ss.fffZ') } else { $null }
    executablePath = [string]$_.ExecutablePath
    commandLine = [string]$_.CommandLine
    ownedByAttempt = $OwnedProcessIds -contains [int]$_.ProcessId
  }
})

$payload = [ordered]@{
  schemaVersion = 'powerpoint-process-census/v1'
  capturedAt = (Get-Date).ToUniversalTime().ToString('yyyy-MM-ddTHH:mm:ss.fffZ')
  computerName = [Environment]::MachineName
  sessionId = [System.Diagnostics.Process]::GetCurrentProcess().SessionId
  processes = $processes
  processCount = $processes.Count
  ownedProcessIds = @($OwnedProcessIds | Sort-Object -Unique)
  unexpectedProcessIds = @($processes | Where-Object { -not $_.ownedByAttempt } | ForEach-Object { $_.processId })
}

$parent = Split-Path -Parent $OutputPath
if ($parent) { New-Item -ItemType Directory -Force -Path $parent | Out-Null }
$payload | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $OutputPath -Encoding utf8
$payload | ConvertTo-Json -Depth 8
