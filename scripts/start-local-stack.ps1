<#
.SYNOPSIS
    Bring the Windows-native local stack up or down as one supervised unit.

.DESCRIPTION
    This is the single supported way to run the local stack. Before it existed,
    the daily stack was assembled by hand from two other launchers: the owned
    PostgreSQL topology of `start-public-v2-rc.ps1` on the ports of
    `start-authored-vnext-dev.ps1`, with each process's environment living only
    in the shell that started it. That combination belonged to neither script,
    so it could not be restarted without reading a running process's PEB, had no
    stop command, and let the code and the database drift apart in silence.

    Components, started in dependency order and stopped in reverse:

        PostgreSQL 21000  ->  FastAPI 8000  ->  generation controller
                                              ->  export controller
                          ->  Next dev 3000  ->  single-origin proxy 5000

    The two controllers are not duplicates: they claim different workspace
    operation kinds through WORKSPACE_OPERATION_EXTERNAL_CONTROLLER.

    Unlike `start-public-v2-rc.ps1`, the PostgreSQL here is *persistent* -- it is
    the database under app_data that holds real decks, not a per-run initdb in
    the temp directory. This script therefore never runs initdb; a missing data
    directory is an error, not an invitation to create an empty one.

.PARAMETER Stop
    Stop everything this script started, in reverse dependency order.

.PARAMETER Status
    Report what is running without changing anything.

.PARAMETER ImportEnvironment
    Seed the DPAPI-protected environment store from a directory of JSON files
    (api.json, worker-generation.json, worker-export.json, next.json,
    proxy.json). Needed once; after that the store is the source of truth.

.PARAMETER KeepDatabase
    With -Stop, leave PostgreSQL running. Useful when another tool is querying it.
#>

[CmdletBinding()]
param(
    [switch]$Stop,
    [ValidateSet("api", "worker-generation", "worker-export")]
    [string[]]$RestartComponent,
    [switch]$Status,
    [switch]$Adopt,
    [string]$ImportEnvironment,
    [switch]$KeepDatabase,
    [int]$ApiPort = 8000,
    [int]$NextPort = 3000,
    [int]$ProxyPort = 5000,
    [int]$DatabasePort = 21000
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

. (Join-Path $PSScriptRoot "local-stack-lib.ps1")

$repoRoot = Split-Path -Parent $PSScriptRoot
$fastApi = Join-Path $repoRoot "servers\fastapi"
$nextJs = Join-Path $repoRoot "servers\nextjs"
$appData = Join-Path $repoRoot "app_data"
$runtimeRoot = Join-Path $appData "public-v2-local"
$dataDirectory = Join-Path $runtimeRoot "postgres-data"
$environmentStore = Join-Path $runtimeRoot "local-stack-env.dpapi.xml"
$statePath = Join-Path $runtimeRoot "local-stack.json"
$logs = Join-Path $appData "logs"
$python = Join-Path $fastApi ".venv\Scripts\python.exe"
$postgresBin = "C:\Program Files\PostgreSQL\15\bin"

# The component order is the dependency order. Everything else in this script
# reads it forwards to start and backwards to stop, so there is one place where
# the topology is written down.
$componentOrder = @("postgres", "api", "worker-generation", "worker-export", "next", "proxy")

function Read-State {
    if (-not (Test-Path -LiteralPath $statePath)) { return @{} }
    $state = @{}
    foreach ($property in (Get-Content -LiteralPath $statePath -Raw | ConvertFrom-Json).PSObject.Properties) {
        $state[$property.Name] = $property.Value
    }
    return $state
}

function Write-State([hashtable]$State) {
    $State | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath $statePath -Encoding utf8
}

function Assert-PostgreSqlRuntime([hashtable]$Environments) {
    foreach ($component in @("api", "worker-generation", "worker-export")) {
        if (-not $Environments.ContainsKey($component)) {
            throw "local stack: missing environment for $component"
        }
        $componentEnvironment = $Environments[$component]
        $databaseUrl = if ($componentEnvironment.ContainsKey("DATABASE_URL")) {
            [string]$componentEnvironment["DATABASE_URL"]
        } else {
            ""
        }
        if ($databaseUrl -notmatch '^postgresql(?:\+[^:]+)?://') {
            throw "local stack: $component DATABASE_URL must use PostgreSQL; SQLite fallback is forbidden"
        }
    }
}

function Import-EnvironmentDirectory([string]$Directory) {
    $components = @{}
    $files = @{
        "api"               = "api.json"
        "worker-generation" = "worker-generation.json"
        "worker-export"     = "worker-export.json"
        "next"              = "next.json"
        "proxy"             = "proxy.json"
    }
    foreach ($entry in $files.GetEnumerator()) {
        $path = Join-Path $Directory $entry.Value
        if (-not (Test-Path -LiteralPath $path)) { throw "local stack: $path is missing" }
        $map = @{}
        foreach ($property in (Get-Content -LiteralPath $path -Raw | ConvertFrom-Json).PSObject.Properties) {
            # npm and terminal integrations inject per-shell noise that must not
            # be replayed into a detached service.
            if ($property.Name -like "npm_*" -or $property.Name -like "WARP_*" -or $property.Name -like "CLAUDE*") { continue }
            $map[$property.Name] = [string]$property.Value
        }
        $components[$entry.Key] = $map
    }
    Save-StackEnvironment -Components $components -Path $environmentStore
    Write-Host "environment store written: $environmentStore ($($components.Count) components)"
}

<#
    Report how far the database is behind the code.

    This is the check whose absence let the stack run for days against a schema
    older than the code, surfacing only as an unrelated-looking warning about a
    capped generation deadline. It reports and never migrates: applying the
    pending set is a decision with its own consequences.
#>
function Show-MigrationDrift([hashtable]$ApiEnvironment) {
    try {
        $scoped = @{
            "DATABASE_URL"        = $ApiEnvironment["DATABASE_URL"]
            "APP_DATA_DIRECTORY"  = $ApiEnvironment["APP_DATA_DIRECTORY"]
            "USER_CONFIG_PATH"    = $ApiEnvironment["USER_CONFIG_PATH"]
        }
        $previous = @{}
        foreach ($entry in $scoped.GetEnumerator()) {
            $current = Get-Item -LiteralPath "Env:$($entry.Key)" -ErrorAction SilentlyContinue
            if ($null -eq $current) { $previous[$entry.Key] = $null } else { $previous[$entry.Key] = $current.Value }
            Set-Item -LiteralPath "Env:$($entry.Key)" -Value ([string]$entry.Value)
        }
        try {
            # alembic.ini sets a *relative* script_location, so this only
            # resolves from the FastAPI directory. Run it anywhere else and it
            # fails -- and a failure that gets filtered away would report a
            # stale database as "up to date", which is the exact blindness this
            # check exists to remove. The exit code is therefore checked before
            # the output is believed.
            # alembic logs its progress on stderr. Windows PowerShell wraps a
            # native command's stderr in ErrorRecords, so merging it with 2>&1
            # under ErrorActionPreference='Stop' turns ordinary INFO lines into
            # a terminating error. Discard stderr and trust the exit code.
            Push-Location $fastApi
            try {
                $previousPreference = $ErrorActionPreference
                $ErrorActionPreference = "Continue"
                $output = @(& $python -m alembic history -r "current:heads" 2>$null)
                $alembicExit = $LASTEXITCODE
                $ErrorActionPreference = $previousPreference
            } finally {
                Pop-Location
            }
            if ($alembicExit -ne 0) {
                Write-Warning "schema: drift check could not run (alembic exit $alembicExit). The database version is UNKNOWN."
                return
            }
            # `history -r current:heads` is inclusive of the current revision,
            # so it also lists the edge *into* what the database already has.
            # Drop it, or the report claims one more pending migration than
            # there is.
            Push-Location $fastApi
            try {
                $ErrorActionPreference = "Continue"
                $applied = @(& $python -m alembic current 2>$null) -join " "
                $ErrorActionPreference = "Stop"
            } finally {
                Pop-Location
            }
            $appliedRevision = ""
            if ($applied -match "([0-9a-f]{12})") { $appliedRevision = $Matches[1] }
            $pending = @($output | Where-Object {
                $_ -notmatch "^INFO" -and $_ -match "->" -and
                ($appliedRevision -eq "" -or $_ -notmatch "->\s*$appliedRevision")
            })
        } finally {
            foreach ($entry in $previous.GetEnumerator()) {
                if ($null -eq $entry.Value) { Remove-Item -LiteralPath "Env:$($entry.Key)" -ErrorAction SilentlyContinue }
                else { Set-Item -LiteralPath "Env:$($entry.Key)" -Value $entry.Value }
            }
        }
        if ($pending.Count -eq 0) {
            Write-Host "  schema: up to date with the code"
            return
        }
        Write-Warning "schema: the database is $($pending.Count) migration(s) behind the code."
        foreach ($line in $pending) { Write-Host "    $line" }
        Write-Host "  MIGRATE_DATABASE_ON_STARTUP is '$($ApiEnvironment['MIGRATE_DATABASE_ON_STARTUP'])', so none of these are applied."
    } catch {
        Write-Warning "schema: drift check failed ($($_.Exception.Message)). Continuing."
    }
}

function Start-PersistentPostgres {
    $owner = Get-PortOwner $DatabasePort
    if ($null -ne $owner) {
        Write-Host "  postgres: already listening on $DatabasePort (PID $owner), adopting"
        return @{ ProcessId = $owner; Started = $false }
    }
    if (-not (Test-Path -LiteralPath $dataDirectory)) {
        throw "local stack: no PostgreSQL data directory at $dataDirectory. This script never runs initdb -- it supervises the persistent database, it does not create one."
    }
    $server = Start-Process -FilePath (Join-Path $postgresBin "postgres.exe") `
        -ArgumentList @("-D", $dataDirectory, "-h", "127.0.0.1", "-p", "$DatabasePort") `
        -RedirectStandardOutput (Join-Path $logs "postgres.stdout.log") `
        -RedirectStandardError (Join-Path $logs "postgres.stderr.log") `
        -PassThru -WindowStyle Hidden
    [void](Wait-ForListener $DatabasePort 60)
    Write-Host "  postgres: started (PID $($server.Id)) on $DatabasePort"
    return @{ ProcessId = $server.Id; Started = $true }
}

<#
    The two controllers run the same command line and bind no port, so nothing
    observable from outside tells them apart -- only the environment they were
    started with does. This script knows the roles when it starts them; when
    adopting a stack someone else started, it can only see that N controllers
    are up. Status therefore reports them as a group rather than inventing a
    role assignment it cannot verify.
#>
function Find-ControllerPids {
    $matched = @{}
    foreach ($process in @(Get-CimInstance Win32_Process -Filter "Name='python.exe'" -ErrorAction SilentlyContinue)) {
        if ($null -eq $process.CommandLine) { continue }
        if ($process.CommandLine -match "workspace_operation_worker") { $matched[[int]$process.ProcessId] = [int]$process.ParentProcessId }
    }
    # The .venv launcher stub and the interpreter it spawns both carry the
    # module on their command line, so a naive match double-counts every
    # controller. Keep the root of each pair.
    $roots = @()
    foreach ($entry in $matched.GetEnumerator()) {
        if (-not $matched.ContainsKey($entry.Value)) { $roots += $entry.Key }
    }
    return @($roots | Sort-Object)
}

function Invoke-Status {
    $state = Read-State
    $ports = @{ "postgres" = $DatabasePort; "api" = $ApiPort; "next" = $NextPort; "proxy" = $ProxyPort }
    foreach ($component in @("postgres", "api", "next", "proxy")) {
        $recorded = "-"
        if ($state.ContainsKey($component)) { $recorded = "$($state[$component])" }
        $live = Get-PortOwner $ports[$component]
        $description = "not running"
        if ($null -ne $live) {
            $description = "listening (PID $live)"
            # The recorded PID is the top of the launcher chain, which is what
            # -Stop needs; the listening PID is the leaf it spawned. Showing
            # both stops the difference from reading as a mismatch.
            if ($recorded -ne "-" -and [int]$recorded -ne [int]$live) { $description += ", supervised root $recorded" }
            if ($recorded -eq "-") { $description += "  [unsupervised -- run -Adopt to manage it]" }
        }
        "{0,-18} recorded={1,-8} {2}" -f $component, $recorded, $description | Write-Host
    }
    $controllers = @(Find-ControllerPids)
    $recordedControllers = @()
    foreach ($component in @("worker-generation", "worker-export")) {
        if ($state.ContainsKey($component)) { $recordedControllers += $state[$component] }
    }
    $suffix = ""
    if ($recordedControllers.Count -eq 0 -and $controllers.Count -gt 0) { $suffix = "  [unsupervised -- run -Adopt to manage them]" }
    "{0,-18} recorded={1,-8} {2}" -f "controllers", $recordedControllers.Count, "$($controllers.Count)/2 running (PIDs $($controllers -join ', '))$suffix" | Write-Host
}

<#
    Take ownership of a stack that is already up, so it can be stopped and
    restarted through this script without being killed by hand first. This is
    the one-time migration path off the hand-started stack.

    A listening PID is the leaf of a launcher chain (npm.cmd above Next, the
    .venv python stub above the API), and stopping only the leaf leaves the port
    held. Climb to the top of the chain -- but never past a process whose
    command line stops naming this repository, so this can never walk up into
    the user's shell.
#>
function Get-StackRootPid([int]$LeafPid) {
    $current = $LeafPid
    for ($hop = 0; $hop -lt 6; $hop++) {
        $process = Get-CimInstance Win32_Process -Filter "ProcessId=$current" -ErrorAction SilentlyContinue
        if ($null -eq $process) { break }
        $parent = Get-CimInstance Win32_Process -Filter "ProcessId=$($process.ParentProcessId)" -ErrorAction SilentlyContinue
        if ($null -eq $parent -or $null -eq $parent.CommandLine) { break }
        if ($parent.CommandLine -notlike "*$repoRoot*") { break }
        $current = [int]$parent.ProcessId
    }
    return $current
}

function Invoke-Adopt {
    $state = @{}
    $ports = @{ "postgres" = $DatabasePort; "api" = $ApiPort; "next" = $NextPort; "proxy" = $ProxyPort }
    foreach ($component in @("postgres", "api", "next", "proxy")) {
        $live = Get-PortOwner $ports[$component]
        if ($null -eq $live) {
            Write-Warning "adopt: $component is not running; it will be started on the next run"
            continue
        }
        if ($component -eq "postgres") { $state[$component] = $live }
        else { $state[$component] = Get-StackRootPid $live }
        Write-Host "  ${component}: adopted (PID $($state[$component]))"
    }
    $controllers = @(Find-ControllerPids)
    if ($controllers.Count -ne 2) {
        Write-Warning "adopt: found $($controllers.Count) controller(s), expected 2"
    }
    # Role cannot be recovered from outside the process, and -Stop does not need
    # it. The slots are filled in discovery order purely so the stop path has
    # both PIDs; the roles are re-established the next time this script starts them.
    $slots = @("worker-generation", "worker-export")
    for ($index = 0; $index -lt $controllers.Count -and $index -lt $slots.Count; $index++) {
        $state[$slots[$index]] = Get-StackRootPid $controllers[$index]
        Write-Host "  $($slots[$index]): adopted (PID $($state[$slots[$index]])) [role not verifiable from outside]"
    }
    Write-State $state
    Write-Host "adopted. stop with: .\scripts\start-local-stack.ps1 -Stop"
}

function Invoke-Stop {
    $state = Read-State
    [array]::Reverse($componentOrder)
    foreach ($component in $componentOrder) {
        if ($component -eq "postgres" -and $KeepDatabase) {
            Write-Host "  postgres: left running (-KeepDatabase)"
            continue
        }
        if (-not $state.ContainsKey($component)) { continue }
        $componentPid = [int]$state[$component]
        if ($component -eq "postgres") {
            # pg_ctl is the clean path; it flushes and releases the data directory.
            & (Join-Path $postgresBin "pg_ctl.exe") -D $dataDirectory -w -t 30 stop -m fast 2>&1 | Out-Null
            Write-Host "  postgres: stopped"
            continue
        }
        Stop-ProcessTree @($componentPid)
        Write-Host "  ${component}: stopped (PID $componentPid)"
    }
    Remove-Item -LiteralPath $statePath -ErrorAction SilentlyContinue
    Write-Host "local stack stopped."
}

function Invoke-RestartComponents {
    $environments = Read-StackEnvironment $environmentStore
    Assert-PostgreSqlRuntime $environments
    $state = Read-State
    $targets = @($RestartComponent | Select-Object -Unique)
    $owned = @{}
    $creationTimes = @{}
    foreach ($component in $targets) {
        if (-not $state.ContainsKey($component)) { throw "local stack: component owner is not recorded" }
        $recorded = [int]$state[$component]
        if ($recorded -le 0) { throw "local stack: invalid component owner" }
        $process = Get-CimInstance Win32_Process -Filter "ProcessId=$recorded" -ErrorAction Stop
        if ($component -eq "api" -and $null -eq $process -and $null -eq (Get-PortOwner $ApiPort)) {
            $owned[$component] = 0
            $creationTimes[$component] = $null
            continue
        }
        if ($null -eq $process -or -not [string]::Equals($process.ExecutablePath, $python, [StringComparison]::OrdinalIgnoreCase)) { throw "local stack: executable ownership mismatch" }
        $expected = if ($component -eq "api") { 'server\.py\s+--port\s+' + $ApiPort + '(?:\s|$)' } else { '-m\s+services\.workspace_operation_worker(?:\s|$)' }
        if ($process.CommandLine -notmatch $expected) { throw "local stack: command ownership mismatch" }
        $tree = @(Get-DescendantPids @($recorded))
        foreach ($other in $state.Keys) {
            if ($other -ne $component -and [int]$state[$other] -in $tree) { throw "local stack: component ownership overlaps another component" }
        }
        if ($component -eq "api") {
            $listener = Get-PortOwner $ApiPort
            if ($null -eq $listener -or [int]$listener -notin $tree) { throw "local stack: API listener ownership mismatch" }
        }
        $owned[$component] = $recorded
        $creationTimes[$component] = $process.CreationDate
    }
    $stamp = Get-Date -Format "yyyyMMdd-HHmmss-fff"
    foreach ($component in $targets) {
        if ([int]$owned[$component] -gt 0) {
            $process = Get-CimInstance Win32_Process -Filter "ProcessId=$($owned[$component])" -ErrorAction Stop
            if ($null -eq $process -or $process.CreationDate -ne $creationTimes[$component]) { throw "local stack: component ownership changed before restart" }
            Stop-ProcessTree @([int]$owned[$component])
        }
        $arguments = if ($component -eq "api") { @("server.py", "--port", "$ApiPort", "--reload", "false") } else { @("-m", "services.workspace_operation_worker") }
        $restarted = Start-LoggedProcess -FilePath $python -ArgumentList $arguments -WorkingDirectory $fastApi -Environment $environments[$component] -StdoutPath (Join-Path $logs "$component-$stamp.stdout.log") -StderrPath (Join-Path $logs "$component-$stamp.stderr.log")
        $state[$component] = $restarted.Id
        Write-State $state
        if ($component -eq "api") { Wait-ForHttp200 "http://127.0.0.1:$ApiPort/api/v1/auth/status" }
        Write-Host "  $component restarted (PID $($restarted.Id))"
    }
}

function Invoke-Start {
    $environments = Read-StackEnvironment $environmentStore
    Assert-PostgreSqlRuntime $environments
    if (-not (Test-Path -LiteralPath $logs)) { New-Item -ItemType Directory -Path $logs -Force | Out-Null }

    foreach ($port in @($ApiPort, $NextPort, $ProxyPort)) {
        $owner = Get-PortOwner $port
        if ($null -ne $owner) { throw "local stack: port $port already has a listener (PID $owner). Stop it first, or run -Stop." }
    }

    $state = @{}
    $stamp = Get-Date -Format "yyyyMMdd-HHmmss"

    # Record what is running even when a later component fails to come up.
    # The state file used to be written only after every component was ready,
    # so a failure part-way through (an API that refuses to start on a schema
    # mismatch, a port that never listens) left everything already started as
    # an untracked orphan: -Stop could not see it, the next -Start refused the
    # port it was holding, and two worker sets could end up claiming the same
    # jobs. Observed twice on 2026-09-12. The finally below makes the file
    # describe reality at every exit, including the failing ones.
    try {

    Write-Host "starting local stack..."
    $postgres = Start-PersistentPostgres
    if ($postgres["Started"]) { $state["postgres"] = $postgres["ProcessId"] }

    Show-MigrationDrift $environments["api"]

    $api = Start-LoggedProcess -FilePath $python -ArgumentList @("server.py", "--port", "$ApiPort", "--reload", "false") `
        -WorkingDirectory $fastApi -Environment $environments["api"] `
        -StdoutPath (Join-Path $logs "api-$stamp.stdout.log") -StderrPath (Join-Path $logs "api-$stamp.stderr.log")
    $state["api"] = $api.Id
    Wait-ForHttp200 "http://127.0.0.1:$ApiPort/api/v1/auth/status"
    Write-Host "  api: ready on $ApiPort"

    foreach ($controller in @("worker-generation", "worker-export")) {
        $worker = Start-LoggedProcess -FilePath $python -ArgumentList @("-m", "services.workspace_operation_worker") `
            -WorkingDirectory $fastApi -Environment $environments[$controller] `
            -StdoutPath (Join-Path $logs "$controller-$stamp.stdout.log") -StderrPath (Join-Path $logs "$controller-$stamp.stderr.log")
        $state[$controller] = $worker.Id
        Write-Host "  ${controller}: started (PID $($worker.Id))"
    }

    $next = Start-LoggedProcess -FilePath "npm.cmd" -ArgumentList @("run", "dev", "--", "-p", "$NextPort") `
        -WorkingDirectory $nextJs -Environment $environments["next"] `
        -StdoutPath (Join-Path $logs "next-$stamp.stdout.log") -StderrPath (Join-Path $logs "next-$stamp.stderr.log")
    $state["next"] = $next.Id
    [void](Wait-ForListener $NextPort 240)
    Write-Host "  next: ready on $NextPort"

    $proxy = Start-LoggedProcess -FilePath "node.exe" -ArgumentList @((Join-Path $repoRoot "scripts\presenton-local-proxy.mjs")) `
        -WorkingDirectory $repoRoot -Environment $environments["proxy"] `
        -StdoutPath (Join-Path $logs "proxy-$stamp.stdout.log") -StderrPath (Join-Path $logs "proxy-$stamp.stderr.log")
    $state["proxy"] = $proxy.Id
    [void](Wait-ForListener $ProxyPort 60)
    Write-Host "  proxy: ready on $ProxyPort"

    Write-Host ""
    Write-Host "local stack is up: http://localhost:$ProxyPort"
    Write-Host "stop it with: .\scripts\start-local-stack.ps1 -Stop"

    }
    finally {
        # Always, so a partial start is still stoppable by name.
        Write-State $state
        if ($state.Count -gt 0 -and -not (Get-PortOwner $ProxyPort)) {
            Write-Warning ("start did not complete; {0} component(s) recorded in {1}. Run -Stop to clear them before starting again." -f $state.Count, $statePath)
        }
    }
}

if ($RestartComponent) {
    if ($Stop -or $Status -or $Adopt -or $ImportEnvironment) { throw "local stack: RestartComponent cannot combine lifecycle actions" }
    Invoke-RestartComponents
    return
}
if ($ImportEnvironment) { Import-EnvironmentDirectory $ImportEnvironment; return }
if ($Status) { Invoke-Status; return }
if ($Adopt) { Invoke-Adopt; return }
if ($Stop) { Invoke-Stop; return }
Invoke-Start

exit 0
