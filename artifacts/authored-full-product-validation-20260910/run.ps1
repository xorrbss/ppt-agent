param(
    [string]$BaseUrl = "http://localhost:5000",
    [string]$SourcePath = "C:\project\PPT-agent\ppt-agent\app_data\workspace_assets\d74ac154-738d-4c58-bade-daf2f8d703d3\b7e0ea61-017e-4ebb-be81-545eead471a6\7746aaeddbf1222fd73c9870ac7297af98446b8e5df22d52c809b4d69b5340ae.pptx",
    [string]$OutputDirectory = "C:\project\PPT-agent\ppt-agent\artifacts\authored-full-product-validation-20260910\run-1"
)

$ErrorActionPreference = "Stop"
$output = New-Item -ItemType Directory -Force -Path $OutputDirectory
$runId = [guid]::NewGuid().ToString()
$source = Get-Item -LiteralPath $SourcePath

function Save-Json([string]$Name, $Value) {
    $Value | ConvertTo-Json -Depth 100 | Set-Content -LiteralPath (Join-Path $output $Name) -Encoding utf8
}

$uploadJson = & curl.exe --fail-with-body --silent --show-error `
    -H "Idempotency-Key: exact35-upload-$runId" `
    -F "file=@$($source.FullName);type=application/vnd.openxmlformats-officedocument.presentationml.presentation" `
    "$BaseUrl/api/v1/ppt/assets/documents"
if ($LASTEXITCODE -ne 0) { throw "document upload failed" }
$upload = $uploadJson | ConvertFrom-Json
Save-Json "01-upload.json" $upload
if ($upload.state -ne "ready" -or $upload.sha256 -ne "7746aaeddbf1222fd73c9870ac7297af98446b8e5df22d52c809b4d69b5340ae") {
    throw "uploaded source identity mismatch"
}

$snapshotBody = @{
    provider_id = "uploaded_document"
    source = @{ asset_ids = @([string]$upload.asset_id) }
} | ConvertTo-Json -Depth 10
$snapshot = Invoke-RestMethod -Method Post -Uri "$BaseUrl/api/v1/ppt/references/snapshots" `
    -ContentType "application/json" `
    -Headers @{ "Idempotency-Key" = "exact35-snapshot-$runId" } `
    -Body $snapshotBody
Save-Json "02-snapshot.json" $snapshot

$analysis = Invoke-RestMethod -Method Post -Uri "$BaseUrl/api/v1/ppt/source-content/v1/analyze" `
    -Headers @{ "Idempotency-Key" = "exact35-analysis-$runId" } `
    -Form @{
        contractVersion = "source-content-analysis-request/v1"
        referenceSnapshotId = [string]$snapshot.snapshot_id
    }
Save-Json "03-analysis.json" $analysis
if ($analysis.status -ne "ready" -or $analysis.summary.sourceUnits -ne 35 -or $analysis.summary.textBlocks -ne 289) {
    throw "source analysis count mismatch"
}

$request = @{
    content = "Generate a presentation from the attached files."
    codex_generation_preset = "luna-medium"
    tone = "default"
    verbosity = "standard"
    web_search = $false
    n_slides = $null
    language = "ko"
    template = "authored"
    quality_profile = "release"
    strategy = "authored"
    generation_mode = "auto"
    generation_engine = "authored"
    source_mode = "source_content_restyle"
    source_content_manifest_pin = @{
        manifest_id = [string]$analysis.manifestPin.manifestId
        digest = [string]$analysis.manifestPin.digest
    }
    content_policy = @{
        text = "verbatim"
        preserve_all_table_cells = $false
        preserve_all_chart_values = $false
        preserve_all_image_occurrences = $true
        preserve_source_order = $true
        preserve_source_slide_count = $true
        allow_slide_expansion = $false
        allow_summarization = $false
        overflow_policy = "same_page_recompose_or_fail"
    }
    include_table_of_contents = $false
    include_title_slide = $false
    reference_snapshot_ids = @([string]$snapshot.snapshot_id)
    export_as = "pptx"
    trigger_webhook = $false
    vision_qa = $false
    authored_style = "hyundai-culture-center"
}
Save-Json "04-generate-request.json" $request

$started = Invoke-RestMethod -Method Post -Uri "$BaseUrl/api/v1/ppt/presentation/generate/async" `
    -ContentType "application/json" `
    -Headers @{ "Idempotency-Key" = "exact35-generate-$runId" } `
    -Body ($request | ConvertTo-Json -Depth 20)
Save-Json "05-admission.json" $started
Write-Output ("ADMITTED task={0} operation={1}" -f $started.id, $started.operation_id)

$deadline = [DateTimeOffset]::UtcNow.AddHours(2)
$lastFingerprint = ""
while ([DateTimeOffset]::UtcNow -lt $deadline) {
    $status = Invoke-RestMethod -Method Get -Uri "$BaseUrl/api/v1/ppt/presentation/status/$($started.id)"
    $fingerprint = "{0}|{1}|{2}" -f $status.status, $status.progress, $status.message
    if ($fingerprint -ne $lastFingerprint) {
        Write-Output ("STATUS {0}" -f ($status | ConvertTo-Json -Depth 8 -Compress))
        $lastFingerprint = $fingerprint
    }
    if ($status.status -eq "completed") {
        Save-Json "06-final-status.json" $status
        $result = $status.data
        if (-not $result.presentation_id -or -not $result.path) { throw "completed result missing artifact identity" }
        $serverPath = ([string]$result.path).Replace('\', '/')
        $basename = [IO.Path]::GetFileName($serverPath)
        $download = Join-Path $output $basename
        $reportedUri = [Uri]::new(
            [Uri]($BaseUrl.TrimEnd('/') + '/'),
            $serverPath.TrimStart('/')
        ).AbsoluteUri
        $fallbackUri = "$($BaseUrl.TrimEnd('/'))/app_data/exports/$([uri]::EscapeDataString($basename))"
        $downloadUri = $reportedUri
        try {
            Invoke-WebRequest -Method Get -Uri $downloadUri -OutFile $download
        } catch {
            $downloadUri = $fallbackUri
            Invoke-WebRequest -Method Get -Uri $downloadUri -OutFile $download
        }
        $artifact = Get-Item -LiteralPath $download
        $evidence = @{
            runId = $runId
            taskId = [string]$started.id
            operationId = [string]$started.operation_id
            presentationId = [string]$result.presentation_id
            serverPath = [string]$result.path
            downloadUri = $downloadUri
            downloadedPath = $artifact.FullName
            downloadedBytes = $artifact.Length
            downloadedSha256 = (Get-FileHash -Algorithm SHA256 -LiteralPath $artifact.FullName).Hash.ToLowerInvariant()
        }
        Save-Json "07-download-evidence.json" $evidence
        Write-Output ("COMPLETED {0}" -f ($evidence | ConvertTo-Json -Compress))
        exit 0
    }
    if ($status.status -eq "error") {
        Save-Json "06-final-status.json" $status
        throw "generation failed: $($status.error | ConvertTo-Json -Compress)"
    }
    Start-Sleep -Seconds 5
}
throw "generation timed out after 2 hours"
