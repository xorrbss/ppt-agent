[CmdletBinding()]
param(
    [ValidateSet("generation-export", "export-only")]
    [string]$Mode = "generation-export",
    [ValidateRange(1024, 65535)]
    [int]$ApiPort = 8010,
    # Bounded product-path verification without starting/reusing Next or proxy.
    [switch]$ApiOnly,
    # Pre-existing operator-owned migration/runtime authorities. This launcher
    # never creates or changes external login roles.
    [string]$DatabaseUrl = $env:PUBLIC_V2_RC_DATABASE_URL,
    [string]$ApiDatabaseUrl = $env:PUBLIC_V2_RC_API_DATABASE_URL,
    [string]$ControllerDatabaseUrl = $env:PUBLIC_V2_RC_CONTROLLER_DATABASE_URL,
    [string]$ObservationDatabaseUrl = $env:PUBLIC_V2_RC_OBSERVATION_DATABASE_URL,
    [string]$RuntimeEnvironment = $env:PUBLIC_V2_RC_ENVIRONMENT,
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
and an S3-compatible artifact authority may be pre-existing operator-owned
authorities. Explicit local-development runs may instead use owned PostgreSQL
and a content-addressed artifact root. The concrete render/export child is
selected by the product's Windows local supervised worker composition root.

The public download contract always proves a short-lived redirect. This
launcher never starts or bootstraps MinIO/S3; local-development redirect
evidence remains development-only. Provider configuration is synced only
through the existing DB bridge and is neither printed nor copied to browser env.
#>

function Require-Value([string]$Name, [string]$Value) {
    if ([string]::IsNullOrWhiteSpace($Value)) { throw "public-v2 RC preflight blocked: $Name is required" }
    return $Value.Trim()
}

function Resolve-WorkerRuntimeLimits([System.Collections.IDictionary]$Source) {
    # Match the controller cache and output policies; explicit invalid values fail.
    $defaults = @{
        "PUBLIC_WORKER_ARTIFACT_CACHE_MAX_BYTES" = "1073741824"
        "PUBLIC_WORKER_ARTIFACT_CACHE_TTL_SECONDS" = "900"
        "PPT_WORKER_OUTPUT_LIMIT_BYTES" = "536870912"
    }
    $resolved = @{}
    foreach ($key in $defaults.Keys) {
        $value = $Source[$key]
        if ($null -eq $value) { $value = $defaults[$key] }
        [System.Numerics.BigInteger]$number = 0
        if ($value -isnot [string] -or -not [System.Numerics.BigInteger]::TryParse($value, [System.Globalization.NumberStyles]::Integer, [System.Globalization.CultureInfo]::InvariantCulture, [ref]$number) -or $number -le 0) {
            throw "public-v2 RC preflight blocked: $key must be a positive integer"
        }
        $resolved[$key] = $value
    }
    return $resolved
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

$workerRuntimeLimits = Resolve-WorkerRuntimeLimits @{
    "PUBLIC_WORKER_ARTIFACT_CACHE_MAX_BYTES" = [Environment]::GetEnvironmentVariable("PUBLIC_WORKER_ARTIFACT_CACHE_MAX_BYTES")
    "PUBLIC_WORKER_ARTIFACT_CACHE_TTL_SECONDS" = [Environment]::GetEnvironmentVariable("PUBLIC_WORKER_ARTIFACT_CACHE_TTL_SECONDS")
    "PPT_WORKER_OUTPUT_LIMIT_BYTES" = [Environment]::GetEnvironmentVariable("PPT_WORKER_OUTPUT_LIMIT_BYTES")
}
$externalDatabaseValues = @($DatabaseUrl, $ApiDatabaseUrl, $ControllerDatabaseUrl, $ObservationDatabaseUrl)
$externalDatabaseValueCount = @($externalDatabaseValues | Where-Object { -not [string]::IsNullOrWhiteSpace($_) }).Count
if ($UseOwnedPostgres -and $externalDatabaseValueCount -gt 0) { throw "public-v2 RC preflight blocked: owned PostgreSQL cannot be combined with external database URLs" }
if ($externalDatabaseValueCount -notin 0, 4) { throw "public-v2 RC preflight blocked: migration, API, controller, and observation database URLs are required together" }
$usingOwnedPostgres = $UseOwnedPostgres -or $externalDatabaseValueCount -eq 0
$migrationDatabaseUrl = $null; $databaseUrl = $null; $observationDatabaseUrl = $null
if (-not $usingOwnedPostgres) {
    $migrationDatabaseUrl = Require-PostgresUrl "PUBLIC_V2_RC_DATABASE_URL" $DatabaseUrl "asyncpg"
    $databaseUrl = Require-PostgresUrl "PUBLIC_V2_RC_API_DATABASE_URL" $ApiDatabaseUrl "asyncpg"
    $ControllerDatabaseUrl = Require-PostgresUrl "PUBLIC_V2_RC_CONTROLLER_DATABASE_URL" $ControllerDatabaseUrl "asyncpg"
    $observationDatabaseUrl = Require-PostgresUrl "PUBLIC_V2_RC_OBSERVATION_DATABASE_URL" $ObservationDatabaseUrl "psycopg"
    $RuntimeEnvironment = Require-Value "PUBLIC_V2_RC_ENVIRONMENT" $RuntimeEnvironment
    if ($RuntimeEnvironment.ToLowerInvariant() -in "local", "development", "dev", "test") { throw "public-v2 RC preflight blocked: external role-separated databases require a non-local environment" }
    if ($databaseUrl -eq $ControllerDatabaseUrl) { throw "public-v2 RC preflight blocked: API and controller database URLs must be distinct" }
}

function Resolve-ProviderPricingContract([string]$RevisionValue, [string]$CatalogValue) {
    $revision = Require-Value "PUBLIC_PROVIDER_PRICING_REVISION" $RevisionValue
    $catalogText = Require-Value "PUBLIC_PROVIDER_PRICING_CATALOG" $CatalogValue
    try { $catalog = ConvertFrom-Json -InputObject $catalogText -Depth 20 }
    catch { throw "public-v2 RC preflight blocked: PUBLIC_PROVIDER_PRICING_CATALOG must be valid JSON" }
    if ($null -eq $catalog -or [string]$catalog.schemaVersion -ne "public-provider-pricing-catalog/v1") {
        throw "public-v2 RC preflight blocked: PUBLIC_PROVIDER_PRICING_CATALOG schemaVersion is invalid"
    }
    if ([string]$catalog.revision -ne $revision) {
        throw "public-v2 RC preflight blocked: provider pricing revision/catalog mismatch"
    }
    if (@($catalog.entries).Count -lt 1) {
        throw "public-v2 RC preflight blocked: provider pricing catalog has no entries"
    }
    return @{ Revision = $revision; Catalog = $catalogText }
}
$artifactBackend = Require-Value "PUBLIC_ARTIFACT_STORE_BACKEND" ([Environment]::GetEnvironmentVariable("PUBLIC_ARTIFACT_STORE_BACKEND"))
if ($artifactBackend -notin "s3", "local-development") { throw "public-v2 RC preflight blocked: PUBLIC_ARTIFACT_STORE_BACKEND must be s3 or local-development" }
if (-not $usingOwnedPostgres -and $artifactBackend -eq "local-development") { throw "public-v2 RC preflight blocked: external role-separated RC requires the S3 artifact authority" }
if ($artifactBackend -eq "s3") {
    foreach ($name in "PUBLIC_ARTIFACT_S3_BUCKET", "PUBLIC_ARTIFACT_S3_PREFIX") { [void](Require-Value $name ([Environment]::GetEnvironmentVariable($name))) }
} else {
    if (-not [string]::IsNullOrWhiteSpace($ArtifactRoot)) {
        $ArtifactRoot = [System.IO.Path]::GetFullPath($ArtifactRoot, $repo)
        if (-not [System.IO.Path]::IsPathFullyQualified($ArtifactRoot)) { throw "public-v2 RC preflight blocked: local artifact root must be absolute" }
    }
}
$providerPricing = $null
if ($Mode -eq "generation-export") {
    if ((Require-Value "PUBLIC_V2_RC_PROVIDER_READY" ([Environment]::GetEnvironmentVariable("PUBLIC_V2_RC_PROVIDER_READY"))) -ne "1") { throw "public-v2 RC preflight blocked: real provider is not declared ready; no fake generation is permitted" }
    $providerPricing = Resolve-ProviderPricingContract `
        ([Environment]::GetEnvironmentVariable("PUBLIC_PROVIDER_PRICING_REVISION")) `
        ([Environment]::GetEnvironmentVariable("PUBLIC_PROVIDER_PRICING_CATALOG"))
}
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
$testBaseUrl = if ($ApiOnly) { "http://127.0.0.1:$ApiPort" } else { "http://127.0.0.1:5110" }
$requiredPorts = if ($ApiOnly) { @($ApiPort) } else { @($ApiPort, 3010, 5110) }
foreach ($port in $requiredPorts) { Assert-PortFree $port }

$runId = "public-v2-rc-$PID-$(Get-Date -Format 'yyyyMMddHHmmss')"
$runtimeRoot = Join-Path ([System.IO.Path]::GetTempPath()) $runId
$appData = Join-Path $runtimeRoot "app-data"; $logs = Join-Path $runtimeRoot "logs"; $staging = Join-Path $runtimeRoot "trusted-staging"; $productEvidence = Join-Path $runtimeRoot "product-evidence"
New-Item -ItemType Directory -Force -Path $appData, $logs, $staging, $productEvidence | Out-Null
if ($artifactBackend -eq "local-development" -and [string]::IsNullOrWhiteSpace($ArtifactRoot)) {
    $ArtifactRoot = Join-Path $runtimeRoot "local-artifact-authority"
}
$authPasswordBytes = New-Object byte[] 24; [System.Security.Cryptography.RandomNumberGenerator]::Fill($authPasswordBytes)
$authPassword = [Convert]::ToHexString($authPasswordBytes).ToLowerInvariant()
$ownedPostgres = $null
$ownedApiDatabasePassword = $null
$ownedControllerDatabasePassword = $null
if ($usingOwnedPostgres) {
    $databasePasswordBytes = New-Object byte[] 24; [System.Security.Cryptography.RandomNumberGenerator]::Fill($databasePasswordBytes)
    $databasePassword = [Convert]::ToHexString($databasePasswordBytes).ToLowerInvariant()
    $ownedPostgres = Start-OwnedPostgres -DataDirectory (Join-Path $runtimeRoot "postgres-data") -LogPath (Join-Path $logs "postgres.log") -PasswordFile (Join-Path $runtimeRoot "postgres.password") -Password $databasePassword
    $migrationDatabaseUrl = "postgresql+asyncpg://presenton_rc:${databasePassword}@127.0.0.1:$($ownedPostgres.Port)/postgres"
    $databaseUrl = $migrationDatabaseUrl
    $observationDatabaseUrl = "postgresql+psycopg://presenton_rc:${databasePassword}@127.0.0.1:$($ownedPostgres.Port)/postgres"
    $apiPasswordBytes = New-Object byte[] 24; [System.Security.Cryptography.RandomNumberGenerator]::Fill($apiPasswordBytes)
    $controllerPasswordBytes = New-Object byte[] 24; [System.Security.Cryptography.RandomNumberGenerator]::Fill($controllerPasswordBytes)
    $ownedApiDatabasePassword = [Convert]::ToHexString($apiPasswordBytes).ToLowerInvariant()
    $ownedControllerDatabasePassword = [Convert]::ToHexString($controllerPasswordBytes).ToLowerInvariant()
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
    "PUBLIC_WORKER_ARTIFACT_CACHE_MAX_BYTES" = $workerRuntimeLimits["PUBLIC_WORKER_ARTIFACT_CACHE_MAX_BYTES"]
    "PUBLIC_WORKER_ARTIFACT_CACHE_TTL_SECONDS" = $workerRuntimeLimits["PUBLIC_WORKER_ARTIFACT_CACHE_TTL_SECONDS"]
    "PPT_WORKER_OUTPUT_LIMIT_BYTES" = $workerRuntimeLimits["PPT_WORKER_OUTPUT_LIMIT_BYTES"]
    "PUBLIC_RENDER_MAX_CONCURRENCY" = "2"
    "PUBLIC_V2_WINDOWS_PRODUCT_E2E" = "1"; "PUBLIC_V2_WINDOWS_PRODUCT_E2E_BASE_URL" = $testBaseUrl; "PUBLIC_V2_WINDOWS_PRODUCT_E2E_DATABASE_URL" = $observationDatabaseUrl
    "PUBLIC_V2_WINDOWS_PRODUCT_E2E_USERNAME" = "rc-admin"; "PUBLIC_V2_WINDOWS_PRODUCT_E2E_PASSWORD" = $authPassword; "PUBLIC_V2_WINDOWS_PRODUCT_E2E_TIMEOUT_SECONDS" = "900"
    "PUBLIC_V2_WINDOWS_PRODUCT_E2E_ARTIFACT_DIR" = $productEvidence
    "AUTH_USERNAME" = "rc-admin"; "AUTH_PASSWORD" = $authPassword; "AUTH_OVERRIDE_FROM_ENV" = "true"
    "PPT_SERVICE_TENANT_ID" = ([Environment]::GetEnvironmentVariable("PPT_SERVICE_TENANT_ID") ?? "default"); "WORKSPACE_OPERATION_WORKER_MODE" = "external"
    # Bootstrap/migrate before the DB-backed provider sync. The generation
    # controller below overrides this and performs the actual provider check.
    "PRESENTON_SKIP_PROVIDER_AVAILABILITY_CHECK" = "true"
}
# Propagate only production gates, never the development fixture adapter.
foreach ($key in @("DECK_IR_GENERATION_ENABLED", "PUBLIC_V2_WINDOWS_PRODUCT_E2E_GENERATION_QUALITY_POLICY")) {
    $value = [Environment]::GetEnvironmentVariable($key)
    if (-not [string]::IsNullOrWhiteSpace($value)) { $sharedEnvironment[$key] = $value }
}
if ($null -ne $providerPricing) {
    # Explicitly bind the validated immutable pricing snapshot into every child
    # instead of relying on ambient inheritance. Exact provider/model matching
    # still occurs at the final admission boundary before any provider I/O.
    $sharedEnvironment["PUBLIC_PROVIDER_PRICING_REVISION"] = $providerPricing.Revision
    $sharedEnvironment["PUBLIC_PROVIDER_PRICING_CATALOG"] = $providerPricing.Catalog
}
if ($usingOwnedPostgres) {
    # The cluster is disposable and loopback-owned regardless of the artifact
    # backend paired with it (local-development or an operator S3 bucket).
    $sharedEnvironment["ENVIRONMENT"] = "local"
} else {
    $sharedEnvironment["ENVIRONMENT"] = $RuntimeEnvironment
    $sharedEnvironment["DATABASE_URL"] = ""
    $sharedEnvironment["PUBLIC_DATABASE_PROCESS_ROLE"] = "api"
    $sharedEnvironment["PUBLIC_API_DATABASE_URL"] = $databaseUrl
    $sharedEnvironment["PUBLIC_CONTROLLER_DATABASE_URL"] = ""
    $sharedEnvironment["PUBLIC_MIGRATION_DATABASE_URL"] = ""
    $sharedEnvironment["MIGRATE_DATABASE_ON_STARTUP"] = "false"
}
if ($artifactBackend -eq "local-development") {
    New-Item -ItemType Directory -Force -Path $ArtifactRoot | Out-Null
    $sharedEnvironment["ENVIRONMENT"] = "local"
    $sharedEnvironment["PUBLIC_WORKER_OBJECT_STORE_ROOT"] = $ArtifactRoot
    $sharedEnvironment["PUBLIC_LOCAL_ARTIFACT_DOWNLOAD_SIGNING_KEY"] = $localDownloadSigningKey
    $sharedEnvironment["PUBLIC_LOCAL_ARTIFACT_DOWNLOAD_ORIGIN"] = "http://127.0.0.1:$ApiPort"
}
foreach ($key in @("PUBLIC_ARTIFACT_S3_BUCKET", "PUBLIC_ARTIFACT_S3_PREFIX", "PUBLIC_ARTIFACT_S3_ENDPOINT_URL", "PUBLIC_ARTIFACT_S3_REGION", "PUBLIC_ARTIFACT_S3_ADDRESSING_STYLE", "PUBLIC_ARTIFACT_S3_TLS_VERIFY", "PUBLIC_ARTIFACT_S3_SERVER_SIDE_ENCRYPTION", "AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_SESSION_TOKEN", "AWS_DEFAULT_REGION", "AWS_ENDPOINT_URL")) {
    $value = [Environment]::GetEnvironmentVariable($key)
    if (-not [string]::IsNullOrWhiteSpace($value)) { $sharedEnvironment[$key] = $value }
}

$startedPids = [System.Collections.Generic.List[int]]::new()
try {
    if (-not $usingOwnedPostgres) {
        $migrationEnvironment = $sharedEnvironment.Clone()
        $migrationEnvironment["DATABASE_URL"] = ""
        $migrationEnvironment["PUBLIC_DATABASE_PROCESS_ROLE"] = "migration"
        $migrationEnvironment["PUBLIC_API_DATABASE_URL"] = ""
        $migrationEnvironment["PUBLIC_CONTROLLER_DATABASE_URL"] = ""
        $migrationEnvironment["PUBLIC_MIGRATION_DATABASE_URL"] = $migrationDatabaseUrl
        $migrationEnvironment["MIGRATE_DATABASE_ON_STARTUP"] = "false"
        $migration = Start-LoggedProcess -FilePath $python -ArgumentList @("-m", "scripts.run_public_v2_rc_migrations") -WorkingDirectory $fastApi -Environment $migrationEnvironment -StdoutPath (Join-Path $logs "database-migration.stdout.log") -StderrPath (Join-Path $logs "database-migration.stderr.log")
        $migration.WaitForExit()
        if ($migration.ExitCode -ne 0) { throw "public-v2 RC database migration failed; see retained evidence logs" }
    } else {
        # Migrations and role creation use the disposable cluster bootstrap
        # owner exactly once. Runtime processes receive mutually exclusive,
        # non-superuser API/controller credentials below.
        $provisionEnvironment = $sharedEnvironment.Clone()
        $provisionEnvironment["PUBLIC_V2_LOCAL_API_DB_PASSWORD"] = $ownedApiDatabasePassword
        $provisionEnvironment["PUBLIC_V2_LOCAL_CONTROLLER_DB_PASSWORD"] = $ownedControllerDatabasePassword
        $provision = Start-LoggedProcess -FilePath $python -ArgumentList @("-m", "scripts.provision_public_v2_local_db_roles") -WorkingDirectory $fastApi -Environment $provisionEnvironment -StdoutPath (Join-Path $logs "database-role-provision.stdout.log") -StderrPath (Join-Path $logs "database-role-provision.stderr.log")
        $provision.WaitForExit()
        if ($provision.ExitCode -ne 0) { throw "public-v2 RC database role provisioning failed; see retained evidence logs" }

        $databaseUrl = "postgresql+asyncpg://presenton_public_api_login:${ownedApiDatabasePassword}@127.0.0.1:$($ownedPostgres.Port)/postgres"
        $controllerDatabaseUrl = "postgresql+asyncpg://presenton_public_controller_login:${ownedControllerDatabasePassword}@127.0.0.1:$($ownedPostgres.Port)/postgres"
        $observationDatabaseUrl = "postgresql+psycopg://presenton_public_controller_login:${ownedControllerDatabasePassword}@127.0.0.1:$($ownedPostgres.Port)/postgres"
        $sharedEnvironment["DATABASE_URL"] = $databaseUrl
        $sharedEnvironment["MIGRATE_DATABASE_ON_STARTUP"] = "false"
        $sharedEnvironment["PUBLIC_DATABASE_PROCESS_ROLE"] = "api"
        $sharedEnvironment["PUBLIC_V2_WINDOWS_PRODUCT_E2E_DATABASE_URL"] = $observationDatabaseUrl
    }

    $api = Start-LoggedProcess -FilePath $python -ArgumentList @("server.py", "--port", "$ApiPort", "--reload", "false") -WorkingDirectory $fastApi -Environment $sharedEnvironment -StdoutPath (Join-Path $logs "fastapi.stdout.log") -StderrPath (Join-Path $logs "fastapi.stderr.log")
    $startedPids.Add($api.Id); Wait-Http200 "http://127.0.0.1:$ApiPort/api/v1/auth/status" $startedPids.ToArray() $ApiPort

    if ($ProviderConfigPath) {
        $syncEnvironment = $sharedEnvironment.Clone()
        # Keep the RC-local compatibility mirror as USER_CONFIG_PATH. The
        # one-shot sync process alone may read the approved import file, and
        # must never mirror runtime/auth state back into that source file.
        $syncEnvironment["PRESENTON_PROVIDER_CONFIG_IMPORT_PATH"] = $ProviderConfigPath
        $sync = Start-LoggedProcess -FilePath $python -ArgumentList @("-m", "scripts.sync_existing_provider_settings") -WorkingDirectory $fastApi -Environment $syncEnvironment -StdoutPath (Join-Path $logs "provider-settings-sync.stdout.log") -StderrPath (Join-Path $logs "provider-settings-sync.stderr.log")
        $sync.WaitForExit(); if ($sync.ExitCode -ne 0) { throw "public-v2 RC provider settings sync failed; see retained evidence logs" }
    }
    if ($Mode -eq "export-only" -and $ExportSnapshotPath) {
        $seed = Start-LoggedProcess -FilePath $python -ArgumentList @("-m", "scripts.seed_public_v2_export_snapshot", "--snapshot", $ExportSnapshotPath, "--username", "rc-admin") -WorkingDirectory $fastApi -Environment $sharedEnvironment -StdoutPath (Join-Path $logs "export-snapshot-seed.stdout.log") -StderrPath (Join-Path $logs "export-snapshot-seed.stderr.log")
        $seed.WaitForExit(); if ($seed.ExitCode -ne 0) { throw "public-v2 RC export snapshot seed failed; see retained evidence logs" }
        $seedReceipt = Get-Content -Raw -LiteralPath (Join-Path $logs "export-snapshot-seed.stdout.log") | ConvertFrom-Json
        $ExistingPresentationId = [string]$seedReceipt.presentationId; $ExistingSourceRevision = [int]$seedReceipt.sourceRevision
        if ([string]::IsNullOrWhiteSpace($ExistingPresentationId) -or $ExistingSourceRevision -lt 0) { throw "public-v2 RC export snapshot seed emitted an invalid receipt" }
    }
    $exportEnvironment = $sharedEnvironment.Clone(); $exportEnvironment["WORKSPACE_OPERATION_WORKER_MODE"] = "external"; $exportEnvironment["PPT_PROCESS_PROFILE"] = "ppt-core"; $exportEnvironment["WORKSPACE_OPERATION_EXTERNAL_CONTROLLER"] = "export"
    if ($usingOwnedPostgres) {
        $exportEnvironment["DATABASE_URL"] = $ControllerDatabaseUrl
        $exportEnvironment["PUBLIC_DATABASE_PROCESS_ROLE"] = "controller"
    } else {
        $exportEnvironment["DATABASE_URL"] = ""
        $exportEnvironment["PUBLIC_DATABASE_PROCESS_ROLE"] = "controller"
        $exportEnvironment["PUBLIC_API_DATABASE_URL"] = ""
        $exportEnvironment["PUBLIC_CONTROLLER_DATABASE_URL"] = $ControllerDatabaseUrl
        $exportEnvironment["PUBLIC_MIGRATION_DATABASE_URL"] = ""
    }
    $export = Start-LoggedProcess -FilePath $python -ArgumentList @("-m", "services.workspace_operation_worker") -WorkingDirectory $fastApi -Environment $exportEnvironment -StdoutPath (Join-Path $logs "export-controller.stdout.log") -StderrPath (Join-Path $logs "export-controller.stderr.log")
    $startedPids.Add($export.Id)
    if ($Mode -eq "generation-export") {
        $generationEnvironment = $sharedEnvironment.Clone(); $generationEnvironment["WORKSPACE_OPERATION_WORKER_MODE"] = "external"; $generationEnvironment["PPT_PROCESS_PROFILE"] = "generation"; $generationEnvironment["WORKSPACE_OPERATION_EXTERNAL_CONTROLLER"] = "generation"; $generationEnvironment["PRESENTON_SKIP_PROVIDER_AVAILABILITY_CHECK"] = "false"
        if ($usingOwnedPostgres) {
            $generationEnvironment["DATABASE_URL"] = $ControllerDatabaseUrl
            $generationEnvironment["PUBLIC_DATABASE_PROCESS_ROLE"] = "controller"
        } else {
            $generationEnvironment["DATABASE_URL"] = ""
            $generationEnvironment["PUBLIC_DATABASE_PROCESS_ROLE"] = "controller"
            $generationEnvironment["PUBLIC_API_DATABASE_URL"] = ""
            $generationEnvironment["PUBLIC_CONTROLLER_DATABASE_URL"] = $ControllerDatabaseUrl
            $generationEnvironment["PUBLIC_MIGRATION_DATABASE_URL"] = ""
        }
        $generation = Start-LoggedProcess -FilePath $python -ArgumentList @("-m", "services.workspace_operation_worker") -WorkingDirectory $fastApi -Environment $generationEnvironment -StdoutPath (Join-Path $logs "generation-controller.stdout.log") -StderrPath (Join-Path $logs "generation-controller.stderr.log")
        $startedPids.Add($generation.Id)
    }
    if (-not $ApiOnly) {
    $nextEnvironment = @{
        "FAST_API_INTERNAL_URL" = "http://127.0.0.1:$ApiPort"
        "USER_CONFIG_PATH" = (Join-Path $appData "userConfig.json")
        "PUBLIC_V2_WEB_CANARY_ENABLED" = "true"
    }
    $next = Start-LoggedProcess -FilePath "npm.cmd" -ArgumentList @("run", "dev", "--", "-p", "3010") -WorkingDirectory $nextJs -Environment $nextEnvironment -StdoutPath (Join-Path $logs "next.stdout.log") -StderrPath (Join-Path $logs "next.stderr.log")
    $startedPids.Add($next.Id)
    $proxyEnvironment = @{ "FASTAPI_PORT" = "$ApiPort"; "NEXT_PORT" = "3010"; "PROXY_PORT" = "5110" }
    $proxy = Start-LoggedProcess -FilePath "node.exe" -ArgumentList @("scripts/presenton-local-proxy.mjs") -WorkingDirectory $repo -Environment $proxyEnvironment -StdoutPath (Join-Path $logs "proxy.stdout.log") -StderrPath (Join-Path $logs "proxy.stderr.log")
    $startedPids.Add($proxy.Id)
    Wait-Http200 "http://127.0.0.1:3010" $startedPids.ToArray() 3010; Wait-Http200 "http://127.0.0.1:5110" $startedPids.ToArray() 5110
    }

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
