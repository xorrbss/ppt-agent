[CmdletBinding()]
param([switch]$KeepDatabase)

$ErrorActionPreference = 'Stop'
$runtime = Join-Path (Split-Path -Parent $PSScriptRoot) 'app_data\public-v2-local'
$manifest = Get-Content -Raw -LiteralPath (Join-Path $runtime 'runtime.json') | ConvertFrom-Json
if ([System.IO.Path]::GetFullPath($manifest.runtimeRoot) -ne [System.IO.Path]::GetFullPath($runtime) -or $manifest.mode -ne 'manual') {
    throw 'Local runtime ownership manifest mismatch.'
}
$snapshot = @(Get-CimInstance Win32_Process)
$owned = [System.Collections.Generic.HashSet[int]]::new()
$pending = [System.Collections.Generic.Queue[int]]::new()
foreach ($entry in $manifest.processes) {
    $process = $snapshot | Where-Object { $_.ProcessId -eq $entry.id } | Select-Object -First 1
    if ($null -ne $process -and $process.CreationDate.ToUniversalTime().Ticks -eq ([DateTime]$entry.startedAt).ToUniversalTime().Ticks) {
        if ($owned.Add([int]$entry.id)) { $pending.Enqueue([int]$entry.id) }
    }
}
while ($pending.Count -gt 0) {
    $parent = $pending.Dequeue()
    foreach ($child in @($snapshot | Where-Object { $_.ParentProcessId -eq $parent })) {
        if ($owned.Add([int]$child.ProcessId)) { $pending.Enqueue([int]$child.ProcessId) }
    }
}
foreach ($id in $owned) { Stop-Process -Id $id -Force -ErrorAction SilentlyContinue }
$data = Join-Path $runtime 'postgres-data'
$listener = Get-NetTCPConnection -State Listen -LocalPort $manifest.databasePort -ErrorAction SilentlyContinue | Select-Object -First 1
if ($null -ne $listener -and -not $KeepDatabase) {
    $postgres = Get-CimInstance Win32_Process -Filter "ProcessId=$($listener.OwningProcess)"
    if ($postgres.Name -ne 'postgres.exe' -or $postgres.CommandLine.Replace('\', '/') -notlike "*$($data.Replace('\', '/'))*") {
        throw 'PostgreSQL listener is not owned by this local runtime; left untouched.'
    }
    $stop = Start-Process -FilePath 'C:\Program Files\PostgreSQL\15\bin\pg_ctl.exe' -ArgumentList @('-D', $data, '-w', '-t', '30', 'stop', '-m', 'fast') -Wait -PassThru -WindowStyle Hidden
    if ($stop.ExitCode -ne 0) { throw 'PostgreSQL graceful shutdown failed; data was not removed.' }
}
Write-Output 'Local application processes stopped. PostgreSQL data and credentials retained.'
