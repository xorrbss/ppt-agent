[CmdletBinding()]
param(
    [ValidateRange(1024, 65535)]
    [int]$ApiPort = 8010
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

function Stop-VerifiedTree([int]$RootPid) {
    $root = Get-CimInstance Win32_Process -Filter "ProcessId=$RootPid" -ErrorAction SilentlyContinue
    if ($null -eq $root) { return }
    $expected = [string]$root.CommandLine
    if ($expected -notmatch [regex]::Escape($repo) -or $expected -notmatch "server.py|services.workspace_operation_worker") {
        throw "public-v2 local process ownership mismatch: $RootPid"
    }
    $pending = [System.Collections.Generic.Queue[int]]::new()
    $ordered = [System.Collections.Generic.List[int]]::new()
    $pending.Enqueue($RootPid)
    while ($pending.Count -gt 0) {
        $current = $pending.Dequeue()
        $ordered.Add($current)
        foreach ($child in @(Get-CimInstance Win32_Process -Filter "ParentProcessId=$current" -ErrorAction SilentlyContinue)) {
            $pending.Enqueue([int]$child.ProcessId)
        }
    }
    for ($index = $ordered.Count - 1; $index -ge 0; $index--) {
        Stop-Process -Id $ordered[$index] -Force -ErrorAction SilentlyContinue
    }
}

$listener = Get-NetTCPConnection -State Listen -LocalPort $ApiPort -ErrorAction SilentlyContinue | Select-Object -First 1
if ($null -ne $listener) {
    $listenerProcess = Get-CimInstance Win32_Process -Filter "ProcessId=$($listener.OwningProcess)"
    $rootPid = [int]$listenerProcess.ProcessId
    while ($null -ne $listenerProcess -and [int]$listenerProcess.ParentProcessId -gt 0) {
        $parent = Get-CimInstance Win32_Process -Filter "ProcessId=$($listenerProcess.ParentProcessId)" -ErrorAction SilentlyContinue
        if ($null -eq $parent -or [string]$parent.CommandLine -notmatch [regex]::Escape($repo)) { break }
        $rootPid = [int]$parent.ProcessId
        $listenerProcess = $parent
    }
    Stop-VerifiedTree $rootPid
}

$workerRoots = Get-CimInstance Win32_Process | Where-Object {
    $_.ExecutablePath -eq $python -and $_.CommandLine -match "services.workspace_operation_worker"
}
foreach ($worker in $workerRoots) { Stop-VerifiedTree ([int]$worker.ProcessId) }

$deadline = [DateTime]::UtcNow.AddSeconds(20)
do {
    $remaining = Get-NetTCPConnection -State Listen -LocalPort $ApiPort -ErrorAction SilentlyContinue
    if ($null -eq $remaining) { break }
    Start-Sleep -Milliseconds 250
} while ([DateTime]::UtcNow -lt $deadline)
if ($null -ne $remaining) { throw "public-v2 FastAPI listener did not stop" }

$apiSecret = [System.Net.NetworkCredential]::new("", $secretMap["api"])
$controllerSecret = [System.Net.NetworkCredential]::new("", $secretMap["controller"])
$downloadSecret = [System.Net.NetworkCredential]::new("", $secretMap["download"])
$common = @{
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
}
foreach ($key in $common.Keys) {
    [Environment]::SetEnvironmentVariable($key, [string]$common[$key], "Process")
}

$env:DATABASE_URL = "postgresql+asyncpg://presenton_public_api_login:$([System.Uri]::EscapeDataString($apiSecret.Password))@127.0.0.1:$($runtime.databasePort)/postgres"
$env:PUBLIC_DATABASE_PROCESS_ROLE = "api"
$env:PUBLIC_WORKER_NODE_ID = "public-v2-source-native-api"
$env:PRESENTON_SKIP_PROVIDER_AVAILABILITY_CHECK = "true"
$api = Start-Process -FilePath $python -ArgumentList @("server.py", "--port", "$ApiPort", "--reload", "false") -WorkingDirectory $fastApi -PassThru -WindowStyle Hidden -RedirectStandardOutput (Join-Path $repo "artifacts\source-native-fastapi.stdout.log") -RedirectStandardError (Join-Path $repo "artifacts\source-native-fastapi.stderr.log")

$deadline = [DateTime]::UtcNow.AddSeconds(90)
do {
    Start-Sleep -Milliseconds 500
    $listener = Get-NetTCPConnection -State Listen -LocalPort $ApiPort -ErrorAction SilentlyContinue | Select-Object -First 1
} while ($null -eq $listener -and [DateTime]::UtcNow -lt $deadline)
if ($null -eq $listener) { throw "public-v2 FastAPI restart timed out" }

$env:DATABASE_URL = "postgresql+asyncpg://presenton_public_controller_login:$([System.Uri]::EscapeDataString($controllerSecret.Password))@127.0.0.1:$($runtime.databasePort)/postgres"
$env:PUBLIC_DATABASE_PROCESS_ROLE = "controller"
$env:PRESENTON_SKIP_PROVIDER_AVAILABILITY_CHECK = "true"
$env:PPT_PROCESS_PROFILE = "ppt-core"
$env:WORKSPACE_OPERATION_EXTERNAL_CONTROLLER = "export"
$env:PUBLIC_WORKER_NODE_ID = "public-v2-source-native-export"
$exportWorker = Start-Process -FilePath $python -ArgumentList @("-m", "services.workspace_operation_worker") -WorkingDirectory $fastApi -PassThru -WindowStyle Hidden -RedirectStandardOutput (Join-Path $repo "artifacts\source-native-export-worker.stdout.log") -RedirectStandardError (Join-Path $repo "artifacts\source-native-export-worker.stderr.log")

$env:PRESENTON_SKIP_PROVIDER_AVAILABILITY_CHECK = "false"
$env:PPT_PROCESS_PROFILE = "generation"
$env:WORKSPACE_OPERATION_EXTERNAL_CONTROLLER = "generation"
$env:PUBLIC_WORKER_NODE_ID = "public-v2-source-native-generation"
$generationWorker = Start-Process -FilePath $python -ArgumentList @("-m", "services.workspace_operation_worker") -WorkingDirectory $fastApi -PassThru -WindowStyle Hidden -RedirectStandardOutput (Join-Path $repo "artifacts\source-native-generation-worker.stdout.log") -RedirectStandardError (Join-Path $repo "artifacts\source-native-generation-worker.stderr.log")

[pscustomobject]@{
    apiPid = $api.Id
    apiListenerPid = $listener.OwningProcess
    exportWorkerPid = $exportWorker.Id
    generationWorkerPid = $generationWorker.Id
} | ConvertTo-Json -Compress
