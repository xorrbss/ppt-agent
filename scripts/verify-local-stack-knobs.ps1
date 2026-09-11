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
    [string[]]$Components = @("api", "worker-generation", "worker-export")
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$fastApiRoot = Join-Path $repoRoot 'servers\fastapi'
. (Join-Path $repoRoot 'scripts\local-stack-lib.ps1')

$store = Read-StackEnvironment (
    Join-Path $repoRoot 'app_data\public-v2-local\local-stack-env.dpapi.xml'
)

# Printed via utils/get_env_programme.py accessors -- keep this in sync with
# that module's public knob accessors rather than reading os.environ directly,
# so this script verifies what production code actually resolves.
$probeScript = @'
import json

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
            $results[$component] = ($raw | ConvertFrom-Json)
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

foreach ($component in $Components) {
    Write-Host "== $component =="
    $results[$component] | ConvertTo-Json -Depth 4 | Write-Host
}
