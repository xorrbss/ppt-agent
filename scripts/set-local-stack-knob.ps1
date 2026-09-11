# Set one non-secret fastapi.runtime rollout knob in the local stack's
# DPAPI-protected environment store, in memory, without ever printing the
# store's contents. Uses the same Read-StackEnvironment / Save-StackEnvironment
# helpers `start-local-stack.ps1` itself uses (scripts\local-stack-lib.ps1).
#
# Why this exists: keys registered in constants/settings_registry.json with
# targets == ["fastapi.runtime"] only (e.g. the authored-verbatim rollout
# knobs) are not reachable through PUT /api/v1/admin/provider-settings --
# `sanitize_provider_settings` (services/provider_settings_values.py) keeps
# only keys whose targets include start.user_config / user_config.managed /
# user_config.ui, so a write to one of these keys is silently dropped by that
# endpoint. `RuntimeSettings.from_sources` (utils/runtime_settings.py)
# resolves fastapi.runtime keys from the process environment, so the
# DPAPI-protected environment store owned by scripts\start-local-stack.ps1 is
# the only sanctioned write path for them on this Windows stack.
#
# This script only edits the in-memory store and writes it back; it does not
# restart the stack. A stack restart (`scripts\start-local-stack.ps1`) is
# still required for every worker process to pick up the new value -- see
# artifacts\authored-live-series-20260915\RUNBOOK.md section 2.
#
# Usage:
#   pwsh scripts\set-local-stack-knob.ps1 -Key AUTHORED_ADMISSION_GATE_MODE -Value enforce
#   pwsh scripts\set-local-stack-knob.ps1 -Key AUTHORED_VERBATIM_REPAIR_LOOP_MODEL_CALLS -Value 1 `
#       -Targets @("api", "worker-generation", "worker-export")

param(
    [Parameter(Mandatory)] [string]$Key,
    [Parameter(Mandatory)] [string]$Value,
    [string[]]$Targets = @("api", "worker-generation", "worker-export")
)
# Set one non-secret runtime knob in the local stack's DPAPI environment store,
# in memory, without printing any stored value. Same helpers the stack script uses.
Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
$repoRoot = "C:\project\PPT-agent\ppt-agent"
. (Join-Path $repoRoot "scripts\local-stack-lib.ps1")
$store = Join-Path $repoRoot "app_data\public-v2-local\local-stack-env.dpapi.xml"
$components = Read-StackEnvironment $store
$changed = @()
foreach ($name in $Targets) {
    if (-not $components.ContainsKey($name)) { throw "component missing: $name" }
    $map = $components[$name]
    $had = $map.ContainsKey($Key)
    $map[$Key] = $Value
    $changed += ("{0}:{1}" -f $name, $(if ($had) { "updated" } else { "added" }))
}
Save-StackEnvironment -Components $components -Path $store
Write-Host ("knob {0} set on {1}" -f $Key, ($changed -join ", "))
