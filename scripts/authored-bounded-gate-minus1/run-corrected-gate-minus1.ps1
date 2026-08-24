param(
  [Parameter(Mandatory = $true)][string]$EvidenceRoot
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

function UtcNow { (Get-Date).ToUniversalTime().ToString('yyyy-MM-ddTHH:mm:ss.fffZ') }
function ShaText([string]$Text) {
  $bytes=[Text.Encoding]::UTF8.GetBytes($Text)
  $hash=[Security.Cryptography.SHA256]::Create().ComputeHash($bytes)
  'sha256:' + (($hash | ForEach-Object { $_.ToString('x2') }) -join '')
}
function ShaFile([string]$Path) { 'sha256:' + (Get-FileHash -Algorithm SHA256 -LiteralPath $Path).Hash.ToLowerInvariant() }
function Write-NewJson([string]$Path,$Value,[int]$Depth=20) {
  if(Test-Path -LiteralPath $Path){throw "refusing-to-overwrite:$Path"}
  New-Item -ItemType Directory -Force -Path (Split-Path -Parent $Path)|Out-Null
  $Value|ConvertTo-Json -Depth $Depth|Set-Content -LiteralPath $Path -Encoding utf8
}

$scriptDir=Split-Path -Parent $PSCommandPath
$repo=(Resolve-Path (Join-Path $scriptDir '..\..')).Path
$contracts=Join-Path $repo 'docs\authored-bounded-hybrid-release-v3-contracts'
$attemptId=('authored-gate-minus1-corrected-'+(Get-Date).ToUniversalTime().ToString('yyyyMMddTHHmmssZ')+'-'+[guid]::NewGuid().ToString('N').Substring(0,8))
$attemptDir=Join-Path $EvidenceRoot $attemptId
if(Test-Path -LiteralPath $attemptDir){throw "fresh-attempt-path-exists:$attemptDir"}
New-Item -ItemType Directory -Path $attemptDir|Out-Null
$timeline=[Collections.Generic.List[object]]::new();$timeline.Add([ordered]@{event='attempt-created';at=(UtcNow);attemptId=$attemptId})
$authority=Join-Path $scriptDir 'gate-minus1-authority.mjs'

Push-Location $repo
try {
  $root=(git rev-parse --show-toplevel).Trim()
  $censusPath=Join-Path $attemptDir 'authority\source-tree-census.json'
  $censusOutput=& node $authority census --repo-root $root --output $censusPath
  if($LASTEXITCODE -ne 0){throw "source-tree-census-opening-failed:$LASTEXITCODE"}
  $censusIdentity=$censusOutput|Select-Object -Last 1|ConvertFrom-Json
  $timeline.Add([ordered]@{event='source-tree-census-opening';at=(UtcNow);censusDigest=$censusIdentity.censusDigest;head=$censusIdentity.head})

  $powerPointExe='C:\Program Files\Microsoft Office\Root\Office16\POWERPNT.EXE';if(-not(Test-Path $powerPointExe)){throw 'powerpoint-16-not-installed'}
  $fontInventoryPath=Join-Path $attemptDir 'authority\font-inventory.json';$python=Join-Path $repo 'servers\fastapi\.venv\Scripts\python.exe';if(-not(Test-Path $python)){throw 'repo-fonttools-python-missing'}
  &$python (Join-Path $scriptDir 'inspect-font-inventory.py') --font-root (Join-Path $env:WINDIR 'Fonts') --output $fontInventoryPath
  if($LASTEXITCODE -ne 0){throw "font-inventory-inspection-failed:$LASTEXITCODE"};$fontInventoryCore=Get-Content -Raw $fontInventoryPath|ConvertFrom-Json;$fontInventoryDigest=$fontInventoryCore.fontInventoryDigest
  $version=(Get-Item $powerPointExe).VersionInfo
  $envCore=[ordered]@{schemaVersion='gate-minus1-environment-receipt/v1';trustDomain='gate_minus1_actual_probe';capturedAt=(UtcNow);computerName=[Environment]::MachineName;userName=[Environment]::UserName;userInteractive=[Environment]::UserInteractive;sessionId=[Diagnostics.Process]::GetCurrentProcess().SessionId;osCaption=(Get-CimInstance Win32_OperatingSystem).Caption;osVersion=[Environment]::OSVersion.Version.ToString();osArchitecture=(Get-CimInstance Win32_OperatingSystem).OSArchitecture;powerPointExecutable=$powerPointExe;powerPointFileVersion=$version.FileVersion;powerPointProductVersion=$version.ProductVersion;officeVersionBuildBitness=('16.0/'+$version.FileVersion+'/'+(Get-CimInstance Win32_OperatingSystem).OSArchitecture);fontInventoryDigest=$fontInventoryDigest;fontInventoryArtifactSha256=(ShaFile $fontInventoryPath);fontInventoryCollectorSha256=(ShaFile (Join-Path $scriptDir 'inspect-font-inventory.py'));fontInventoryFaces=@($fontInventoryCore.faces)}
  $envCore.environmentReceiptDigest=ShaText ($envCore|ConvertTo-Json -Depth 12 -Compress);$environmentPath=Join-Path $attemptDir 'authority\environment-receipt.json';Write-NewJson $environmentPath $envCore 15

  $policy=Get-Content -Raw (Join-Path $contracts 'evidence-policy.v2.json')|ConvertFrom-Json
  $carrier=[ordered]@{schemaVersion='gate-minus1-carrier-values/v1';attemptId=$attemptId;planDigest=(ShaText $attemptId);exportAttemptId=[guid]::NewGuid().ToString();policyDigest=$policy.policyDigest}
  $carrierPath=Join-Path $attemptDir 'authority\carrier-values.json';Write-NewJson $carrierPath $carrier
  $inputPptx=Join-Path $attemptDir 'input\gate-minus1-input.pptx'
  $probeHost=(Get-Command pwsh.exe -ErrorAction Stop).Source
  & $probeHost -NoProfile -ExecutionPolicy Bypass -File (Join-Path $scriptDir 'probe-com.ps1') -Mode create -OutputDir $attemptDir -CarrierValuesPath $carrierPath -InputPptx $inputPptx
  if($LASTEXITCODE -ne 0){throw "fresh-input-create-failed:$LASTEXITCODE"}
  $inputCleanupDeadline=(Get-Date).AddSeconds(10);while(@(Get-Process POWERPNT -ErrorAction SilentlyContinue).Count -ne 0 -and (Get-Date) -lt $inputCleanupDeadline){Start-Sleep -Milliseconds 200}
  if(@(Get-Process POWERPNT -ErrorAction SilentlyContinue).Count -ne 0){throw 'powerpoint-survivor-after-input-create'}
  $timeline.Add([ordered]@{event='fresh-input-created';at=(UtcNow);sha256=(ShaFile $inputPptx)})

  $watcher=Join-Path $scriptDir 'dialog-watcher.ps1';$censusScript=Join-Path $scriptDir 'process-census.ps1';$probe=Join-Path $scriptDir 'probe-com.ps1'
  $prepareOutput=& node $authority prepare --reference-manifest (Join-Path $contracts 'gate-minus1-case-manifest.v1.json') --policy (Join-Path $contracts 'evidence-policy.v2.json') --input-pptx $inputPptx --environment-receipt $environmentPath --source-tree-census $censusPath --probe-script $probe --watcher-script $watcher --census-script $censusScript --carrier-values $carrierPath --output-dir $attemptDir
  if($LASTEXITCODE -ne 0){throw "execution-seal-prepare-failed:$LASTEXITCODE"}
  $identity=$prepareOutput|Select-Object -Last 1|ConvertFrom-Json;$manifest=Get-Content -Raw $identity.manifestPath|ConvertFrom-Json
  if($manifest.manifestStatus -ne 'sealed-for-execution' -or -not $manifest.executionPins.sha256){throw 'manifest-not-sealed-before-probe'}
  $timeline.Add([ordered]@{event='manifest-and-execution-seal-finalized';at=(UtcNow);manifestDigest=$manifest.manifestDigest;executionSealDigest=(Get-Content -Raw $identity.executionSealPath|ConvertFrom-Json).executionSealDigest})
  Write-NewJson (Join-Path $attemptDir 'authority\pre-execution-timeline.json') @($timeline) 10

  & $probeHost -NoProfile -ExecutionPolicy Bypass -File $probe -Mode execute -OutputDir $attemptDir -CarrierValuesPath $carrierPath -InputPptx $inputPptx -ManifestPath $identity.manifestPath -EnvironmentReceiptPath $environmentPath -WatcherScriptPath $watcher -CensusScriptPath $censusScript
  if($LASTEXITCODE -ne 0){throw "gate-minus1-com-probe-failed:$LASTEXITCODE"}
  $raw=Join-Path $attemptDir 'raw\gate-minus1-raw-report.json';if(-not(Test-Path $raw)){throw 'raw-probe-report-missing'}
  try {
    $qualifyOutput=& node $authority qualify --manifest $identity.manifestPath --validator-contract (Join-Path $contracts 'gate-minus1-semantic-validator-contract.v1.json') --raw-report $raw --output-dir $attemptDir
    $qualifyExit=$LASTEXITCODE
  } finally {
    if(Test-Path (Join-Path $attemptDir 'authority\.ephemeral-private-key.pem')){Remove-Item -LiteralPath (Join-Path $attemptDir 'authority\.ephemeral-private-key.pem') -Force}
  }
  $summary=$qualifyOutput|Select-Object -Last 1|ConvertFrom-Json
  $finalCensus=Join-Path $attemptDir 'authority\powerpoint-final-census.json';&$censusScript -OutputPath $finalCensus|Out-Null
  $final=(Get-Content -Raw $finalCensus|ConvertFrom-Json);if($final.processCount -ne 0){throw 'powerpoint-final-census-not-zero'}
  $timeline.Add([ordered]@{event='qualification-complete';at=(UtcNow);suiteStatus=$summary.suiteStatus;resultSetDigest=$summary.resultSetDigest;powerPointFinalProcessCount=$final.processCount})
  Write-NewJson (Join-Path $attemptDir 'completion-timeline.json') @($timeline) 12
  [ordered]@{attemptDir=$attemptDir;suiteStatus=$summary.suiteStatus;executedCaseCount=$summary.executedCaseCount;skippedCaseCount=$summary.skippedCaseCount;manifestDigest=$summary.manifestDigest;resultSetDigest=$summary.resultSetDigest;inputPptxDigest=(ShaFile $inputPptx);savedPptxDigest=(ShaFile (Join-Path $attemptDir 'saved\gate-minus1-saved.pptx'));powerPointFinalProcessCount=$final.processCount}|ConvertTo-Json -Depth 8
  if($qualifyExit -ne 0 -or $summary.suiteStatus -ne 'feasible'){exit 2}
} finally { Pop-Location }
