[CmdletBinding()]
param(
    [ValidateSet("generation-export", "export-only")]
    [string]$Mode = "generation-export",
    # Pre-existing, operator-owned authorities. This launcher never creates one.
    [string]$DatabaseUrl = $env:PUBLIC_V2_RC_DATABASE_URL,
    [string]$ObservationDatabaseUrl = $env:PUBLIC_V2_RC_OBSERVATION_DATABASE_URL,
    [switch]$UseOwnedPostgres,
    [string]$ProviderConfigPath,
    [string]$ArtifactRoot = $env:PUBLIC_WORKER_OBJECT_STORE_ROOT,
    [string]$ExistingPresentationId,
    [int]$ExistingSourceRevision = -1,
    [string]$ExportSnapshotPath,
    [switch]$KeepStack
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

<#
Starts an owned public-v2 RC application stack without Docker, Docker Desktop,
WSL, containers, image inspection, or container-derived services. PostgreSQL
and the S3-compatible artifact authority are pre-existing operator-owned
authorities. The concrete render/export child is selected by the product's
Windows local supervised TrustedWorkerWireTransport composition root.

The public download contract needs a short-lived redirect, therefore this
launcher requires the existing `s3` artifact backend configuration. It never
starts or bootstraps MinIO/S3 and does not convert a local filesystem into
authorized-download evidence. Provider configuration is synced only through
the existing DB bridge and is neither printed nor copied to browser env.
#>

function Require-Value([string]$Name, [string]$Value) {
    if ([string]::IsNullOrWhiteSpace($Value)) { throw "public-v2 RC preflight blocked: $Name is required" }
    return $Value.Trim()
}

function Require-PostgresUrl([string]$Name, [string]$Value, [string]$Driver) {
    $normalized = Require-Value $Name $Value
    if ($normalized -notmatch "^postgresql\+${Driver}://") { throw "public-v2 RC preflight blocked: $Name must use postgresql+${Driver}://" }
    return $normalized
}

function Assert-PortFree([int]$Port) {
    $existing = Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction SilentlyContinue
    if ($null -ne $existing) {
        $owners = ($existing | Select-Object -ExpandProperty OwningProcess -Unique) -join ", "
        throw "public-v2 RC preflight blocked: port $Port already has a listener (PID: $owners)"
    }
}

function Get-OwnedDescendantPids([int[]]$RootPids) {
    $pending = [System.Collections.Generic.Queue[int]]::new()
    $all = [System.Collections.Generic.HashSet[int]]::new()
    foreach ($root in $RootPids) { if ($all.Add($root)) { $pending.Enqueue($root) } }
    while ($pending.Count -gt 0) {
        $parent = $pending.Dequeue()
        foreach ($child in @(Get-CimInstance Win32_Process -Filter "ParentProcessId=$parent" -ErrorAction SilentlyContinue)) {
            $childPid = [int]$child.ProcessId
            if ($all.Add($childPid)) { $pending.Enqueue($childPid) }
        }
    }
    return @($all)
}

function Wait-OwnedListener([int]$Port, [int[]]$RootPids, [int]$TimeoutSeconds = 90) {
    $deadline = [DateTime]::UtcNow.AddSeconds($TimeoutSeconds)
    while ([DateTime]::UtcNow -lt $deadline) {
        $listener = Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction SilentlyContinue | Select-Object -First 1
        if ($null -ne $listener) {
            if ((Get-OwnedDescendantPids $RootPids) -contains [int]$listener.OwningProcess) { return $listener }
            throw "public-v2 RC listener ownership mismatch on port $Port"
        }
        Start-Sleep -Milliseconds 500
    }
    throw "public-v2 RC listener timeout on port $Port"
}

function Wait-Http200([string]$Url, [int[]]$RootPids, [int]$Port) {
    [void](Wait-OwnedListener $Port $RootPids)
    $deadline = [DateTime]::UtcNow.AddSeconds(90)
    while ([DateTime]::UtcNow -lt $deadline) {
        try {
            $response = Invoke-WebRequest -UseBasicParsing -Uri $Url -TimeoutSec 5
            if ($response.StatusCode -eq 200) { return }
        } catch { Start-Sleep -Milliseconds 500 }
    }
    throw "public-v2 RC HTTP readiness timeout: $Url"
}

function Start-LoggedProcess {
    param(
        [Parameter(Mandatory = $true)][string]$FilePath,
        [Parameter(Mandatory = $true)][string[]]$ArgumentList,
        [Parameter(Mandatory = $true)][string]$WorkingDirectory,
        [Parameter(Mandatory = $true)][hashtable]$Environment,
        [Parameter(Mandatory = $true)][string]$StdoutPath,
        [Parameter(Mandatory = $true)][string]$StderrPath
    )
    # Windows PowerShell has no per-child environment map. Scope values around
    # creation, restore immediately, and never emit their values in logs.
    $previous = @{}
    foreach ($entry in $Environment.GetEnumerator()) {
        $current = Get-Item -LiteralPath "Env:$($entry.Key)" -ErrorAction SilentlyContinue
        $previous[$entry.Key] = if ($null -eq $current) { $null } else { $current.Value }
        Set-Item -LiteralPath "Env:$($entry.Key)" -Value ([string]$entry.Value)
    }
    try {
        return Start-Process -FilePath $FilePath -ArgumentList $ArgumentList -WorkingDirectory $WorkingDirectory `
            -RedirectStandardOutput $StdoutPath -RedirectStandardError $StderrPath -PassThru -WindowStyle Hidden
    } finally {
        foreach ($entry in $previous.GetEnumerator()) {
            if ($null -eq $entry.Value) { Remove-Item -LiteralPath "Env:$($entry.Key)" -ErrorAction SilentlyContinue }
            else { Set-Item -LiteralPath "Env:$($entry.Key)" -Value $entry.Value }
        }
    }
}

function Stop-OwnedProcessTree([int[]]$RootPids) {
    foreach ($ownedProcessId in (Get-OwnedDescendantPids $RootPids | Sort-Object -Descending)) { Stop-Process -Id $ownedProcessId -Force -ErrorAction SilentlyContinue }
}

function Find-FreeLoopbackPort {
    for ($port = 21000; $port -lt 24000; $port++) {
        if ($null -eq (Get-NetTCPConnection -State Listen -LocalPort $port -ErrorAction SilentlyContinue)) { return $port }
    }
    throw "public-v2 RC preflight blocked: no owned PostgreSQL port is available"
}

function Start-OwnedPostgres {
    param(
        [Parameter(Mandatory = $true)][string]$DataDirectory,
        [Parameter(Mandatory = $true)][string]$LogPath,
        [Parameter(Mandatory = $true)][string]$PasswordFile,
        [Parameter(Mandatory = $true)][string]$Password
    )
    $bin = "C:\Program Files\PostgreSQL\15\bin"
    $initdb = Join-Path $bin "initdb.exe"; $pgCtl = Join-Path $bin "pg_ctl.exe"; $postgres = Join-Path $bin "postgres.exe"
    if (-not (Test-Path -LiteralPath $initdb -PathType Leaf) -or -not (Test-Path -LiteralPath $pgCtl -PathType Leaf) -or -not (Test-Path -LiteralPath $postgres -PathType Leaf)) {
        throw "public-v2 RC preflight blocked: PostgreSQL 15 initdb/pg_ctl are unavailable"
    }
    $port = Find-FreeLoopbackPort
    [System.IO.File]::WriteAllText($PasswordFile, $Password, [System.Text.UTF8Encoding]::new($false))
    & $initdb -D $DataDirectory -U presenton_rc --auth=scram-sha-256 --pwfile=$PasswordFile --encoding=UTF8 --no-locale | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "public-v2 RC owned PostgreSQL initdb failed" }
    # Start postgres directly. pg_ctl's Windows daemon child can retain its
    # console/pipeline handle and prevent the launcher from progressing after
    # readiness; direct ownership gives this launcher an auditable root PID.
    $server = Start-Process -FilePath $postgres -ArgumentList @("-D", $DataDirectory, "-h", "127.0.0.1", "-p", "$port") -RedirectStandardOutput $LogPath -RedirectStandardError "$LogPath.stderr" -PassThru -WindowStyle Hidden
    $deadline = [DateTime]::UtcNow.AddSeconds(60)
    $listener = $null
    while ([DateTime]::UtcNow -lt $deadline) {
        $listener = Get-NetTCPConnection -State Listen -LocalPort $port -ErrorAction SilentlyContinue | Select-Object -First 1
        if ($null -ne $listener) { break }
        $server.Refresh()
        if ($server.HasExited) { throw "public-v2 RC owned PostgreSQL startup failed" }
        Start-Sleep -Milliseconds 250
    }
    if ($null -eq $listener) {
        [void](Start-Process -FilePath $pgCtl -ArgumentList @("-D", $DataDirectory, "-w", "-t", "30", "stop", "-m", "fast") -PassThru -Wait -WindowStyle Hidden)
        throw "public-v2 RC owned PostgreSQL listener proof failed"
    }
    $process = Get-CimInstance Win32_Process -Filter "ProcessId=$($listener.OwningProcess)" -ErrorAction SilentlyContinue
    $normalizedDataDirectory = $DataDirectory.Replace("\", "/")
    $normalizedCommandLine = if ($null -eq $process) { "" } else { $process.CommandLine.Replace("\", "/") }
    if ($null -eq $process -or [string]::IsNullOrWhiteSpace($process.CommandLine) -or $normalizedCommandLine -notlike "*$normalizedDataDirectory*") {
        [void](Start-Process -FilePath $pgCtl -ArgumentList @("-D", $DataDirectory, "-w", "-t", "30", "stop", "-m", "fast") -PassThru -Wait -WindowStyle Hidden)
        throw "public-v2 RC owned PostgreSQL process ownership proof failed"
    }
    return @{ Port = $port; PgCtl = $pgCtl; DataDirectory = $DataDirectory; ProcessId = [int]$listener.OwningProcess }
}

$repo = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$fastApi = Join-Path $repo "servers\fastapi"
$nextJs = Join-Path $repo "servers\nextjs"
$python = Join-Path $fastApi ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $python -PathType Leaf)) { throw "public-v2 RC preflight blocked: backend virtual environment is unavailable" }

$hasDatabaseUrl = -not [string]::IsNullOrWhiteSpace($DatabaseUrl)
$hasObservationUrl = -not [string]::IsNullOrWhiteSpace($ObservationDatabaseUrl)
if ($hasDatabaseUrl -ne $hasObservationUrl) { throw "public-v2 RC preflight blocked: both external database URLs are required together" }
$usingOwnedPostgres = $UseOwnedPostgres -or -not $hasDatabaseUrl
$databaseUrl = $null; $observationDatabaseUrl = $null
if (-not $usingOwnedPostgres) {
    $databaseUrl = Require-PostgresUrl "PUBLIC_V2_RC_DATABASE_URL" $DatabaseUrl "asyncpg"
    $observationDatabaseUrl = Require-PostgresUrl "PUBLIC_V2_RC_OBSERVATION_DATABASE_URL" $ObservationDatabaseUrl "psycopg"
}
$artifactBackend = Require-Value "PUBLIC_ARTIFACT_STORE_BACKEND" ([Environment]::GetEnvironmentVariable("PUBLIC_ARTIFACT_STORE_BACKEND"))
if ($artifactBackend -notin "s3", "local-development") { throw "public-v2 RC preflight blocked: PUBLIC_ARTIFACT_STORE_BACKEND must be s3 or local-development" }
if ($artifactBackend -eq "s3") {
    foreach ($name in "PUBLIC_ARTIFACT_S3_BUCKET", "PUBLIC_ARTIFACT_S3_PREFIX") { [void](Require-Value $name ([Environment]::GetEnvironmentVariable($name))) }
} else {
    if (-not [string]::IsNullOrWhiteSpace($ArtifactRoot)) {
        $ArtifactRoot = [System.IO.Path]::GetFullPath($ArtifactRoot, $repo)
        if (-not [System.IO.Path]::IsPathFullyQualified($ArtifactRoot)) { throw "public-v2 RC preflight blocked: local artifact root must be absolute" }
    }
}
if ($Mode -eq "generation-export" -and (Require-Value "PUBLIC_V2_RC_PROVIDER_READY" ([Environment]::GetEnvironmentVariable("PUBLIC_V2_RC_PROVIDER_READY"))) -ne "1") { throw "public-v2 RC preflight blocked: real provider is not declared ready; no fake generation is permitted" }
if ($ProviderConfigPath) {
    $ProviderConfigPath = [System.IO.Path]::GetFullPath($ProviderConfigPath, $repo)
    if (-not (Test-Path -LiteralPath $ProviderConfigPath -PathType Leaf)) { throw "public-v2 RC preflight blocked: provider config file is unavailable" }
}
if ($Mode -eq "export-only") {
    $hasExisting = -not [string]::IsNullOrWhiteSpace($ExistingPresentationId) -and $ExistingSourceRevision -ge 0
    $hasSnapshot = -not [string]::IsNullOrWhiteSpace($ExportSnapshotPath)
    if ($hasExisting -eq $hasSnapshot) { throw "export-only mode requires exactly one existing presentation/revision pair or -ExportSnapshotPath" }
    if ($hasSnapshot) {
        $ExportSnapshotPath = [System.IO.Path]::GetFullPath($ExportSnapshotPath, $repo)
        if (-not (Test-Path -LiteralPath $ExportSnapshotPath -PathType Leaf)) { throw "public-v2 RC preflight blocked: export snapshot is unavailable" }
    }
}
foreach ($port in 8010, 3010, 5110) { Assert-PortFree $port }

$runId = "public-v2-rc-$PID-$(Get-Date -Format 'yyyyMMddHHmmss')"
$runtimeRoot = Join-Path ([System.IO.Path]::GetTempPath()) $runId
$appData = Join-Path $runtimeRoot "app-data"; $logs = Join-Path $runtimeRoot "logs"; $staging = Join-Path $runtimeRoot "trusted-staging"
New-Item -ItemType Directory -Force -Path $appData, $logs, $staging | Out-Null
if ($artifactBackend -eq "local-development" -and [string]::IsNullOrWhiteSpace($ArtifactRoot)) {
    $ArtifactRoot = Join-Path $runtimeRoot "local-artifact-authority"
}
$authPasswordBytes = New-Object byte[] 24; [System.Security.Cryptography.RandomNumberGenerator]::Fill($authPasswordBytes)
$authPassword = [Convert]::ToHexString($authPasswordBytes).ToLowerInvariant()
$ownedPostgres = $null
if ($usingOwnedPostgres) {
    $databasePasswordBytes = New-Object byte[] 24; [System.Security.Cryptography.RandomNumberGenerator]::Fill($databasePasswordBytes)
    $databasePassword = [Convert]::ToHexString($databasePasswordBytes).ToLowerInvariant()
    $ownedPostgres = Start-OwnedPostgres -DataDirectory (Join-Path $runtimeRoot "postgres-data") -LogPath (Join-Path $logs "postgres.log") -PasswordFile (Join-Path $runtimeRoot "postgres.password") -Password $databasePassword
    $databaseUrl = "postgresql+asyncpg://presenton_rc:${databasePassword}@127.0.0.1:$($ownedPostgres.Port)/postgres"
    $observationDatabaseUrl = "postgresql+psycopg://presenton_rc:${databasePassword}@127.0.0.1:$($ownedPostgres.Port)/postgres"
}
$capabilityBytes = New-Object byte[] 32; [System.Security.Cryptography.RandomNumberGenerator]::Fill($capabilityBytes)
$capabilityFile = Join-Path $runtimeRoot "worker-capability.key"; [System.IO.File]::WriteAllBytes($capabilityFile, $capabilityBytes)
$localDownloadSigningKeyBytes = New-Object byte[] 32; [System.Security.Cryptography.RandomNumberGenerator]::Fill($localDownloadSigningKeyBytes)
$localDownloadSigningKey = [Convert]::ToHexString($localDownloadSigningKeyBytes).ToLowerInvariant()
$renderWorkerCommand = @($python, "-m", "workers.public_render_worker") | ConvertTo-Json -Compress
$exportWorkerCommand = @($python, "-m", "workers.public_export_worker") | ConvertTo-Json -Compress

$sharedEnvironment = @{
    "DATABASE_URL" = $databaseUrl; "APP_DATA_DIRECTORY" = $appData; "USER_CONFIG_PATH" = (Join-Path $appData "userConfig.json")
    "MIGRATE_DATABASE_ON_STARTUP" = "true"; "PUBLIC_V2_INTEGRATION_PROFILE" = "postgresql-strict"; "PUBLIC_ARTIFACT_STORE_BACKEND" = $artifactBackend
    "PUBLIC_TRUSTED_WORKER_SANDBOX_BACKEND" = "windows-local"; "PUBLIC_TRUSTED_WORKER_LOCAL_POLICY" = "development"; "PUBLIC_TRUSTED_WORKER_STAGING_ROOT" = $staging; "PUBLIC_WORKER_NODE_ID" = "$runId-export"
    "PUBLIC_WORKER_CAPABILITY_KEY_FILE" = $capabilityFile; "PUBLIC_RENDER_WORKER_COMMAND_JSON" = $renderWorkerCommand; "PUBLIC_EXPORT_WORKER_COMMAND_JSON" = $exportWorkerCommand
    "PUBLIC_V2_WINDOWS_PRODUCT_E2E" = "1"; "PUBLIC_V2_WINDOWS_PRODUCT_E2E_BASE_URL" = "http://127.0.0.1:5110"; "PUBLIC_V2_WINDOWS_PRODUCT_E2E_DATABASE_URL" = $observationDatabaseUrl
    "PUBLIC_V2_WINDOWS_PRODUCT_E2E_USERNAME" = "rc-admin"; "PUBLIC_V2_WINDOWS_PRODUCT_E2E_PASSWORD" = $authPassword; "PUBLIC_V2_WINDOWS_PRODUCT_E2E_TIMEOUT_SECONDS" = "900"
    "AUTH_USERNAME" = "rc-admin"; "AUTH_PASSWORD" = $authPassword; "AUTH_OVERRIDE_FROM_ENV" = "true"
    "PPT_SERVICE_TENANT_ID" = ([Environment]::GetEnvironmentVariable("PPT_SERVICE_TENANT_ID") ?? "default"); "WORKSPACE_OPERATION_WORKER_MODE" = "external"
    # Bootstrap/migrate before the DB-backed provider sync. The generation
    # controller below overrides this and performs the actual provider check.
    "PRESENTON_SKIP_PROVIDER_AVAILABILITY_CHECK" = "true"
}
if ($artifactBackend -eq "local-development") {
    New-Item -ItemType Directory -Force -Path $ArtifactRoot | Out-Null
    $sharedEnvironment["ENVIRONMENT"] = "local"
    $sharedEnvironment["PUBLIC_WORKER_OBJECT_STORE_ROOT"] = $ArtifactRoot
    $sharedEnvironment["PUBLIC_LOCAL_ARTIFACT_DOWNLOAD_SIGNING_KEY"] = $localDownloadSigningKey
    $sharedEnvironment["PUBLIC_LOCAL_ARTIFACT_DOWNLOAD_ORIGIN"] = "http://127.0.0.1:8010"
}
foreach ($key in @("PUBLIC_ARTIFACT_S3_BUCKET", "PUBLIC_ARTIFACT_S3_PREFIX", "PUBLIC_ARTIFACT_S3_ENDPOINT_URL", "PUBLIC_ARTIFACT_S3_REGION", "PUBLIC_ARTIFACT_S3_ADDRESSING_STYLE", "PUBLIC_ARTIFACT_S3_TLS_VERIFY", "PUBLIC_ARTIFACT_S3_SERVER_SIDE_ENCRYPTION", "AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_SESSION_TOKEN", "AWS_DEFAULT_REGION", "AWS_ENDPOINT_URL")) {
    $value = [Environment]::GetEnvironmentVariable($key)
    if (-not [string]::IsNullOrWhiteSpace($value)) { $sharedEnvironment[$key] = $value }
}

$startedPids = [System.Collections.Generic.List[int]]::new()
try {
    $api = Start-LoggedProcess -FilePath $python -ArgumentList @("server.py", "--port", "8010", "--reload", "false") -WorkingDirectory $fastApi -Environment $sharedEnvironment -StdoutPath (Join-Path $logs "fastapi.stdout.log") -StderrPath (Join-Path $logs "fastapi.stderr.log")
    $startedPids.Add($api.Id); Wait-Http200 "http://127.0.0.1:8010/api/v1/auth/status" $startedPids.ToArray() 8010

    if ($ProviderConfigPath) {
        $syncEnvironment = $sharedEnvironment.Clone(); $syncEnvironment["USER_CONFIG_PATH"] = $ProviderConfigPath
        $sync = Start-LoggedProcess -FilePath $python -ArgumentList @("scripts\sync_existing_provider_settings.py") -WorkingDirectory $fastApi -Environment $syncEnvironment -StdoutPath (Join-Path $logs "provider-settings-sync.stdout.log") -StderrPath (Join-Path $logs "provider-settings-sync.stderr.log")
        $sync.WaitForExit(); if ($sync.ExitCode -ne 0) { throw "public-v2 RC provider settings sync failed; see retained evidence logs" }
    }
    if ($Mode -eq "export-only" -and $ExportSnapshotPath) {
        $seed = Start-LoggedProcess -FilePath $python -ArgumentList @("scripts\seed_public_v2_export_snapshot.py", "--snapshot", $ExportSnapshotPath, "--username", "rc-admin") -WorkingDirectory $fastApi -Environment $sharedEnvironment -StdoutPath (Join-Path $logs "export-snapshot-seed.stdout.log") -StderrPath (Join-Path $logs "export-snapshot-seed.stderr.log")
        $seed.WaitForExit(); if ($seed.ExitCode -ne 0) { throw "public-v2 RC export snapshot seed failed; see retained evidence logs" }
        $seedReceipt = Get-Content -Raw -LiteralPath (Join-Path $logs "export-snapshot-seed.stdout.log") | ConvertFrom-Json
        $ExistingPresentationId = [string]$seedReceipt.presentationId; $ExistingSourceRevision = [int]$seedReceipt.sourceRevision
        if ([string]::IsNullOrWhiteSpace($ExistingPresentationId) -or $ExistingSourceRevision -lt 0) { throw "public-v2 RC export snapshot seed emitted an invalid receipt" }
    }
    $exportEnvironment = $sharedEnvironment.Clone(); $exportEnvironment["WORKSPACE_OPERATION_WORKER_MODE"] = "external"; $exportEnvironment["PPT_PROCESS_PROFILE"] = "ppt-core"; $exportEnvironment["WORKSPACE_OPERATION_EXTERNAL_CONTROLLER"] = "export"
    $export = Start-LoggedProcess -FilePath $python -ArgumentList @("-m", "services.workspace_operation_worker") -WorkingDirectory $fastApi -Environment $exportEnvironment -StdoutPath (Join-Path $logs "export-controller.stdout.log") -StderrPath (Join-Path $logs "export-controller.stderr.log")
    $startedPids.Add($export.Id)
    if ($Mode -eq "generation-export") {
        $generationEnvironment = $sharedEnvironment.Clone(); $generationEnvironment["WORKSPACE_OPERATION_WORKER_MODE"] = "external"; $generationEnvironment["PPT_PROCESS_PROFILE"] = "generation"; $generationEnvironment["WORKSPACE_OPERATION_EXTERNAL_CONTROLLER"] = "generation"; $generationEnvironment["PRESENTON_SKIP_PROVIDER_AVAILABILITY_CHECK"] = "false"
        $generation = Start-LoggedProcess -FilePath $python -ArgumentList @("-m", "services.workspace_operation_worker") -WorkingDirectory $fastApi -Environment $generationEnvironment -StdoutPath (Join-Path $logs "generation-controller.stdout.log") -StderrPath (Join-Path $logs "generation-controller.stderr.log")
        $startedPids.Add($generation.Id)
    }
    $nextEnvironment = @{ "FAST_API_INTERNAL_URL" = "http://127.0.0.1:8010"; "USER_CONFIG_PATH" = (Join-Path $appData "userConfig.json") }
    $next = Start-LoggedProcess -FilePath "npm.cmd" -ArgumentList @("run", "dev", "--", "-p", "3010") -WorkingDirectory $nextJs -Environment $nextEnvironment -StdoutPath (Join-Path $logs "next.stdout.log") -StderrPath (Join-Path $logs "next.stderr.log")
    $startedPids.Add($next.Id)
    $proxyEnvironment = @{ "FASTAPI_PORT" = "8010"; "NEXT_PORT" = "3010"; "PROXY_PORT" = "5110" }
    $proxy = Start-LoggedProcess -FilePath "node.exe" -ArgumentList @("scripts/presenton-local-proxy.mjs") -WorkingDirectory $repo -Environment $proxyEnvironment -StdoutPath (Join-Path $logs "proxy.stdout.log") -StderrPath (Join-Path $logs "proxy.stderr.log")
    $startedPids.Add($proxy.Id)
    Wait-Http200 "http://127.0.0.1:3010" $startedPids.ToArray() 3010; Wait-Http200 "http://127.0.0.1:5110" $startedPids.ToArray() 5110

    if ($Mode -eq "export-only") {
        $testEnvironment = $sharedEnvironment.Clone(); $testEnvironment["PUBLIC_V2_WINDOWS_PRODUCT_E2E_EXPORT_ONLY"] = "1"; $testEnvironment["PUBLIC_V2_WINDOWS_PRODUCT_E2E_EXISTING_PRESENTATION_ID"] = $ExistingPresentationId; $testEnvironment["PUBLIC_V2_WINDOWS_PRODUCT_E2E_EXISTING_SOURCE_REVISION"] = "$ExistingSourceRevision"
        $test = Start-LoggedProcess -FilePath $python -ArgumentList @("-m", "pytest", "-q", "tests/integration/test_public_generation_export_windows_product_path.py", "-k", "export_only") -WorkingDirectory $fastApi -Environment $testEnvironment -StdoutPath (Join-Path $logs "product-path.stdout.log") -StderrPath (Join-Path $logs "product-path.stderr.log")
    } else {
        $test = Start-LoggedProcess -FilePath $python -ArgumentList @("-m", "pytest", "-q", "tests/integration/test_public_generation_export_windows_product_path.py", "-k", "http_generation_export") -WorkingDirectory $fastApi -Environment $sharedEnvironment -StdoutPath (Join-Path $logs "product-path.stdout.log") -StderrPath (Join-Path $logs "product-path.stderr.log")
    }
    $test.WaitForExit(); if ($test.ExitCode -ne 0) { throw "public-v2 RC live product-path test failed; see retained evidence logs" }
    Write-Output "Public v2 RC live product-path PASS. Evidence retained: $runtimeRoot"
} finally {
    if (-not $KeepStack) { Stop-OwnedProcessTree $startedPids.ToArray() }
    if ($null -ne $ownedPostgres -and -not $KeepStack) {
        [void](Start-Process -FilePath $ownedPostgres.PgCtl -ArgumentList @("-D", $ownedPostgres.DataDirectory, "-w", "-t", "30", "stop", "-m", "fast") -PassThru -Wait -WindowStyle Hidden)
    }
    Write-Output "Public v2 RC cleanup/deletion audit is intentionally deferred; retained evidence root: $runtimeRoot"
}
