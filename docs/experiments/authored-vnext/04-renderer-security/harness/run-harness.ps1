param(
  [string]$OutputDirectory = "",
  [string]$ElectronObservation = "",
  [string]$SourceRevision = ""
)

$ErrorActionPreference = "Stop"
$HarnessRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$ExperimentRoot = Split-Path -Parent $HarnessRoot
$RepositoryRoot = (Resolve-Path (Join-Path $ExperimentRoot "..\..\..\..")).Path
if (-not $OutputDirectory) {
  $OutputDirectory = Join-Path $ExperimentRoot "results\hardened-harness"
}
$OutputDirectory = [System.IO.Path]::GetFullPath($OutputDirectory)
New-Item -ItemType Directory -Path $OutputDirectory -Force | Out-Null

if (-not $SourceRevision) {
  $SourceRevision = (git -C $RepositoryRoot rev-parse HEAD).Trim()
  if ($LASTEXITCODE -ne 0) { throw "Could not resolve source revision" }
}
if ($SourceRevision -notmatch '^[0-9a-f]{40}$') {
  throw "SourceRevision must be a full lowercase Git commit"
}
if ($ElectronObservation) {
  $ElectronObservation = (Resolve-Path -LiteralPath $ElectronObservation).Path
}

if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
  Write-Error "Docker is required for the isolated harness; no host fallback is allowed."
  exit 2
}

$ImageName = "authored-renderer-security-harness:local"
$ContainerName = "authored-renderer-security-$PID"
$ProductContainerName = "authored-renderer-contract-$PID"
$ProductImageTag = "presenton-authored-render-worker:local"
$ProductComposeFile = Join-Path $RepositoryRoot "contracts\authored-renderer\compose.authored-renderer.json"
$Dockerfile = Join-Path $HarnessRoot "Dockerfile"
$Attestation = python (Join-Path $RepositoryRoot "contracts\authored-renderer\attestation.py") | ConvertFrom-Json
if ($LASTEXITCODE -ne 0) { throw "Could not compute renderer attestation" }
$SourceTreeDigest = $Attestation.AUTHORED_RENDERER_SOURCE_TREE_DIGEST
$ContractDigest = $Attestation.AUTHORED_RENDERER_CONTRACT_DIGEST

try {
  $env:AUTHORED_RENDERER_SOURCE_REVISION = $SourceRevision
  $env:AUTHORED_RENDERER_SOURCE_TREE_DIGEST = $SourceTreeDigest
  $env:AUTHORED_RENDERER_CONTRACT_DIGEST = $ContractDigest
  $env:AUTHORED_RENDERER_IMAGE_REF = $ProductImageTag
  docker compose --ansi never -f $ProductComposeFile build authored-render-worker
  if ($LASTEXITCODE -ne 0) { throw "Product renderer image build failed" }
  $ProductImageId = (docker image inspect $ProductImageTag --format '{{.Id}}').Trim()
  if ($LASTEXITCODE -ne 0 -or $ProductImageId -notmatch '^sha256:[0-9a-f]{64}$') {
    throw "Product renderer image ID is invalid"
  }
  $env:AUTHORED_RENDERER_IMAGE_REF = $ProductImageId
  docker compose --ansi never -f $ProductComposeFile run -d --no-deps -T `
    --name $ProductContainerName authored-render-worker | Out-Null
  if ($LASTEXITCODE -ne 0) { throw "Product renderer inspect container failed" }

  docker build --tag $ImageName --build-arg "SOURCE_REVISION=$SourceRevision" --file $Dockerfile $ExperimentRoot
  if ($LASTEXITCODE -ne 0) { throw "Docker build failed" }

  docker create --name $ContainerName `
    --init `
    --network none `
    --read-only `
    --tmpfs /tmp:rw,noexec,nosuid,nodev,size=96m `
    --tmpfs /dev/shm:rw,noexec,nosuid,nodev,size=64m `
    --cap-drop ALL `
    --security-opt no-new-privileges `
    --security-opt "seccomp=$(Join-Path $RepositoryRoot 'contracts\authored-renderer\chromium-seccomp.json')" `
    --pids-limit 128 `
    --memory 512m `
    --memory-swap 512m `
    --cpus 1 `
    --ulimit nofile=256:256 `
    --ipc private `
    --user 65532:65532 `
    $ImageName | Out-Null
  if ($LASTEXITCODE -ne 0) { throw "Docker create failed" }

  $InspectPath = Join-Path $OutputDirectory "container-inspect.json"
  docker inspect $ProductContainerName | Set-Content -LiteralPath $InspectPath -Encoding utf8
  if ($LASTEXITCODE -ne 0) { throw "Docker inspect failed" }

  $RuntimeObservation = docker start --attach $ContainerName
  $HarnessExit = $LASTEXITCODE
  $RuntimeObservation | Set-Content `
    -LiteralPath (Join-Path $OutputDirectory "security-observation.json") `
    -Encoding utf8
  if (-not $RuntimeObservation) { throw "Docker runtime observation was empty" }

  python (Join-Path $HarnessRoot "verify_container_inspect.py") `
    --inspect $InspectPath `
    --observation (Join-Path $OutputDirectory "security-observation.json") `
    --source-revision $SourceRevision `
    --launcher (Join-Path $RepositoryRoot "contracts\authored-renderer\invoke.py") `
    --image-ref $ProductImageId `
    --source-tree-digest $SourceTreeDigest `
    --contract-digest $ContractDigest
  $InspectExit = $LASTEXITCODE

  python (Join-Path $HarnessRoot "verify_product_resilience.py") `
    --observation (Join-Path $OutputDirectory "security-observation.json") `
    --launcher (Join-Path $RepositoryRoot "contracts\authored-renderer\invoke.py")
  $ResilienceExit = $LASTEXITCODE

  $GateInput = Join-Path $OutputDirectory "security-observation.json"
  if ($ElectronObservation) {
    $GateInput = Join-Path $OutputDirectory "combined-observation.json"
    python (Join-Path $ExperimentRoot "ci\combine_observations.py") `
      --container (Join-Path $OutputDirectory "security-observation.json") `
      --electron $ElectronObservation `
      --output $GateInput
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
  }

  python (Join-Path $ExperimentRoot "ci\security_gate.py") `
    --input $GateInput `
    --output-dir $OutputDirectory
  $GateExit = $LASTEXITCODE
  if (($HarnessExit -ne 0 -or $InspectExit -ne 0 -or $ResilienceExit -ne 0) -and $GateExit -eq 0) { exit 1 }
  exit $GateExit
}
finally {
  docker rm --force $ContainerName 2>$null | Out-Null
  docker rm --force $ProductContainerName 2>$null | Out-Null
}
