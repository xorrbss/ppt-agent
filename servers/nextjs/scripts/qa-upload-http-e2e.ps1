param(
  [int]$FastApiPort = 8018,
  [int]$NextPort = 3018
)

$ErrorActionPreference = "Stop"
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..\..")).Path
$stamp = Get-Date -Format "yyyyMMdd-HHmmss"
$qaRoot = Join-Path $repoRoot ".tmp-upload-http-e2e\$stamp"
$qaData = Join-Path $qaRoot "app_data"
$qaBackendTemp = Join-Path $qaRoot "backend_temp"
New-Item -ItemType Directory -Path $qaData -Force | Out-Null
New-Item -ItemType Directory -Path $qaBackendTemp -Force | Out-Null

$username = "e2e-admin"
$password = "e2e-only-Strong-2026"
$reportPath = Join-Path $qaRoot "report.json"
$fastApiProcess = $null
$nextProcess = $null
$results = [ordered]@{
  qa_root = $qaRoot
  fastapi_origin = "http://127.0.0.1:$FastApiPort"
  next_origin = "http://127.0.0.1:$NextPort"
  passed = $false
  checks = [ordered]@{}
}

function Assert-Port-Free([int]$Port) {
  if (Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction SilentlyContinue) {
    throw "Port $Port is already in use; refusing to touch an existing process."
  }
}

function Wait-Http([string]$Url, [int]$Seconds = 30) {
  $deadline = (Get-Date).AddSeconds($Seconds)
  while ((Get-Date) -lt $deadline) {
    try {
      $response = Invoke-WebRequest -UseBasicParsing -Uri $Url -TimeoutSec 1
      if ($response.StatusCode -ge 200) { return }
    } catch {
      Start-Sleep -Milliseconds 500
    }
  }
  throw "Timed out waiting for $Url"
}

function New-SizedFile([string]$Path, [long]$Length, [byte[]]$Prefix) {
  $stream = [System.IO.File]::Open(
    $Path,
    [System.IO.FileMode]::CreateNew,
    [System.IO.FileAccess]::Write,
    [System.IO.FileShare]::None
  )
  try {
    $stream.Write($Prefix, 0, $Prefix.Length)
    $stream.SetLength($Length)
  } finally {
    $stream.Dispose()
  }
}

function Curl-Status([string[]]$Arguments) {
  $status = & curl.exe @Arguments
  if ($LASTEXITCODE -ne 0) {
    throw "curl failed ($LASTEXITCODE): $($Arguments -join ' ')"
  }
  return [int]$status
}

Assert-Port-Free $FastApiPort
Assert-Port-Free $NextPort

try {
  $fastApiOut = Join-Path $qaRoot "fastapi.stdout.log"
  $fastApiErr = Join-Path $qaRoot "fastapi.stderr.log"
  $fastApiStart = New-Object System.Diagnostics.ProcessStartInfo
  $fastApiStart.FileName = "uv"
  $fastApiStart.WorkingDirectory = Join-Path $repoRoot "servers\fastapi"
  $fastApiStart.Arguments = "run python server.py --port $FastApiPort --reload false"
  $fastApiStart.UseShellExecute = $false
  $fastApiStart.CreateNoWindow = $true
  $fastApiStart.RedirectStandardOutput = $true
  $fastApiStart.RedirectStandardError = $true
  $fastApiStart.Environment["APP_DATA_DIRECTORY"] = $qaData
  $fastApiStart.Environment["TEMP_DIRECTORY"] = $qaBackendTemp
  $fastApiStart.Environment["USER_CONFIG_PATH"] = Join-Path $qaData "userConfig.json"
  $fastApiStart.Environment["NEXT_INTERNAL_URL"] = "http://127.0.0.1:$NextPort"
  $fastApiStart.Environment["AUTH_USERNAME"] = $username
  $fastApiStart.Environment["AUTH_PASSWORD"] = $password
  $fastApiStart.Environment["AUTH_OVERRIDE_FROM_ENV"] = "true"
  $fastApiStart.Environment["DISABLE_AUTH"] = "false"
  $fastApiStart.Environment["MIGRATE_DATABASE_ON_STARTUP"] = "false"
  $fastApiStart.Environment["PRESENTON_MAX_UPLOAD_MB"] = "100"
  $fastApiStart.Environment["PRESENTON_MAX_UPLOAD_TOTAL_MB"] = "512"
  $fastApiStart.Environment["PRESENTON_MAX_IMAGE_UPLOAD_MB"] = "1"
  $fastApiProcess = [System.Diagnostics.Process]::Start($fastApiStart)
  $fastApiProcess.BeginOutputReadLine()
  $fastApiProcess.BeginErrorReadLine()
  Register-ObjectEvent $fastApiProcess OutputDataReceived -Action {
    if ($EventArgs.Data) { Add-Content -LiteralPath $using:fastApiOut -Value $EventArgs.Data }
  } | Out-Null
  Register-ObjectEvent $fastApiProcess ErrorDataReceived -Action {
    if ($EventArgs.Data) { Add-Content -LiteralPath $using:fastApiErr -Value $EventArgs.Data }
  } | Out-Null
  Wait-Http "http://127.0.0.1:$FastApiPort/api/v1/auth/status"

  $unauthStatus = Curl-Status @(
    "-sS", "-o", (Join-Path $qaRoot "fastapi-unauth.json"), "-w", "%{http_code}",
    "http://127.0.0.1:$FastApiPort/api/v1/ppt/files/upload-limits"
  )
  $results.checks.fastapi_unauthenticated = $unauthStatus

  $loginBody = @{
    username = $username
    password = $password
  } | ConvertTo-Json -Compress
  $loginResponse = Invoke-WebRequest -UseBasicParsing `
    -Uri "http://127.0.0.1:$FastApiPort/api/v1/auth/login" `
    -Method Post `
    -Body $loginBody `
    -ContentType "application/json" `
    -SessionVariable authSession
  $sessionCookie = $authSession.Cookies.GetCookies(
    [Uri]"http://127.0.0.1:$FastApiPort"
  )["presenton_session"]
  if ($null -eq $sessionCookie) {
    throw "Login succeeded but did not return presenton_session."
  }
  $cookieHeader = "presenton_session=$($sessionCookie.Value)"
  $results.checks.login = $loginResponse.StatusCode

  $limitsPath = Join-Path $qaRoot "fastapi-limits.json"
  $limitStatus = Curl-Status @(
    "-sS", "-b", $cookieHeader, "-o", $limitsPath, "-w", "%{http_code}",
    "http://127.0.0.1:$FastApiPort/api/v1/ppt/files/upload-limits"
  )
  $limits = Get-Content -LiteralPath $limitsPath -Raw | ConvertFrom-Json
  $results.checks.fastapi_limits = [ordered]@{
    status = $limitStatus
    single_file_mb = $limits.single_file_mb
    request_total_mb = $limits.request_total_mb
    image_mb = $limits.image_mb
  }

  $exactDocument = Join-Path $qaRoot "exact-100MiB.pdf"
  $overDocument = Join-Path $qaRoot "over-100MiB.pdf"
  New-SizedFile $exactDocument (100MB) ([Text.Encoding]::ASCII.GetBytes("%PDF-1.4`n"))
  New-SizedFile $overDocument ((100MB) + 1) ([Text.Encoding]::ASCII.GetBytes("%PDF-1.4`n"))
  $results.checks.document_exact_boundary = Curl-Status @(
    "-sS", "-u", "${username}:${password}", "-o", (Join-Path $qaRoot "upload-exact.json"),
    "-w", "%{http_code}", "-F", "files=@$exactDocument;type=application/pdf",
    "http://127.0.0.1:$FastApiPort/api/v1/ppt/files/upload"
  )
  $results.checks.document_over_boundary = Curl-Status @(
    "-sS", "-u", "${username}:${password}", "-o", (Join-Path $qaRoot "upload-over.json"),
    "-w", "%{http_code}", "-F", "files=@$overDocument;type=application/pdf",
    "http://127.0.0.1:$FastApiPort/api/v1/ppt/files/upload"
  )

  $nextOut = Join-Path $qaRoot "next.stdout.log"
  $nextErr = Join-Path $qaRoot "next.stderr.log"
  $nextStart = New-Object System.Diagnostics.ProcessStartInfo
  $nextStart.FileName = "node"
  $nextStart.WorkingDirectory = Join-Path $repoRoot "servers\nextjs"
  $nextStart.Arguments = "node_modules/next/dist/bin/next dev -p $NextPort"
  $nextStart.UseShellExecute = $false
  $nextStart.CreateNoWindow = $true
  $nextStart.RedirectStandardOutput = $true
  $nextStart.RedirectStandardError = $true
  $nextStart.Environment["APP_DATA_DIRECTORY"] = $qaData
  $nextStart.Environment["USER_CONFIG_PATH"] = Join-Path $qaData "userConfig.json"
  $nextStart.Environment["FAST_API_INTERNAL_URL"] = "http://127.0.0.1:$FastApiPort"
  $nextStart.Environment["DISABLE_AUTH"] = "false"
  $nextStart.Environment["PRESENTON_MAX_UPLOAD_MB"] = "100"
  $nextStart.Environment["PRESENTON_MAX_UPLOAD_TOTAL_MB"] = "512"
  $nextStart.Environment["PRESENTON_MAX_IMAGE_UPLOAD_MB"] = "1"
  $nextProcess = [System.Diagnostics.Process]::Start($nextStart)
  $nextProcess.BeginOutputReadLine()
  $nextProcess.BeginErrorReadLine()
  Register-ObjectEvent $nextProcess OutputDataReceived -Action {
    if ($EventArgs.Data) { Add-Content -LiteralPath $using:nextOut -Value $EventArgs.Data }
  } | Out-Null
  Register-ObjectEvent $nextProcess ErrorDataReceived -Action {
    if ($EventArgs.Data) { Add-Content -LiteralPath $using:nextErr -Value $EventArgs.Data }
  } | Out-Null
  Wait-Http "http://127.0.0.1:$NextPort"

  $results.checks.next_unauthenticated = Curl-Status @(
    "-sS", "-o", (Join-Path $qaRoot "next-unauth.json"), "-w", "%{http_code}",
    "http://127.0.0.1:$NextPort/api/upload-limits"
  )
  $nextLimitsPath = Join-Path $qaRoot "next-limits.json"
  $results.checks.next_limits = Curl-Status @(
    "-sS", "-b", $cookieHeader, "-o", $nextLimitsPath, "-w", "%{http_code}",
    "http://127.0.0.1:$NextPort/api/upload-limits"
  )

  $exactImage = Join-Path $qaRoot "exact-1MiB.png"
  $overImage = Join-Path $qaRoot "over-1MiB.png"
  New-SizedFile $exactImage (1MB) ([byte[]](0x89,0x50,0x4e,0x47,0x0d,0x0a,0x1a,0x0a))
  New-SizedFile $overImage ((1MB) + 1) ([byte[]](0x89,0x50,0x4e,0x47,0x0d,0x0a,0x1a,0x0a))
  $results.checks.image_exact_boundary = Curl-Status @(
    "-sS", "-b", $cookieHeader, "-o", (Join-Path $qaRoot "image-exact.json"),
    "-w", "%{http_code}", "-F", "file=@$exactImage;type=image/png",
    "http://127.0.0.1:$NextPort/api/upload-image"
  )
  $results.checks.image_over_boundary = Curl-Status @(
    "-sS", "-b", $cookieHeader, "-o", (Join-Path $qaRoot "image-over.json"),
    "-w", "%{http_code}", "-F", "file=@$overImage;type=image/png",
    "http://127.0.0.1:$NextPort/api/upload-image"
  )
  $results.checks.image_missing_content_length = Curl-Status @(
    "-sS", "-b", $cookieHeader, "-H", "Transfer-Encoding: chunked",
    "-o", (Join-Path $qaRoot "image-no-length.json"), "-w", "%{http_code}",
    "--data-binary", "@$exactImage",
    "http://127.0.0.1:$NextPort/api/upload-image"
  )
  $expected = [ordered]@{
    fastapi_unauthenticated = 401
    login = 200
    document_exact_boundary = 200
    document_over_boundary = 413
    next_unauthenticated = 401
    next_limits = 200
    image_exact_boundary = 200
    image_over_boundary = 413
    image_missing_content_length = 411
  }
  foreach ($name in $expected.Keys) {
    if ([int]$results.checks[$name] -ne [int]$expected[$name]) {
      throw "Expected $name=$($expected[$name]), received $($results.checks[$name])."
    }
  }
  if (
    $results.checks.fastapi_limits.status -ne 200 -or
    $results.checks.fastapi_limits.single_file_mb -ne 100 -or
    $results.checks.fastapi_limits.request_total_mb -ne 512 -or
    $results.checks.fastapi_limits.image_mb -ne 1
  ) {
    throw "FastAPI upload-limit payload did not match the configured values."
  }
  $results.passed = $true
} catch {
  $results.error = $_.Exception.Message
  throw
} finally {
  $results | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $reportPath -Encoding UTF8
  $processSnapshot = @(Get-CimInstance Win32_Process -ErrorAction SilentlyContinue)
  $ownedIds = New-Object "System.Collections.Generic.HashSet[int]"
  foreach ($process in @($nextProcess, $fastApiProcess)) {
    if ($null -ne $process) {
      [void]$ownedIds.Add([int]$process.Id)
    }
  }
  do {
    $added = $false
    foreach ($candidate in $processSnapshot) {
      if (
        $ownedIds.Contains([int]$candidate.ParentProcessId) -and
        $ownedIds.Add([int]$candidate.ProcessId)
      ) {
        $added = $true
      }
    }
  } while ($added)
  foreach ($ownedId in $ownedIds) {
    Stop-Process -Id $ownedId -Force -ErrorAction SilentlyContinue
  }
  foreach ($fixture in @(
    "exact-100MiB.pdf",
    "over-100MiB.pdf",
    "exact-1MiB.png",
    "over-1MiB.png"
  )) {
    $fixturePath = Join-Path $qaRoot $fixture
    if (Test-Path -LiteralPath $fixturePath) {
      Remove-Item -LiteralPath $fixturePath -Force
    }
  }
  $verifiedQaRoot = [IO.Path]::GetFullPath($qaRoot).TrimEnd("\") + "\"
  $verifiedBackendTemp = [IO.Path]::GetFullPath($qaBackendTemp)
  if (
    $verifiedBackendTemp.StartsWith(
      $verifiedQaRoot,
      [StringComparison]::OrdinalIgnoreCase
    ) -and
    (Test-Path -LiteralPath $verifiedBackendTemp)
  ) {
    Remove-Item -LiteralPath $verifiedBackendTemp -Recurse -Force
  }
  Write-Output "QA_REPORT=$reportPath"
}
