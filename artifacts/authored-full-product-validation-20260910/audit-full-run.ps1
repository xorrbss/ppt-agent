[CmdletBinding()]
param(
    [Parameter(Mandatory)]
    [ValidateNotNullOrEmpty()]
    [string]$RunDirectory,

    [string]$OperationId,

    [ValidateRange(1, 10000)]
    [int]$ExpectedSlides = 35,

    [ValidateRange(0, 100000)]
    [int]$ExpectedSourceBlocks = 289,

    [ValidatePattern('^[a-fA-F0-9]{64}$')]
    [string]$ExpectedInputSha256 = '7746aaeddbf1222fd73c9870ac7297af98446b8e5df22d52c809b4d69b5340ae',

    # This permits archived failed-run rejection while the local database is
    # intentionally offline. It can never accept a release.
    [switch]$Offline
)

# This script intentionally performs only reads.  It emits a redacted JSON
# verdict to stdout and exits 0 only when every release-evidence check passes.
# Exit 2 means the run completed its audit but did not meet the release gate.
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

function Add-AuditCheck {
    param(
        [System.Collections.Generic.List[object]]$Checks,
        [string]$Name,
        [bool]$Passed,
        $Actual,
        $Expected
    )

    $Checks.Add([pscustomobject]@{
        name = $Name
        passed = $Passed
        actual = $Actual
        expected = $Expected
    })
}

function Get-JsonProperty {
    param($Object, [string]$Name)
    if ($null -eq $Object) { return $null }
    $property = $Object.PSObject.Properties[$Name]
    if ($null -eq $property) { return $null }
    return $property.Value
}

function Get-Sha256Text {
    param([Parameter(Mandatory)][string]$Text)
    $sha = [System.Security.Cryptography.SHA256]::Create()
    try {
        $bytes = [System.Text.Encoding]::UTF8.GetBytes($Text)
        return ([System.BitConverter]::ToString($sha.ComputeHash($bytes))).Replace('-', '').ToLowerInvariant()
    }
    finally {
        $sha.Dispose()
    }
}

try {
    $runPath = (Resolve-Path -LiteralPath $RunDirectory).Path
    $admissionPath = Join-Path $runPath '05-admission.json'
    if ([string]::IsNullOrWhiteSpace($OperationId)) {
        if (-not (Test-Path -LiteralPath $admissionPath -PathType Leaf)) {
            throw "OperationId was not supplied and '$admissionPath' does not exist."
        }
        $admission = Get-Content -LiteralPath $admissionPath -Raw -Encoding UTF8 | ConvertFrom-Json
        $OperationId = [string](Get-JsonProperty $admission 'operation_id')
    }

    $parsedOperationId = [Guid]::Empty
    if (-not [Guid]::TryParse($OperationId, [ref]$parsedOperationId)) {
        throw 'OperationId must be a UUID.'
    }
    $OperationId = $parsedOperationId.ToString()

    if ($Offline) {
        $checks = [System.Collections.Generic.List[object]]::new()
        $finalPath = Join-Path $runPath '06-final-status.json'
        $final = if (Test-Path -LiteralPath $finalPath -PathType Leaf) { Get-Content -LiteralPath $finalPath -Raw -Encoding UTF8 | ConvertFrom-Json } else { $null }
        $finalStatus = Get-JsonProperty $final 'status'
        $failure = Get-JsonProperty $final 'failure'
        $analysisPath = Join-Path $runPath '03-analysis.json'
        $analysis = if (Test-Path -LiteralPath $analysisPath -PathType Leaf) { Get-Content -LiteralPath $analysisPath -Raw -Encoding UTF8 | ConvertFrom-Json } else { $null }
        $analysisSummary = Get-JsonProperty $analysis 'summary'
        $downloadPath = Join-Path $runPath '07-download-evidence.json'

        Add-AuditCheck $checks 'archive.finalStatusSucceeded' ($finalStatus -eq 'completed') $finalStatus 'completed'
        Add-AuditCheck $checks 'archive.failureCodeAbsent' ([string]::IsNullOrWhiteSpace([string](Get-JsonProperty $failure 'code'))) (Get-JsonProperty $failure 'code') 'null'
        Add-AuditCheck $checks 'archive.downloadEvidencePresent' (Test-Path -LiteralPath $downloadPath -PathType Leaf) (Test-Path -LiteralPath $downloadPath -PathType Leaf) $true
        Add-AuditCheck $checks 'input.slideCount' (([int](Get-JsonProperty $analysisSummary 'sourceUnits')) -eq $ExpectedSlides) (Get-JsonProperty $analysisSummary 'sourceUnits') $ExpectedSlides
        Add-AuditCheck $checks 'input.sourceBlockCount' (([int](Get-JsonProperty $analysisSummary 'textBlocks')) -eq $ExpectedSourceBlocks) (Get-JsonProperty $analysisSummary 'textBlocks') $ExpectedSourceBlocks

        [pscustomobject]@{
            schemaVersion = 'authored-full-run-audit/v1'
            mode = 'offline-rejection-only'
            verdict = 'failed'
            operation = [pscustomobject]@{ id = $OperationId }
            archivedFinalStatus = [pscustomobject]@{
                status = $finalStatus
                failureCode = Get-JsonProperty $failure 'code'
            }
            flaggedSlides = 'unavailable'
            checks = $checks
        } | ConvertTo-Json -Depth 6
        exit 2
    }

    $repoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
    $libraryPath = Join-Path $repoRoot 'scripts\local-stack-lib.ps1'
    $environmentPath = Join-Path $repoRoot 'app_data\public-v2-local\local-stack-env.dpapi.xml'
    if (-not (Test-Path -LiteralPath $libraryPath -PathType Leaf)) { throw "Missing local-stack helper: $libraryPath" }
    if (-not (Test-Path -LiteralPath $environmentPath -PathType Leaf)) { throw "Missing local-stack environment file: $environmentPath" }

    . $libraryPath
    $stackEnvironment = Read-StackEnvironment $environmentPath
    $databaseUrl = [string]$stackEnvironment['api']['DATABASE_URL']
    if ([string]::IsNullOrWhiteSpace($databaseUrl)) { throw 'The local API DATABASE_URL is unavailable.' }
    $databaseProvider = if ($databaseUrl -match '^postgresql\+asyncpg://') { 'postgresql+asyncpg' } else { 'not-postgresql+asyncpg' }
    $databaseUrl = $databaseUrl -replace '^postgresql\+asyncpg://', 'postgresql://'
    $workerEnvironment = $stackEnvironment['worker-generation']
    $effectiveBatchWaveConcurrency = [int]$workerEnvironment['AUTHORED_BATCH_WAVE_CONCURRENCY']
    $effectiveClusterJobConcurrency = if ($null -ne $workerEnvironment['AUTHORED_CLUSTER_JOB_CONCURRENCY']) { [int]$workerEnvironment['AUTHORED_CLUSTER_JOB_CONCURRENCY'] } else { [int]$stackEnvironment['api']['AUTHORED_JOB_CONCURRENCY'] }
    $effectiveRenderConcurrency = if ($null -ne $workerEnvironment['AUTHORED_RENDER_CONCURRENCY']) { [int]$workerEnvironment['AUTHORED_RENDER_CONCURRENCY'] } else { [int]$workerEnvironment['PUBLIC_RENDER_MAX_CONCURRENCY'] }
    $effectiveSlideRenderConcurrency = if ($null -ne $workerEnvironment['AUTHORED_SLIDE_RENDER_CONCURRENCY']) { [int]$workerEnvironment['AUTHORED_SLIDE_RENDER_CONCURRENCY'] } else { [int]$workerEnvironment['PUBLIC_RENDER_MAX_CONCURRENCY'] }

    $psql = Get-Command 'psql.exe' -ErrorAction SilentlyContinue
    if ($null -eq $psql) {
        $knownPsql = 'C:\Program Files\PostgreSQL\15\bin\psql.exe'
        if (Test-Path -LiteralPath $knownPsql -PathType Leaf) { $psql = Get-Item -LiteralPath $knownPsql }
    }
    if ($null -eq $psql) { throw 'psql.exe was not found.' }

    # The UUID has been parsed above, so embedding it as a SQL literal is safe.
    # Select only release evidence; never print the database URL, request body,
    # storage key, or any provider credential.
    $sql = @"
SELECT json_build_object(
  'jobId', j.id::text,
  'jobState', j.state,
  'jobAttempt', j.attempt_number,
  'resumeCount', j.resume_count,
  'totalSlides', j.total_slides,
  'completedSlides', j.completed_slides,
  'publishedAt', j.published_at,
  'operationId', o.id::text,
  'operationState', o.state,
  'operationAttempt', o.attempt_number,
  'retryCount', o.retry_count,
  'maxAttempts', o.max_attempts,
  'operationPhase', o.phase,
  'resultRevision', o.result_revision,
  'errorCode', o.error_code,
  'artifactId', o.result->>'artifact_id',
  'artifactSha256', lower(o.result->>'artifact_sha256'),
  'qualityGate', p.theme #> '{authoredQualityGate}'
)::text
FROM workspace_operations o
LEFT JOIN presentation_generation_jobs j ON j.workspace_operation_id = o.id
LEFT JOIN presentations p ON p.id = j.presentation_id
WHERE o.id = '$OperationId';
"@
    $databaseRow = & $psql.Source "--dbname=$databaseUrl" '-X' '-q' '-t' '-A' '-c' $sql
    if ($LASTEXITCODE -ne 0) { throw 'The release-evidence database query failed.' }
    $databaseRow = @($databaseRow | Where-Object { -not [string]::IsNullOrWhiteSpace([string]$_) })
    if ($databaseRow.Count -ne 1) { throw "Expected one operation row, found $($databaseRow.Count)." }
    $record = $databaseRow[0] | ConvertFrom-Json

    $checks = [System.Collections.Generic.List[object]]::new()
    Add-AuditCheck $checks 'runtime.postgresql' ($databaseProvider -eq 'postgresql+asyncpg') $databaseProvider 'postgresql+asyncpg'
    Add-AuditCheck $checks 'runtime.batchWaveConcurrency' ($effectiveBatchWaveConcurrency -eq 5) $effectiveBatchWaveConcurrency 5
    Add-AuditCheck $checks 'runtime.clusterJobConcurrency' ($effectiveClusterJobConcurrency -eq 2) $effectiveClusterJobConcurrency 2
    Add-AuditCheck $checks 'runtime.renderConcurrency' ($effectiveRenderConcurrency -eq 2) $effectiveRenderConcurrency 2
    Add-AuditCheck $checks 'runtime.slideRenderConcurrency' ($effectiveSlideRenderConcurrency -eq 2) $effectiveSlideRenderConcurrency 2
    Add-AuditCheck $checks 'operation.succeeded' ((Get-JsonProperty $record 'operationState') -eq 'succeeded') (Get-JsonProperty $record 'operationState') 'succeeded'
    # Successful generation-job operations in the live schema use phase=succeeded.
    Add-AuditCheck $checks 'operation.phase' ((Get-JsonProperty $record 'operationPhase') -eq 'succeeded') (Get-JsonProperty $record 'operationPhase') 'succeeded'
    Add-AuditCheck $checks 'job.published' ((Get-JsonProperty $record 'jobState') -eq 'published') (Get-JsonProperty $record 'jobState') 'published'
    Add-AuditCheck $checks 'job.publishedAt' ($null -ne (Get-JsonProperty $record 'publishedAt')) (Get-JsonProperty $record 'publishedAt') 'non-null'
    Add-AuditCheck $checks 'job.slideCount' (([int](Get-JsonProperty $record 'totalSlides')) -eq $ExpectedSlides -and ([int](Get-JsonProperty $record 'completedSlides')) -eq $ExpectedSlides) ([pscustomobject]@{ total = (Get-JsonProperty $record 'totalSlides'); completed = (Get-JsonProperty $record 'completedSlides') }) "$ExpectedSlides/$ExpectedSlides"
    Add-AuditCheck $checks 'job.attempt' (([int](Get-JsonProperty $record 'jobAttempt')) -eq 1) (Get-JsonProperty $record 'jobAttempt') 1
    Add-AuditCheck $checks 'job.resumeCount' (([int](Get-JsonProperty $record 'resumeCount')) -eq 0) (Get-JsonProperty $record 'resumeCount') 0
    Add-AuditCheck $checks 'operation.attempt' (([int](Get-JsonProperty $record 'operationAttempt')) -eq 1) (Get-JsonProperty $record 'operationAttempt') 1
    Add-AuditCheck $checks 'operation.retryCount' (([int](Get-JsonProperty $record 'retryCount')) -eq 0) (Get-JsonProperty $record 'retryCount') 0
    Add-AuditCheck $checks 'operation.resultRevision' (([int](Get-JsonProperty $record 'resultRevision')) -ge 1) (Get-JsonProperty $record 'resultRevision') '>= 1'
    Add-AuditCheck $checks 'operation.artifactId' (-not [string]::IsNullOrWhiteSpace([string](Get-JsonProperty $record 'artifactId'))) ([bool](-not [string]::IsNullOrWhiteSpace([string](Get-JsonProperty $record 'artifactId')))) $true

    $uploadPath = Join-Path $runPath '01-upload.json'
    $upload = if (Test-Path -LiteralPath $uploadPath -PathType Leaf) { Get-Content -LiteralPath $uploadPath -Raw -Encoding UTF8 | ConvertFrom-Json } else { $null }
    $snapshotPath = Join-Path $runPath '02-snapshot.json'
    $snapshot = if (Test-Path -LiteralPath $snapshotPath -PathType Leaf) { Get-Content -LiteralPath $snapshotPath -Raw -Encoding UTF8 | ConvertFrom-Json } else { $null }
    $analysisPath = Join-Path $runPath '03-analysis.json'
    $analysis = if (Test-Path -LiteralPath $analysisPath -PathType Leaf) { Get-Content -LiteralPath $analysisPath -Raw -Encoding UTF8 | ConvertFrom-Json } else { $null }
    $requestPath = Join-Path $runPath '04-generate-request.json'
    $request = if (Test-Path -LiteralPath $requestPath -PathType Leaf) { Get-Content -LiteralPath $requestPath -Raw -Encoding UTF8 | ConvertFrom-Json } else { $null }
    $analysisSummary = Get-JsonProperty $analysis 'summary'
    $analysisPin = Get-JsonProperty $analysis 'manifestPin'
    $requestPin = Get-JsonProperty $request 'source_content_manifest_pin'
    Add-AuditCheck $checks 'input.slideCount' (([int](Get-JsonProperty $analysisSummary 'sourceUnits')) -eq $ExpectedSlides) (Get-JsonProperty $analysisSummary 'sourceUnits') $ExpectedSlides
    Add-AuditCheck $checks 'input.sourceBlockCount' (([int](Get-JsonProperty $analysisSummary 'textBlocks')) -eq $ExpectedSourceBlocks) (Get-JsonProperty $analysisSummary 'textBlocks') $ExpectedSourceBlocks
    Add-AuditCheck $checks 'input.uploadSha256' (([string](Get-JsonProperty $upload 'sha256')).ToLowerInvariant() -eq $ExpectedInputSha256.ToLowerInvariant()) (Get-JsonProperty $upload 'sha256') $ExpectedInputSha256.ToLowerInvariant()
    Add-AuditCheck $checks 'input.snapshotAssetBinding' (@(Get-JsonProperty $snapshot 'asset_ids') -contains (Get-JsonProperty $upload 'asset_id')) ([bool](@(Get-JsonProperty $snapshot 'asset_ids') -contains (Get-JsonProperty $upload 'asset_id'))) $true
    Add-AuditCheck $checks 'input.manifestPin' ((Get-JsonProperty $requestPin 'digest') -eq (Get-JsonProperty $analysisPin 'digest') -and (Get-JsonProperty $requestPin 'manifest_id') -eq (Get-JsonProperty $analysisPin 'manifestId')) ([pscustomobject]@{ requestDigest = (Get-JsonProperty $requestPin 'digest'); analysisDigest = (Get-JsonProperty $analysisPin 'digest') }) 'matching request/analysis pin'

    $qualityGate = Get-JsonProperty $record 'qualityGate'
    $sourceReadback = Get-JsonProperty $qualityGate 'sourceReadback'
    $flaggedSlidesProperty = if ($null -ne $qualityGate) { $qualityGate.PSObject.Properties['flaggedSlides'] } else { $null }
    $flaggedSlides = if ($null -ne $flaggedSlidesProperty) { @($flaggedSlidesProperty.Value) } else { 'unavailable' }
    $missingBlocks = @(Get-JsonProperty $sourceReadback 'missingBlockIds')
    $visibilityFailures = @(Get-JsonProperty $sourceReadback 'visibilityFailures')
    Add-AuditCheck $checks 'qualityGate.present' ($null -ne $qualityGate) ([bool]($null -ne $qualityGate)) $true
    Add-AuditCheck $checks 'qualityGate.documentCompleted' ((Get-JsonProperty $qualityGate 'documentCompleted') -eq $true) (Get-JsonProperty $qualityGate 'documentCompleted') $true
    Add-AuditCheck $checks 'sourceReadback.present' ($null -ne $sourceReadback) ([bool]($null -ne $sourceReadback)) $true
    Add-AuditCheck $checks 'sourceReadback.checkedSlides' (([int](Get-JsonProperty $sourceReadback 'checkedSlideCount')) -eq $ExpectedSlides) (Get-JsonProperty $sourceReadback 'checkedSlideCount') $ExpectedSlides
    Add-AuditCheck $checks 'sourceReadback.checkedBlocks' (([int](Get-JsonProperty $sourceReadback 'checkedBlockCount')) -eq $ExpectedSourceBlocks) (Get-JsonProperty $sourceReadback 'checkedBlockCount') $ExpectedSourceBlocks
    Add-AuditCheck $checks 'sourceReadback.passedAndVerified' ((Get-JsonProperty $sourceReadback 'passed') -eq $true -and (Get-JsonProperty $sourceReadback 'verified') -eq $true) ([pscustomobject]@{ passed = (Get-JsonProperty $sourceReadback 'passed'); verified = (Get-JsonProperty $sourceReadback 'verified') }) 'true/true'
    Add-AuditCheck $checks 'sourceReadback.noResiduals' (($missingBlocks.Count -eq 0) -and (([int](Get-JsonProperty $sourceReadback 'orderViolations')) -eq 0) -and ($visibilityFailures.Count -eq 0)) ([pscustomobject]@{ missingBlockIds = $missingBlocks.Count; orderViolations = (Get-JsonProperty $sourceReadback 'orderViolations'); visibilityFailures = $visibilityFailures.Count }) '0/0/0'

    $downloadPath = Join-Path $runPath '07-download-evidence.json'
    $download = if (Test-Path -LiteralPath $downloadPath -PathType Leaf) { Get-Content -LiteralPath $downloadPath -Raw -Encoding UTF8 | ConvertFrom-Json } else { $null }
    $pptxPath = [string](Get-JsonProperty $download 'downloadedPath')
    $pptxExists = -not [string]::IsNullOrWhiteSpace($pptxPath) -and (Test-Path -LiteralPath $pptxPath -PathType Leaf)
    Add-AuditCheck $checks 'pptx.downloaded' $pptxExists $pptxExists $true

    $pptxSha256 = $null
    $pptxSlideCount = $null
    $zipReadable = $false
    if ($pptxExists) {
        $pptxSha256 = (Get-FileHash -LiteralPath $pptxPath -Algorithm SHA256).Hash.ToLowerInvariant()
        $expectedDownloadSha = [string](Get-JsonProperty $download 'downloadedSha256')
        $expectedArtifactSha = [string](Get-JsonProperty $record 'artifactSha256')
        Add-AuditCheck $checks 'pptx.downloadHash' ($pptxSha256 -eq $expectedDownloadSha) $pptxSha256 $expectedDownloadSha
        Add-AuditCheck $checks 'pptx.artifactHash' ($pptxSha256 -eq $expectedArtifactSha) $pptxSha256 $expectedArtifactSha
        try {
            Add-Type -AssemblyName System.IO.Compression.FileSystem
            $archive = [System.IO.Compression.ZipFile]::OpenRead($pptxPath)
            try {
                $pptxSlideCount = @($archive.Entries | Where-Object { $_.FullName -match '^ppt/slides/slide[0-9]+\.xml$' }).Count
                $zipReadable = $true
            }
            finally {
                $archive.Dispose()
            }
        }
        catch {
            $zipReadable = $false
        }
    }
    Add-AuditCheck $checks 'pptx.zipReadable' $zipReadable $zipReadable $true
    Add-AuditCheck $checks 'pptx.slideXmlCount' ($pptxSlideCount -eq $ExpectedSlides) $pptxSlideCount $ExpectedSlides

    $appDataDirectory = [string]$stackEnvironment['api']['APP_DATA_DIRECTORY']
    $receiptDirectory = if (-not [string]::IsNullOrWhiteSpace([string]$stackEnvironment['api']['AUTHORED_QUALITY_RECEIPT_DIRECTORY'])) {
        [string]$stackEnvironment['api']['AUTHORED_QUALITY_RECEIPT_DIRECTORY']
    } else {
        Join-Path $appDataDirectory 'authored-quality-receipts'
    }
    $jobHash = Get-Sha256Text ([string](Get-JsonProperty $record 'jobId'))
    $receiptBase = Join-Path $receiptDirectory "job-$($jobHash.Substring(0, 32))"

    $allPassed = @($checks | Where-Object { -not $_.passed }).Count -eq 0
    [pscustomobject]@{
        schemaVersion = 'authored-full-run-audit/v1'
        verdict = if ($allPassed) { 'passed' } else { 'failed' }
        operation = [pscustomobject]@{
            id = Get-JsonProperty $record 'operationId'
            state = Get-JsonProperty $record 'operationState'
            phase = Get-JsonProperty $record 'operationPhase'
            attempt = Get-JsonProperty $record 'operationAttempt'
            retryCount = Get-JsonProperty $record 'retryCount'
            maxAttempts = Get-JsonProperty $record 'maxAttempts'
            resultRevision = Get-JsonProperty $record 'resultRevision'
            errorCode = Get-JsonProperty $record 'errorCode'
        }
        runtime = [pscustomobject]@{
            databaseProvider = $databaseProvider
            batchWaveConcurrency = $effectiveBatchWaveConcurrency
            clusterJobConcurrency = $effectiveClusterJobConcurrency
            renderConcurrency = $effectiveRenderConcurrency
            slideRenderConcurrency = $effectiveSlideRenderConcurrency
        }
        job = [pscustomobject]@{
            id = Get-JsonProperty $record 'jobId'
            state = Get-JsonProperty $record 'jobState'
            attempt = Get-JsonProperty $record 'jobAttempt'
            resumeCount = Get-JsonProperty $record 'resumeCount'
            slides = "$(Get-JsonProperty $record 'completedSlides')/$(Get-JsonProperty $record 'totalSlides')"
            publishedAt = Get-JsonProperty $record 'publishedAt'
        }
        receipts = [pscustomobject]@{
            qualityGateStoredOnPresentation = ($null -ne $qualityGate)
            qualityReceiptV2FilePresent = (Test-Path -LiteralPath "$receiptBase.json" -PathType Leaf)
            qualityReceiptV3FilePresent = (Test-Path -LiteralPath "$receiptBase-v3.json" -PathType Leaf)
            timingReceiptFilePresent = (Test-Path -LiteralPath "$receiptBase-attempt-timing.json" -PathType Leaf)
        }
        flaggedSlides = $flaggedSlides
        pptx = [pscustomobject]@{
            downloaded = $pptxExists
            sha256 = $pptxSha256
            slideXmlCount = $pptxSlideCount
        }
        checks = $checks
    } | ConvertTo-Json -Depth 8

    if ($allPassed) { exit 0 }
    exit 2
}
catch {
    [pscustomobject]@{
        schemaVersion = 'authored-full-run-audit/v1'
        verdict = 'audit-error'
        error = $_.Exception.Message
    } | ConvertTo-Json -Depth 4
    exit 1
}
