[CmdletBinding()]
param(
    [ValidateRange(1, 65535)]
    [int]$FastApiPort = 8000,

    [ValidateRange(1, 65535)]
    [int]$NextPort = 3000,

    [ValidateRange(1, 65535)]
    [int]$ProxyPort = 5000,

    [string]$DatabasePath,

    [string]$ProviderConfigPath,

    [string]$LogoSvgPath,

    [switch]$IsolatedDatabase,

    [switch]$AllowProviderSettings
)

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot
$fastApiRoot = Join-Path $repoRoot "servers\fastapi"
$nextRoot = Join-Path $repoRoot "servers\nextjs"
$appDataRoot = Join-Path $repoRoot "app_data"
$devSessionName = "session-$PID"
$devSessionRoot = Join-Path $appDataRoot "authored-vnext-dev\$devSessionName"
$persistentDatabasePath = Join-Path $appDataRoot "fastapi.db"
if ($DatabasePath -and $IsolatedDatabase) {
    throw "Pass either -DatabasePath or -IsolatedDatabase, not both."
}
if ($DatabasePath) {
    $selectedDatabasePath = [System.IO.Path]::GetFullPath($DatabasePath, $repoRoot)
}
elseif ($IsolatedDatabase) {
    $selectedDatabasePath = Join-Path $devSessionRoot "authored-vnext-dev.sqlite3"
}
else {
    # Keep documents visible across launcher restarts. Per-process databases are
    # still available explicitly for destructive or isolated test runs.
    $selectedDatabasePath = $persistentDatabasePath
}
$devDatabasePath = $selectedDatabasePath.Replace("\", "/")
$userConfigPath = if ([string]::IsNullOrWhiteSpace($ProviderConfigPath)) {
    Join-Path $appDataRoot "userConfig.json"
}
else {
    [System.IO.Path]::GetFullPath($ProviderConfigPath, $repoRoot)
}
if ($ProviderConfigPath -and -not (Test-Path -LiteralPath $userConfigPath -PathType Leaf)) {
    throw "Provider config file does not exist: $userConfigPath"
}
$resolvedLogoSvgPath = if ([string]::IsNullOrWhiteSpace($LogoSvgPath)) {
    $null
}
else {
    $resolved = [System.IO.Path]::GetFullPath($LogoSvgPath, $repoRoot)
    if (-not (Test-Path -LiteralPath $resolved -PathType Leaf)) {
        throw "Logo SVG file does not exist: $resolved"
    }
    $resolved
}
$logRoot = Join-Path $devSessionRoot "logs"
$nextDistDirectoryName = ".next-authored-vnext-dev-$PID"
$nextEnvPath = Join-Path $nextRoot "next-env.d.ts"
$nextTsConfigPath = Join-Path $nextRoot "tsconfig.json"

function Assert-CommandAvailable {
    param([Parameter(Mandatory = $true)][string]$Name)
    if (-not (Get-Command $Name -ErrorAction SilentlyContinue)) {
        throw "Required command is not available on PATH: $Name"
    }
}

function Assert-PortAvailable {
    param([Parameter(Mandatory = $true)][int]$Port)
    $listener = Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction SilentlyContinue
    if ($listener) {
        $owners = ($listener | Select-Object -ExpandProperty OwningProcess -Unique) -join ", "
        throw "Port $Port is already in use (PID: $owners). Stop that process or pass a different port."
    }
}

function Start-LoggedProcess {
    param(
        [Parameter(Mandatory = $true)][string]$Name,
        [Parameter(Mandatory = $true)][string]$FilePath,
        [Parameter(Mandatory = $true)][string[]]$ArgumentList,
        [Parameter(Mandatory = $true)][string]$WorkingDirectory,
        [Parameter(Mandatory = $true)][hashtable]$Environment,
        [Parameter(Mandatory = $true)][string]$StdoutPath,
        [Parameter(Mandatory = $true)][string]$StderrPath
    )

    $previousEnvironment = @{}
    foreach ($entry in $Environment.GetEnumerator()) {
        $existing = Get-Item -LiteralPath "Env:$($entry.Key)" -ErrorAction SilentlyContinue
        $previousEnvironment[$entry.Key] = if ($null -eq $existing) { $null } else { $existing.Value }
        Set-Item -LiteralPath "Env:$($entry.Key)" -Value ([string]$entry.Value)
    }
    try {
        $process = Start-Process `
            -FilePath $FilePath `
            -ArgumentList $ArgumentList `
            -WorkingDirectory $WorkingDirectory `
            -RedirectStandardOutput $StdoutPath `
            -RedirectStandardError $StderrPath `
            -WindowStyle Hidden `
            -PassThru
    }
    finally {
        foreach ($entry in $previousEnvironment.GetEnumerator()) {
            if ($null -eq $entry.Value) {
                Remove-Item -LiteralPath "Env:$($entry.Key)" -ErrorAction SilentlyContinue
            }
            else {
                Set-Item -LiteralPath "Env:$($entry.Key)" -Value $entry.Value
            }
        }
    }
    Write-Host ("Started {0} (PID {1})" -f $Name, $process.Id)
    return $process
}

function Wait-HttpReady {
    param(
        [Parameter(Mandatory = $true)][string]$Name,
        [Parameter(Mandatory = $true)][string]$Uri,
        [Parameter(Mandatory = $true)][System.Diagnostics.Process]$Process,
        [int]$TimeoutSeconds = 180
    )

    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    do {
        if ($Process.HasExited) {
            throw "$Name exited before it became ready (exit code $($Process.ExitCode))."
        }
        try {
            $response = Invoke-WebRequest -UseBasicParsing -Uri $Uri -TimeoutSec 3
            if ($response.StatusCode -ge 200 -and $response.StatusCode -lt 300) {
                return
            }
        }
        catch {
            # The service may still be importing, migrating, or compiling.
        }
        Start-Sleep -Milliseconds 500
    } while ((Get-Date) -lt $deadline)
    throw "$Name did not become ready within $TimeoutSeconds seconds: $Uri"
}

function Restore-NextGeneratedProjectFiles {
    param(
        [Parameter(Mandatory = $true)][byte[]]$NextEnvOriginal,
        [Parameter(Mandatory = $true)][byte[]]$TsConfigOriginal,
        [Parameter(Mandatory = $true)][string]$DistDirectoryName
    )

    $utf8 = [System.Text.UTF8Encoding]::new($false)
    $normalize = {
        param([string]$Value)
        return $Value.Replace("`r`n", "`n")
    }

    if (Test-Path -LiteralPath $nextEnvPath -PathType Leaf) {
        $originalText = & $normalize ($utf8.GetString($NextEnvOriginal))
        $currentText = & $normalize ([System.IO.File]::ReadAllText($nextEnvPath))
        $routeImportPattern = '(?m)^import "\./[^"\r\n]+/(?:dev/)?types/routes\.d\.ts";$'
        $expectedDev = [regex]::Replace(
            $originalText,
            $routeImportPattern,
            "import `"./$DistDirectoryName/dev/types/routes.d.ts`";",
            1
        )
        $expectedBuild = [regex]::Replace(
            $originalText,
            $routeImportPattern,
            "import `"./$DistDirectoryName/types/routes.d.ts`";",
            1
        )
        if ($currentText -eq $expectedDev -or $currentText -eq $expectedBuild -or $currentText -eq $originalText) {
            if ($currentText -ne $originalText) {
                [System.IO.File]::WriteAllBytes($nextEnvPath, $NextEnvOriginal)
            }
        }
        else {
            Write-Warning "next-env.d.ts changed beyond the exact Next.js generated route import; leaving it untouched."
        }
    }

    if (Test-Path -LiteralPath $nextTsConfigPath -PathType Leaf) {
        try {
            $originalConfig = ($utf8.GetString($TsConfigOriginal) | ConvertFrom-Json)
            $currentConfig = (Get-Content -Raw -LiteralPath $nextTsConfigPath | ConvertFrom-Json)
            # Next can append either this launch's exact PID directory or stale
            # generated authored-vNext paths left by an interrupted launcher.
            # They are derived type outputs, never source includes. Remove only
            # that generated family; preserve every unrelated user include.
            $currentConfig.include = @(
                $currentConfig.include | Where-Object {
                    $_ -notmatch '^\.next-authored-vnext-dev-[0-9]+/(?:dev/)?types/\*\*/\*\.ts$'
                }
            )
            $originalComparable = $originalConfig | ConvertTo-Json -Depth 100 -Compress
            $currentComparable = $currentConfig | ConvertTo-Json -Depth 100 -Compress
            if ($currentComparable -eq $originalComparable) {
                [System.IO.File]::WriteAllBytes($nextTsConfigPath, $TsConfigOriginal)
            }
            else {
                Write-Warning "tsconfig.json changed beyond the exact Next.js generated includes; leaving it untouched."
            }
        }
        catch {
            Write-Warning "Could not safely classify Next.js changes to tsconfig.json; leaving it untouched."
        }
    }
}

Assert-CommandAvailable "uv.exe"
Assert-CommandAvailable "node.exe"
Assert-CommandAvailable "npm.cmd"
Assert-PortAvailable $FastApiPort
Assert-PortAvailable $NextPort
Assert-PortAvailable $ProxyPort

New-Item -ItemType Directory -Path $appDataRoot -Force | Out-Null
New-Item -ItemType Directory -Path $devSessionRoot -Force | Out-Null
New-Item -ItemType Directory -Path $logRoot -Force | Out-Null
New-Item -ItemType Directory -Path (Split-Path -Parent $selectedDatabasePath) -Force | Out-Null
$nextEnvOriginalBytes = [System.IO.File]::ReadAllBytes($nextEnvPath)
$nextTsConfigOriginalBytes = [System.IO.File]::ReadAllBytes($nextTsConfigPath)

$workerTokenBytes = New-Object byte[] 32
$randomNumberGenerator = [System.Security.Cryptography.RandomNumberGenerator]::Create()
try {
    $randomNumberGenerator.GetBytes($workerTokenBytes)
}
finally {
    $randomNumberGenerator.Dispose()
}
$workerToken = [BitConverter]::ToString($workerTokenBytes).Replace("-", "").ToLowerInvariant()
$proxyUrl = "http://127.0.0.1:$ProxyPort"
$fastApiUrl = "http://127.0.0.1:$FastApiPort"

$sharedAuthoredEnvironment = @{
    "APP_DATA_DIRECTORY" = $appDataRoot
    "USER_CONFIG_PATH" = $userConfigPath
    "CAN_CHANGE_KEYS" = if ($AllowProviderSettings) { "true" } else { "false" }
    "ENVIRONMENT" = "local"
    "DISABLE_AUTH" = "true"
    # A fresh clean worktree has no legacy credential sidecar. Seed a local-only
    # bootstrap principal so DISABLE_AUTH can still establish request ownership.
    # The random value is process-scoped and only its hash is persisted.
    "AUTH_USERNAME" = "authored-vnext-local"
    "AUTH_PASSWORD" = $workerToken
    "DISABLE_ANONYMOUS_TRACKING" = "true"
    "DISABLE_IMAGE_GENERATION" = "true"
    "AUTHORED_SOURCE_IMAGES_ENABLED" = "true"
    "AUTHORED_VNEXT_MASTER_ENABLED" = "true"
    "AUTHORED_VNEXT_GENERATION_ENABLED" = "true"
    "AUTHORED_VNEXT_EXPORT_ENABLED" = "true"
    "AUTHORED_VNEXT_OOXML_PACKAGE_ENABLED" = "true"
    "AUTHORED_VNEXT_REPOSITORY_MODE" = "sql"
    "AUTHORED_VNEXT_LOCAL_SLIDE_COUNT" = "3"
    "AUTHORED_ONLY_NEW_PRESENTATION_ADMISSION" = "true"
    "AUTHORED_SEMANTIC_DUAL_OUTPUT_MODE" = "declared"
    "AUTHORED_SEMANTIC_BUNDLE_WRITES_ENABLED" = "true"
    "AUTHORED_VNEXT_OOXML_WORKER_TOKEN" = $workerToken
}

$fastApiEnvironment = $sharedAuthoredEnvironment.Clone()
$fastApiEnvironment["DATABASE_URL"] = "sqlite:///$devDatabasePath"
$fastApiEnvironment["MIGRATE_DATABASE_ON_STARTUP"] = "true"
# Server-to-server OOXML envelopes can contain several megabytes of verified
# native template fragments. Send them directly to Next instead of routing the
# body through the browser-facing single-origin proxy.
$fastApiEnvironment["NEXT_INTERNAL_URL"] = "http://127.0.0.1:$NextPort"
$fastApiEnvironment["NEXT_PUBLIC_URL"] = $proxyUrl
# Explicitly forward only release/QA inputs that FastAPI consumes. Values are
# deliberately neither logged nor copied into the browser-facing environment.
$fastApiReleaseQaEnvironmentKeys = @(
    "AUTHORED_POWERPOINT_QA_INTERACTIVE_OBSERVATION",
    "AUTHORED_RELEASE_DEPLOYMENT_ENVIRONMENT",
    "AUTHORED_RELEASE_OS_BUILD",
    "AUTHORED_RELEASE_OFFICE_BUILD",
    "AUTHORED_RELEASE_RENDERER_IMAGE_DIGEST",
    "AUTHORED_RELEASE_FONT_INVENTORY_DIGEST",
    "AUTHORED_RELEASE_BACKEND_BUILD_DIGEST",
    "AUTHORED_RELEASE_BACKEND_COMMIT_SHA",
    "AUTHORED_RELEASE_KILL_SWITCHES",
    "AUTHORED_RELEASE_VISUAL_GATE_HMAC_KEY",
    "AUTHORED_RELEASE_CI_EVIDENCE_HMAC_KEY",
    "AUTHORED_EDITABLE_MVP_TRUST_SHA256"
)
foreach ($environmentKey in $fastApiReleaseQaEnvironmentKeys) {
    $environmentValue = [Environment]::GetEnvironmentVariable($environmentKey)
    if (-not [string]::IsNullOrWhiteSpace($environmentValue)) {
        $fastApiEnvironment[$environmentKey] = $environmentValue
    }
}

$nextEnvironment = $sharedAuthoredEnvironment.Clone()
# Keep the deterministic verification UI locked by default. Interactive local
# generation must opt in explicitly so an administrator can configure a text
# provider without weakening the release-verification launch contract.
$nextEnvironment["FAST_API_INTERNAL_URL"] = $fastApiUrl
$nextEnvironment["NEXT_INTERNAL_URL"] = $proxyUrl
$nextEnvironment["NEXT_PUBLIC_URL"] = $proxyUrl
$nextEnvironment["NEXT_PUBLIC_AUTHORED_VNEXT_MASTER_ENABLED"] = "true"
$nextEnvironment["NEXT_PUBLIC_AUTHORED_VNEXT_DEV_SLIDE_COUNT"] = "3"
$nextEnvironment["AUTHORED_VNEXT_RENDERER_ENABLED"] = "true"
$nextEnvironment["PRESENTON_TEST_NEXT_DIST_DIR"] = $nextDistDirectoryName
$nextEnvironment["CYPRESS_BASE_URL"] = $proxyUrl
$nextEnvironment["WORKSPACE_LIVE_BACKEND"] = $fastApiUrl
# Bounded Hybrid v3 has no Gate -1-qualified native subset.  Do not inherit
# this from the parent environment: an absent or misspelled value must remain
# raster-only and must match the Gate 1 runtime pin.
$nextEnvironment["AUTHORED_HYBRID_BOUNDED_NATIVE_MODE"] = "raster-only"

$proxyEnvironment = @{
    "FASTAPI_PORT" = [string]$FastApiPort
    "NEXT_PORT" = [string]$NextPort
    "PROXY_PORT" = [string]$ProxyPort
}

$processes = [System.Collections.Generic.List[System.Diagnostics.Process]]::new()
try {
    $fastApi = Start-LoggedProcess `
        -Name "FastAPI" `
        -FilePath "uv.exe" `
        -ArgumentList @("run", "python", "server.py", "--port", [string]$FastApiPort, "--reload", "false") `
        -WorkingDirectory $fastApiRoot `
        -Environment $fastApiEnvironment `
        -StdoutPath (Join-Path $logRoot "fastapi.stdout.log") `
        -StderrPath (Join-Path $logRoot "fastapi.stderr.log")
    $processes.Add($fastApi)
    Wait-HttpReady -Name "FastAPI" -Uri "$fastApiUrl/metrics/workspace" -Process $fastApi

    if ($ProviderConfigPath) {
        # An existing local provider file can be newer than this worktree's
        # already-created provider_settings row. Import it explicitly through
        # the same CAS bridge used by synchronous OAuth writers; no credential
        # value is passed on the command line or written to the launcher logs.
        $providerConfigSync = Start-LoggedProcess `
            -Name "existing provider settings sync" `
            -FilePath "uv.exe" `
            -ArgumentList @(
                "run",
                "python",
                "scripts\sync_existing_provider_settings.py"
            ) `
            -WorkingDirectory $fastApiRoot `
            -Environment $fastApiEnvironment `
            -StdoutPath (Join-Path $logRoot "provider-settings-sync.stdout.log") `
            -StderrPath (Join-Path $logRoot "provider-settings-sync.stderr.log")
        $providerConfigSync.WaitForExit()
        $providerConfigSync.Refresh()
        if ($providerConfigSync.ExitCode -ne 0) {
            throw "Existing provider settings sync failed (exit code $($providerConfigSync.ExitCode)). See $logRoot."
        }
    }

    $catalogSeedArguments = @("run", "python", "scripts\seed_authored_vnext_master_catalog.py")
    if ($resolvedLogoSvgPath) {
        # Start-Process joins ArgumentList with spaces, so preserve this leaf
        # path as one argv token for Windows paths containing spaces or Hangul.
        $catalogSeedArguments += @("--logo-svg", ('"{0}"' -f $resolvedLogoSvgPath))
    }
    $catalogSeed = Start-LoggedProcess `
        -Name "PPT Master catalog seed" `
        -FilePath "uv.exe" `
        -ArgumentList $catalogSeedArguments `
        -WorkingDirectory $fastApiRoot `
        -Environment $fastApiEnvironment `
        -StdoutPath (Join-Path $logRoot "master-seed.stdout.log") `
        -StderrPath (Join-Path $logRoot "master-seed.stderr.log")
    $catalogSeed.WaitForExit()
    $catalogSeed.Refresh()
    $catalogSeedExitCode = $catalogSeed.ExitCode
    if ($null -eq $catalogSeedExitCode) {
        try {
            $catalogSeedReport = Get-Content -Raw -LiteralPath (Join-Path $logRoot "master-seed.stdout.log") | ConvertFrom-Json
            if (-not $catalogSeedReport.masters) {
                throw "seed report did not contain masters"
            }
            $catalogSeedExitCode = 0
        }
        catch {
            $catalogSeedExitCode = 1
        }
    }
    if ($catalogSeedExitCode -ne 0) {
        throw "PPT Master catalog seed failed (exit code $catalogSeedExitCode). See $logRoot."
    }

    $next = Start-LoggedProcess `
        -Name "Next.js" `
        -FilePath "npm.cmd" `
        -ArgumentList @("run", "dev", "--", "-p", [string]$NextPort) `
        -WorkingDirectory $nextRoot `
        -Environment $nextEnvironment `
        -StdoutPath (Join-Path $logRoot "next.stdout.log") `
        -StderrPath (Join-Path $logRoot "next.stderr.log")
    $processes.Add($next)
    Wait-HttpReady -Name "Next.js" -Uri "http://127.0.0.1:$NextPort/upload" -Process $next -TimeoutSeconds 240
    # Next dev rewrites these tracked project files when it registers the
    # per-session dist directory. Restore only those exact generated edits as
    # soon as startup is ready so release source censuses remain stable while
    # the official local stack is running.
    Restore-NextGeneratedProjectFiles `
        -NextEnvOriginal $nextEnvOriginalBytes `
        -TsConfigOriginal $nextTsConfigOriginalBytes `
        -DistDirectoryName $nextDistDirectoryName

    $proxy = Start-LoggedProcess `
        -Name "single-origin proxy" `
        -FilePath "node.exe" `
        -ArgumentList @((Join-Path $repoRoot "scripts\presenton-local-proxy.mjs")) `
        -WorkingDirectory $repoRoot `
        -Environment $proxyEnvironment `
        -StdoutPath (Join-Path $logRoot "proxy.stdout.log") `
        -StderrPath (Join-Path $logRoot "proxy.stderr.log")
    $processes.Add($proxy)
    Wait-HttpReady -Name "single-origin proxy" -Uri "$proxyUrl/api/v1/ppt/authored-vnext/master/status" -Process $proxy

    Write-Host ""
    Write-Host "Presenton with PPT Master local development is ready."
    Write-Host "Open the existing flow: $proxyUrl/upload"
    Write-Host "Local slide count: 3"
    Write-Host "New-presentation engine: Authored only (vNext independent generation frozen; compatibility open/export preserved)"
    Write-Host "Database: $devDatabasePath"
    Write-Host "Logs: $logRoot"
    Write-Host "Press Ctrl+C to stop only the three processes started by this script."

    while ($true) {
        foreach ($process in $processes) {
            if ($process.HasExited) {
                throw "A development child process exited unexpectedly (PID $($process.Id), code $($process.ExitCode))."
            }
        }
        Start-Sleep -Seconds 1
    }
}
finally {
    foreach ($process in $processes) {
        $rootStillExists = Get-Process -Id $process.Id -ErrorAction SilentlyContinue
        if ($rootStillExists) {
            # Stop only the tree rooted at the exact wrapper PID this script
            # created. npm.cmd and uv.exe may otherwise leave node/python
            # children holding the development ports after Ctrl+C.
            & taskkill.exe /PID $process.Id /T /F 2>$null | Out-Null
            Wait-Process -Id $process.Id -Timeout 10 -ErrorAction SilentlyContinue
        }
    }
    Restore-NextGeneratedProjectFiles `
        -NextEnvOriginal $nextEnvOriginalBytes `
        -TsConfigOriginal $nextTsConfigOriginalBytes `
        -DistDirectoryName $nextDistDirectoryName
}
