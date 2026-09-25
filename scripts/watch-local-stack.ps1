param(
    [string]$LogDirectory = (Join-Path $PSScriptRoot '../app_data/logs'),
    [int]$PollSeconds = 1
)
$ErrorActionPreference = 'Stop'
$components = @('api', 'next', 'proxy', 'worker-generation', 'worker-export')
$positions = @{}
$pending = @{}
$selected = @{}
$streams = @('stdout', 'stderr')
Write-Host 'Server: http://localhost:5000' -ForegroundColor Cyan
Write-Host 'Live logs: API / Next / proxy / generation / export'
Write-Host 'Ctrl+C stops this viewer only; servers keep running.'
& (Join-Path $PSScriptRoot 'start-local-stack.ps1') -Status

function Write-SafeLogLine([string]$Label, [string]$Line) {
    $safe = $Line -replace '(?i)(bearer\s+)[A-Za-z0-9._~+/=-]+', '$1[REDACTED]'
    $safe = $safe -replace '(?i)((?:authorization|cookie|set-cookie)\s*[:=]\s*).*', '$1[REDACTED]'
    $safe = $safe -replace '(?i)((?:password|passwd|api[_-]?key|access[_-]?token|refresh[_-]?token|client[_-]?secret)\s*[":= ]+)[^\s,;"}]+', '$1[REDACTED]'
    $safe = $safe -replace '(?i)([a-z][a-z0-9+.-]*://[^\s:/]+:)[^\s@]+@', '$1[REDACTED]@'
    if ($safe.Length -gt 0) { Write-Host "[$Label] $safe" }
}

while ($true) {
    foreach ($component in $components) {
        foreach ($streamName in $streams) {
            $label = "$component/$streamName"
            $latest = Get-ChildItem -LiteralPath $LogDirectory -Filter "$component-*.$streamName.log" -File -ErrorAction SilentlyContinue |
                Sort-Object LastWriteTimeUtc -Descending | Select-Object -First 1
            if ($null -eq $latest) { continue }
            $logPath = $latest.FullName
            if ($selected[$label] -ne $logPath) {
                $selected[$label] = $logPath
                $positions[$logPath] = $latest.Length
                $pending[$logPath] = ''
                Write-Host "[$label] Following $($latest.Name)" -ForegroundColor DarkCyan
                Get-Content -LiteralPath $logPath -Tail 8 -ErrorAction SilentlyContinue |
                    ForEach-Object { Write-SafeLogLine $label $_ }
                continue
            }
            $fileStream = $null
            $reader = $null
            try {
                $fileStream = [System.IO.File]::Open(
                    $logPath, [System.IO.FileMode]::Open, [System.IO.FileAccess]::Read,
                    ([System.IO.FileShare]::ReadWrite -bor [System.IO.FileShare]::Delete)
                )
                if ($fileStream.Length -lt $positions[$logPath]) {
                    $positions[$logPath] = 0
                    $pending[$logPath] = ''
                }
                if ($fileStream.Length -eq $positions[$logPath]) { continue }
                $fileStream.Position = $positions[$logPath]
                $reader = [System.IO.StreamReader]::new($fileStream, [System.Text.Encoding]::UTF8, $true)
                $chunk = $reader.ReadToEnd()
                $positions[$logPath] = $fileStream.Position
                $lines = ($pending[$logPath] + $chunk) -split "\r?\n"
                $pending[$logPath] = $lines[-1]
                for ($index = 0; $index -lt $lines.Length - 1; $index++) {
                    Write-SafeLogLine $label $lines[$index]
                }
            } catch [System.IO.IOException] {
                # A managed restart can rotate the file between discovery and open.
            } finally {
                if ($null -ne $reader) { $reader.Dispose() }
                elseif ($null -ne $fileStream) { $fileStream.Dispose() }
            }
        }
    }
    Start-Sleep -Seconds ([Math]::Max(1, $PollSeconds))
}

