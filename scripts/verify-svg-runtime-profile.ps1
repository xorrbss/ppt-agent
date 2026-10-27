$ErrorActionPreference = 'Stop'
$repo = Split-Path -Parent $PSScriptRoot
. (Join-Path $repo 'scripts\local-stack-lib.ps1')
$storePath = Join-Path $repo 'app_data\public-v2-local\local-stack-env.dpapi.xml'
$components = Read-StackEnvironment $storePath
$python = Join-Path $repo 'servers\fastapi\.venv\Scripts\python.exe'
$probe = 'from services.generation_core.runtime.profile import resolve_svg_runtime_profile as r; ' +
         'import sys; x = r(source_reflow=sys.argv[1] == "true"); ' +
         'print(f"author={x.author_concurrency} render={x.render_concurrency}")'

foreach ($name in @('worker-generation', 'api', 'worker-export')) {
    $previous = @{}
    $previousPythonDontWriteBytecode = $env:PYTHONDONTWRITEBYTECODE
    foreach ($entry in $components[$name].GetEnumerator()) {
        $current = Get-Item -LiteralPath "Env:$($entry.Key)" -ErrorAction SilentlyContinue
        $previous[$entry.Key] = if ($null -eq $current) { $null } else { $current.Value }
        Set-Item -LiteralPath "Env:$($entry.Key)" -Value ([string]$entry.Value)
    }
    try {
        Push-Location (Join-Path $repo 'servers\fastapi')
        $env:PYTHONDONTWRITEBYTECODE = '1'
        foreach ($mode in @('free-design', 'source-reflow')) {
            $sourceReflow = $mode -eq 'source-reflow'
            $out = & $python -c $probe ([string]$sourceReflow).ToLowerInvariant() 2>&1 | Select-Object -Last 1
            '{0} {1}: {2}' -f $name, $mode, $out
        }
    } finally {
        Pop-Location
        foreach ($key in $previous.Keys) {
            if ($null -eq $previous[$key]) { Remove-Item -LiteralPath "Env:$key" -ErrorAction SilentlyContinue }
            else { Set-Item -LiteralPath "Env:$key" -Value $previous[$key] }
        }
        if ($null -eq $previousPythonDontWriteBytecode) { Remove-Item Env:PYTHONDONTWRITEBYTECODE -ErrorAction SilentlyContinue }
        else { Set-Item Env:PYTHONDONTWRITEBYTECODE -Value $previousPythonDontWriteBytecode }
    }
}
