[CmdletBinding()]
param(
    [ValidateRange(1, 16)]
    [int]$AuthoredConcurrency = 2,
    [string]$NodeId = "public-v2-source-native-generation-extra"
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$repo = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$fastApi = Join-Path $repo "servers\fastapi"
$python = (Resolve-Path (Join-Path $fastApi ".venv\Scripts\python.exe")).Path
$runtimeRoot = Join-Path $repo "app_data\public-v2-local"
$runtime = Get-Content -LiteralPath (Join-Path $runtimeRoot "runtime.json") -Raw | ConvertFrom-Json
$secretMap = Import-Clixml -LiteralPath (Join-Path $runtimeRoot "secrets.dpapi.xml")
$profile = Get-Content -LiteralPath (Join-Path $runtimeRoot "local-profile.json") -Raw | ConvertFrom-Json
$controllerSecret = [System.Net.NetworkCredential]::new("", $secretMap["controller"])
$downloadSecret = [System.Net.NetworkCredential]::new("", $secretMap["download"])

$settings = @{
    APP_DATA_DIRECTORY = [string]$runtime.appDataDirectory
    USER_CONFIG_PATH = Join-Path $runtime.appDataDirectory "userConfig.json"
    MIGRATE_DATABASE_ON_STARTUP = "false"
    ENVIRONMENT = "local"
    PUBLIC_V2_INTEGRATION_PROFILE = "postgresql-strict"
    PUBLIC_ARTIFACT_STORE_BACKEND = "local-development"
    PUBLIC_WORKER_OBJECT_STORE_ROOT = Join-Path $runtime.runtimeRoot "local-artifact-authority"
    PUBLIC_TRUSTED_WORKER_SANDBOX_BACKEND = "windows-local"
    PUBLIC_TRUSTED_WORKER_LOCAL_POLICY = "development"
    PUBLIC_TRUSTED_WORKER_STAGING_ROOT = Join-Path $runtime.runtimeRoot "trusted-staging"
    PUBLIC_WORKER_CAPABILITY_KEY_FILE = Join-Path $runtime.runtimeRoot "worker-capability.key"
    PUBLIC_RENDER_WORKER_COMMAND_JSON = @($python, "-m", "workers.public_render_worker") | ConvertTo-Json -Compress
    PUBLIC_EXPORT_WORKER_COMMAND_JSON = @($python, "-m", "workers.public_export_worker") | ConvertTo-Json -Compress
    PUBLIC_WORKER_ARTIFACT_CACHE_MAX_BYTES = "1073741824"
    PUBLIC_WORKER_ARTIFACT_CACHE_TTL_SECONDS = "900"
    PPT_WORKER_OUTPUT_LIMIT_BYTES = "536870912"
    PUBLIC_EXPORT_IDENTITY_V2_ENABLED = "true"
    PUBLIC_RENDER_MAX_CONCURRENCY = "2"
    PUBLIC_V2_WINDOWS_PRODUCT_E2E = "1"
    PUBLIC_LOCAL_ARTIFACT_DOWNLOAD_SIGNING_KEY = $downloadSecret.Password
    PUBLIC_LOCAL_ARTIFACT_DOWNLOAD_ORIGIN = "http://127.0.0.1:8010"
    PPT_SERVICE_TENANT_ID = "default"
    PUBLIC_PROVIDER_PRICING_REVISION = [string]$profile.pricingCatalog.revision
    PUBLIC_PROVIDER_PRICING_CATALOG = $profile.pricingCatalog | ConvertTo-Json -Depth 20 -Compress
    WORKSPACE_OPERATION_WORKER_MODE = "external"
    DATABASE_URL = "postgresql+asyncpg://presenton_public_controller_login:$([System.Uri]::EscapeDataString($controllerSecret.Password))@127.0.0.1:$($runtime.databasePort)/postgres"
    PUBLIC_DATABASE_PROCESS_ROLE = "controller"
    PRESENTON_SKIP_PROVIDER_AVAILABILITY_CHECK = "false"
    PPT_PROCESS_PROFILE = "generation"
    WORKSPACE_OPERATION_EXTERNAL_CONTROLLER = "generation"
    PUBLIC_WORKER_NODE_ID = $NodeId
    AUTHORED_CLUSTER_JOB_CONCURRENCY = [string]$AuthoredConcurrency
}
foreach ($key in $settings.Keys) {
    [Environment]::SetEnvironmentVariable($key, [string]$settings[$key], "Process")
}

$logToken = $NodeId -replace "[^A-Za-z0-9._-]", "-"
$stdout = Join-Path $repo "artifacts\source-native-generation-worker-$logToken.stdout.log"
$stderr = Join-Path $repo "artifacts\source-native-generation-worker-$logToken.stderr.log"
$worker = Start-Process -FilePath $python -ArgumentList @("-m", "services.workspace_operation_worker") -WorkingDirectory $fastApi -PassThru -WindowStyle Hidden -RedirectStandardOutput $stdout -RedirectStandardError $stderr

[pscustomobject]@{
    generationWorkerPid = $worker.Id
    authoredConcurrency = $AuthoredConcurrency
    nodeId = $NodeId
} | ConvertTo-Json -Compress
