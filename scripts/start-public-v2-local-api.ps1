[CmdletBinding()]
param(
    [ValidateRange(1024, 65535)]
    [int]$ApiPort = 8011
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
$apiSecret = [System.Net.NetworkCredential]::new("", $secretMap["api"])
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
    PUBLIC_LOCAL_ARTIFACT_DOWNLOAD_ORIGIN = "http://127.0.0.1:$ApiPort"
    PPT_SERVICE_TENANT_ID = "default"
    PUBLIC_PROVIDER_PRICING_REVISION = [string]$profile.pricingCatalog.revision
    PUBLIC_PROVIDER_PRICING_CATALOG = $profile.pricingCatalog | ConvertTo-Json -Depth 20 -Compress
    WORKSPACE_OPERATION_WORKER_MODE = "external"
    DATABASE_URL = "postgresql+asyncpg://presenton_public_api_login:$([System.Uri]::EscapeDataString($apiSecret.Password))@127.0.0.1:$($runtime.databasePort)/postgres"
    PUBLIC_DATABASE_PROCESS_ROLE = "api"
    PUBLIC_WORKER_NODE_ID = "public-v2-source-native-api-$ApiPort"
    PRESENTON_SKIP_PROVIDER_AVAILABILITY_CHECK = "true"
}
foreach ($key in $settings.Keys) {
    [Environment]::SetEnvironmentVariable($key, [string]$settings[$key], "Process")
}

$stdout = Join-Path $repo "artifacts\source-native-fastapi-$ApiPort.stdout.log"
$stderr = Join-Path $repo "artifacts\source-native-fastapi-$ApiPort.stderr.log"
$api = Start-Process -FilePath $python -ArgumentList @("server.py", "--port", "$ApiPort", "--reload", "false") -WorkingDirectory $fastApi -PassThru -WindowStyle Hidden -RedirectStandardOutput $stdout -RedirectStandardError $stderr

$deadline = [DateTime]::UtcNow.AddSeconds(90)
do {
    Start-Sleep -Milliseconds 500
    $listener = Get-NetTCPConnection -State Listen -LocalPort $ApiPort -ErrorAction SilentlyContinue | Select-Object -First 1
} while ($null -eq $listener -and [DateTime]::UtcNow -lt $deadline)
if ($null -eq $listener) { throw "public-v2 FastAPI start timed out" }

[pscustomobject]@{
    apiPid = $api.Id
    apiListenerPid = $listener.OwningProcess
    port = $ApiPort
} | ConvertTo-Json -Compress
