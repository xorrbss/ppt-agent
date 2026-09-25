<#
.SYNOPSIS
Print the resolved value of the authored-verbatim rollout knobs as the
running stack's own process environment will resolve them, one component at
a time.

.DESCRIPTION
Reuses the sanctioned wrapper pattern from
servers\fastapi\scripts\run_authored_checkpoint_repair_probe.ps1: the stack's
DPAPI-protected environment store (scripts\local-stack-lib.ps1) is decrypted
in memory only, exported to a child `uv run python` process, and the parent
process environment is restored immediately after the child exits. Nothing
from the store is ever written to disk or printed by this script -- only the
resolved knob *values* (not secrets; none of these keys are in
SECRET_SETTING_KEYS) are printed, via the same accessors production code
uses: servers\fastapi\utils\get_env_programme.py.

This is post-restart provenance for artifacts\authored-live-series-20260915\
RUNBOOK.md section 2: after `scripts\start-local-stack.ps1` restarts the
stack, run this once per relevant component to confirm every worker process
actually resolves the knob values the stage table claims, instead of trusting
that the DPAPI store write alone was enough.

.PARAMETER AsJson
Emit one machine-readable JSON object on stdout instead of the per-component
human-readable blocks, so an automated precheck can gate on it. The object
carries, per component, the resolved knob values plus the evidence that the
component's *running* process actually predates nothing: its pid from
app_data\public-v2-local\local-stack.json, that process's start time, and the
DPAPI store's last-write time. A component whose process started before the
store was last written is reported with restartRequired = true -- writing a
knob without restarting is the failure this exists to catch, and the resolved
values alone cannot show it.

.PARAMETER Components
Which stack components to check. Each is checked independently because each
runs as its own OS process with its own environment; a knob written to the
store but missing from one component's process environment is exactly the
kind of drift this script exists to catch. Defaults to the three components
scripts\set-local-stack-knob.ps1 writes by default.

.EXAMPLE
pwsh scripts\verify-local-stack-knobs.ps1
pwsh scripts\verify-local-stack-knobs.ps1 -Components @("worker-generation")
#>

[CmdletBinding()]
param(
    [string[]]$Components = @("api", "worker-generation", "worker-export"),
    [switch]$AsJson
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$fastApiRoot = Join-Path $repoRoot 'servers\fastapi'
. (Join-Path $repoRoot 'scripts\local-stack-lib.ps1')

$storePath = Join-Path $repoRoot 'app_data\public-v2-local\local-stack-env.dpapi.xml'
$store = Read-StackEnvironment $storePath
$storeWrittenAt = [DateTimeOffset]((Get-Item -LiteralPath $storePath).LastWriteTimeUtc)

# The resolved values below come from the store, which is what a component
# *would* see at its next start. Whether the component running right now sees
# them is a different question, and the one that actually invalidates a series
# stage: start-local-stack.ps1 records one pid per component here, and a
# process that started before the store's last write is running the old
# environment no matter what the store says.
$statePath = Join-Path $repoRoot 'app_data\public-v2-local\local-stack.json'
$componentProcessIds = @{}
if (Test-Path -LiteralPath $statePath) {
    $state = Get-Content -LiteralPath $statePath -Raw | ConvertFrom-Json
    foreach ($property in $state.PSObject.Properties) {
        $componentProcessIds[$property.Name] = [int]$property.Value
    }
}

function Get-ComponentRuntimeEvidence {
    param([string]$Component)

    $evidence = [ordered]@{
        processId       = $null
        startedAt       = $null
        running         = $false
        restartRequired = $true
        reason          = 'no pid recorded in local-stack.json; cannot prove the running process sees the store'
    }
    if (-not $componentProcessIds.ContainsKey($Component)) { return $evidence }

    $componentProcessId = $componentProcessIds[$Component]
    $evidence.processId = $componentProcessId
    $process = Get-Process -Id $componentProcessId -ErrorAction SilentlyContinue
    if ($null -eq $process) {
        $evidence.reason = "recorded pid $componentProcessId is not running; the component is down"
        return $evidence
    }

    $startedAt = [DateTimeOffset]$process.StartTime.ToUniversalTime()
    $evidence.running = $true
    $evidence.startedAt = $startedAt.ToString('o')
    if ($startedAt -lt $storeWrittenAt) {
        $evidence.reason = "process started $($startedAt.ToString('o')) but the store was written $($storeWrittenAt.ToString('o')); restart the stack"
        return $evidence
    }
    $evidence.restartRequired = $false
    $evidence.reason = $null
    return $evidence
}

# Printed via utils/get_env_programme.py accessors -- keep this in sync with
# that module's public knob accessors rather than reading os.environ directly,
# so this script verifies what production code actually resolves.
$probeScript = @'
import json

from utils.get_env import (
    get_authored_author_concurrency_env,
    get_authored_batch_wave_concurrency_env,
)
from utils.get_env_programme import (
    get_authored_admission_gate_mode,
    get_authored_author_prompt_model,
    get_authored_author_prompt_profile,
    get_authored_composition_envelope_profile,
    get_authored_html_normalization,
    get_authored_verbatim_repair_loop_model_calls,
    is_authored_verbatim_placeholder_authoring_enabled,
    is_authored_verbatim_single_repair_loop_enabled,
)

print(json.dumps({
    "AUTHORED_ADMISSION_GATE_MODE": get_authored_admission_gate_mode(),
    "AUTHORED_HTML_NORMALIZATION": get_authored_html_normalization(),
    "AUTHORED_VERBATIM_PLACEHOLDER_AUTHORING": is_authored_verbatim_placeholder_authoring_enabled(),
    "AUTHORED_VERBATIM_SINGLE_REPAIR_LOOP": is_authored_verbatim_single_repair_loop_enabled(),
    "AUTHORED_VERBATIM_REPAIR_LOOP_MODEL_CALLS": get_authored_verbatim_repair_loop_model_calls(),
    "AUTHORED_AUTHOR_PROMPT_PROFILE": get_authored_author_prompt_profile(),
    "AUTHORED_AUTHOR_PROMPT_MODEL": get_authored_author_prompt_model(),
    "AUTHORED_COMPOSITION_ENVELOPE_PROFILE": get_authored_composition_envelope_profile(),
    # Throughput knobs: not part of the rollout table, but the audit asserts
    # the stack agreed with the run's configuration, so it has to read the
    # resolved value from the same place the precheck does.
    "AUTHORED_BATCH_WAVE_CONCURRENCY": get_authored_batch_wave_concurrency_env(),
    "AUTHORED_AUTHOR_CONCURRENCY": get_authored_author_concurrency_env(),
}))
'@
$probeScriptPath = Join-Path ([System.IO.Path]::GetTempPath()) ("verify-local-stack-knobs-{0}.py" -f ([Guid]::NewGuid()))
Set-Content -LiteralPath $probeScriptPath -Value $probeScript -Encoding utf8NoBOM

$results = @{}
try {
    foreach ($component in $Components) {
        if (-not $store.ContainsKey($component)) { throw "component missing: $component" }

        $childEnvironment = @{}
        foreach ($entry in $store[$component].GetEnumerator()) {
            $childEnvironment[[string]$entry.Key] = $entry.Value
        }
        # fastapi.runtime accessors fall back to a standalone RuntimeSettings
        # built from os.environ when no published snapshot exists in this
        # process (utils/runtime_settings.py:get_process_runtime_settings),
        # so DATABASE_URL is not required here -- but settings-registry
        # loading still wants a coherent env, so carry it over the same way
        # the checkpoint-repair probe does, for consistency.
        foreach ($key in @('DATABASE_URL', 'APP_DATA_DIRECTORY', 'USER_CONFIG_PATH')) {
            if (-not $childEnvironment.ContainsKey($key) -and $store['api'].ContainsKey($key)) {
                $childEnvironment[$key] = $store['api'][$key]
            }
        }

        $allowedKeys = @($childEnvironment.Keys)
        $previous = @{}
        $locationPushed = $false
        try {
            foreach ($key in $allowedKeys) {
                $previous[$key] = [Environment]::GetEnvironmentVariable($key, 'Process')
                $value = $childEnvironment[$key]
                if ($null -ne $value -and -not [string]::IsNullOrWhiteSpace([string]$value)) {
                    [Environment]::SetEnvironmentVariable($key, [string]$value, 'Process')
                }
                else {
                    [Environment]::SetEnvironmentVariable($key, $null, 'Process')
                }
            }

            Push-Location $fastApiRoot
            $locationPushed = $true
            $raw = & uv run python $probeScriptPath
            if ($LASTEXITCODE -ne 0) { throw "knob probe failed for component '$component' (exit $LASTEXITCODE)" }
            $results[$component] = [pscustomobject]@{
                resolved = ($raw | ConvertFrom-Json)
                runtime  = [pscustomobject](Get-ComponentRuntimeEvidence -Component $component)
            }
        }
        finally {
            if ($locationPushed) { Pop-Location }
            foreach ($key in $allowedKeys) {
                [Environment]::SetEnvironmentVariable($key, $previous[$key], 'Process')
            }
        }
    }
}
finally {
    Remove-Item -LiteralPath $probeScriptPath -ErrorAction SilentlyContinue
}

if ($AsJson) {
    # Not $components: PowerShell variable names are case-insensitive, so that
    # name would silently overwrite the $Components parameter being iterated.
    $componentReports = [ordered]@{}
    foreach ($component in $Components) { $componentReports[$component] = $results[$component] }
    [pscustomobject]@{
        storePath      = $storePath
        storeWrittenAt = $storeWrittenAt.ToString('o')
        components     = [pscustomobject]$componentReports
    } | ConvertTo-Json -Depth 6 | Write-Output
}
else {
    foreach ($component in $Components) {
        Write-Host "== $component =="
        $results[$component].resolved | ConvertTo-Json -Depth 4 | Write-Host
        $runtime = $results[$component].runtime
        if ($runtime.restartRequired) {
            Write-Host ("   !! restart required: {0}" -f $runtime.reason)
        }
        else {
            Write-Host ("   ok: pid {0} started {1}, after the store was written" -f $runtime.processId, $runtime.startedAt)
        }
    }
}
