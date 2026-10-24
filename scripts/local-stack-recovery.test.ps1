param(
    [string]$PostgresBin = "C:\Program Files\PostgreSQL\15\bin",
    [int]$Port = 21099,
    [int]$TimeoutPort = 21098,
    [string]$LiveRepoRoot
)

$ErrorActionPreference = "Stop"
$env:PYTHONDONTWRITEBYTECODE = "1"
. (Join-Path $PSScriptRoot "local-stack-lib.ps1")
if ($Port -in @(21000, 5000, 8000, 8001, 3000) -or $TimeoutPort -in @(21000, 5000, 8000, 8001, 3000) -or $Port -eq $TimeoutPort) {
    throw "Recovery test requires distinct throwaway ports, never live stack ports"
}
foreach ($testPort in @($Port, $TimeoutPort)) {
    if ($null -ne (Get-PortOwner $testPort)) { throw "Test port $testPort is already in use" }
}
$tempRoot = [IO.Path]::GetFullPath([IO.Path]::GetTempPath())
$cluster = Join-Path $tempRoot ("local-stack-recovery-" + [Guid]::NewGuid().ToString())
$cluster = [IO.Path]::GetFullPath($cluster)
if (-not $cluster.StartsWith($tempRoot, [StringComparison]::OrdinalIgnoreCase)) {
    throw "Throwaway cluster must stay under TEMP"
}
$previousPgOptions = [Environment]::GetEnvironmentVariable("PGOPTIONS")
$server = $null
$results = [ordered]@{ shell = $PSVersionTable.PSVersion.ToString() }

function Invoke-TestPostgresCommand([string]$Executable, [string]$Arguments) {
    $result = Invoke-CapturedCommand (Join-Path $PostgresBin $Executable) $Arguments $tempRoot
    if ($result.ExitCode -ne 0) {
        throw "Test command $Executable failed with exit $($result.ExitCode)"
    }
    return $result
}

try {
    $env:PGOPTIONS = "-c default_transaction_read_only=off"
    [void](Invoke-TestPostgresCommand "initdb.exe" "-D `"$cluster`" -A trust -U postgres --no-locale -E UTF8")
    $serverArguments = @("-D", "`"$cluster`"", "-h", "127.0.0.1", "-p", "$Port", "-c", "checkpoint_timeout=1h", "-c", "max_wal_size=1GB")
    $server = Start-Process -FilePath (Join-Path $PostgresBin "postgres.exe") -ArgumentList $serverArguments `
        -RedirectStandardOutput (Join-Path $cluster "stdout.log") -RedirectStandardError (Join-Path $cluster "stderr.log") `
        -PassThru -WindowStyle Hidden
    Wait-ForPostgresReady $PostgresBin $Port
    $connection = "-X -w -h 127.0.0.1 -p $Port -U postgres -d postgres -v ON_ERROR_STOP=1"
    [void](Invoke-TestPostgresCommand "psql.exe" "$connection -c `"CREATE TABLE recovery_test AS SELECT i, repeat(md5(i::text), 20) AS payload FROM generate_series(1, 400000) AS i`"")
    [void](Invoke-TestPostgresCommand "pg_ctl.exe" "-D `"$cluster`" -w -t 30 stop -m immediate")

    $env:PGOPTIONS = "-c default_transaction_read_only=on"
    $timer = [Diagnostics.Stopwatch]::StartNew()
    $server = Start-Process -FilePath (Join-Path $PostgresBin "postgres.exe") -ArgumentList $serverArguments `
        -RedirectStandardOutput (Join-Path $cluster "recovery.stdout.log") -RedirectStandardError (Join-Path $cluster "recovery.stderr.log") `
        -PassThru -WindowStyle Hidden
    [void](Wait-ForListener $Port 60)
    $results.listenerMilliseconds = $timer.ElapsedMilliseconds
    $probe = Invoke-CapturedCommand (Join-Path $PostgresBin "pg_isready.exe") "-q -h 127.0.0.1 -p $Port -t 1" $PostgresBin
    $results.probeAfterListenerExit = $probe.ExitCode
    Wait-ForPostgresReady $PostgresBin $Port
    $results.acceptingMilliseconds = $timer.ElapsedMilliseconds
    $query = Invoke-TestPostgresCommand "psql.exe" "$connection -At -c `"SELECT 1, current_setting('transaction_read_only'), count(*) FROM recovery_test`""
    if ($query.Stdout.Trim() -ne "1|on|400000") { throw "Read-only query after readiness returned unexpected data" }
    $results.query = $query.Stdout.Trim()
    $results.queryMilliseconds = $timer.ElapsedMilliseconds
    $recoveryLog = Get-Content -LiteralPath (Join-Path $cluster "recovery.stderr.log") -Raw
    if ($recoveryLog -notmatch 'database system was not properly shut down' -or $recoveryLog -notmatch 'ready to accept connections') {
        throw "PostgreSQL log did not confirm crash recovery and readiness"
    }
    $results.crashRecoveryConfirmed = $true

    $timer.Restart()
    $failure = ""
    try { Wait-ForPostgresReady $PostgresBin $TimeoutPort 2 } catch { $failure = $_.Exception.Message }
    $results.timeoutMilliseconds = $timer.ElapsedMilliseconds
    $results.timeout = $failure
    if ($failure -notmatch '^postgres_readiness_timeout:' -or $results.timeoutMilliseconds -gt 3500) {
        throw "Readiness timeout was not named and bounded"
    }

    if ($LiveRepoRoot) {
        # Load only function definitions from this worktree; execute no lifecycle
        # entrypoint and point the read-only status view at the existing live state.
        $tokens = $null; $parseErrors = $null
        $ast = [Management.Automation.Language.Parser]::ParseFile(
            (Join-Path $PSScriptRoot "start-local-stack.ps1"), [ref]$tokens, [ref]$parseErrors)
        if ($parseErrors.Count -ne 0) { throw "Stack script failed to parse" }
        foreach ($definition in $ast.FindAll({ param($node)
            $node -is [Management.Automation.Language.FunctionDefinitionAst]
        }, $false)) { Invoke-Expression $definition.Extent.Text }
        $repoRoot = $LiveRepoRoot
        $fastApi = Join-Path $repoRoot "servers\fastapi"
        $statePath = Join-Path $repoRoot "app_data\public-v2-local\local-stack.json"
        $DatabasePort = 21000; $ApiPort = 8000; $NextPort = 3000; $ProxyPort = 5000
        $postgresBin = $PostgresBin
        $statusLines = @(Invoke-Status 6>&1 | ForEach-Object { $_.ToString() })
        $postgresStatus = $statusLines | Where-Object { $_ -match '^postgres\s' }
        if ($postgresStatus -notmatch 'accepting connections') { throw "Modified status did not confirm live PostgreSQL readiness" }
        $results.liveStatus = @($statusLines)
    }
} finally {
    if ($null -ne $previousPgOptions) { $env:PGOPTIONS = $previousPgOptions }
    else { Remove-Item Env:PGOPTIONS -ErrorAction SilentlyContinue }
    if ($null -ne $server) {
        $stop = Invoke-CapturedCommand (Join-Path $PostgresBin "pg_ctl.exe") "-D `"$cluster`" -w -t 30 stop -m fast" $tempRoot
        if ($stop.ExitCode -ne 0 -and -not $server.HasExited) { throw "Throwaway PostgreSQL did not stop; directory preserved at $cluster" }
        $server.WaitForExit()
    }
    if (Test-Path -LiteralPath $cluster) {
        $resolvedCluster = (Resolve-Path -LiteralPath $cluster).Path
        if ($resolvedCluster -ne $cluster -or -not $resolvedCluster.StartsWith($tempRoot, [StringComparison]::OrdinalIgnoreCase)) {
            throw "Refusing cleanup outside the verified throwaway directory"
        }
        Remove-Item -LiteralPath $resolvedCluster -Recurse -Force
    }
    $results.clusterRemoved = -not (Test-Path -LiteralPath $cluster)
    $results.portReleased = $null -eq (Get-PortOwner $Port)
}
$results | ConvertTo-Json -Depth 4
