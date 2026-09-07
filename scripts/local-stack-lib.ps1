# Process and environment helpers shared by the local stack supervisor.
#
# `start-public-v2-rc.ps1` carries equivalent helpers for its own throwaway RC
# stack. They are not shared yet because that script is a verification harness
# other work depends on and re-plumbing it is a separate change; if it is
# refactored later, it should dot-source this file rather than keep its copy.
#
# Windows PowerShell 5.1 only: no ternary, no null-coalescing, no `&&`.

Set-StrictMode -Version Latest

function Get-DescendantPids([int[]]$RootPids) {
    $pending = [System.Collections.Generic.Queue[int]]::new()
    $all = [System.Collections.Generic.HashSet[int]]::new()
    foreach ($root in $RootPids) { if ($all.Add($root)) { $pending.Enqueue($root) } }
    while ($pending.Count -gt 0) {
        $parent = $pending.Dequeue()
        foreach ($child in @(Get-CimInstance Win32_Process -Filter "ParentProcessId=$parent" -ErrorAction SilentlyContinue)) {
            $childPid = [int]$child.ProcessId
            if ($all.Add($childPid)) { $pending.Enqueue($childPid) }
        }
    }
    return @($all)
}

function Get-PortOwner([int]$Port) {
    $listener = Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($null -eq $listener) { return $null }
    return [int]$listener.OwningProcess
}

function Wait-ForListener([int]$Port, [int]$TimeoutSeconds = 120) {
    $deadline = [DateTime]::UtcNow.AddSeconds($TimeoutSeconds)
    while ([DateTime]::UtcNow -lt $deadline) {
        $owner = Get-PortOwner $Port
        if ($null -ne $owner) { return $owner }
        Start-Sleep -Milliseconds 500
    }
    throw "local stack: nothing started listening on port $Port within ${TimeoutSeconds}s"
}

function Wait-ForHttp200([string]$Url, [int]$TimeoutSeconds = 180) {
    $deadline = [DateTime]::UtcNow.AddSeconds($TimeoutSeconds)
    while ([DateTime]::UtcNow -lt $deadline) {
        try {
            $response = Invoke-WebRequest -UseBasicParsing -Uri $Url -TimeoutSec 5
            if ($response.StatusCode -eq 200) { return }
        } catch {
            Start-Sleep -Milliseconds 500
        }
    }
    throw "local stack: $Url never answered 200 within ${TimeoutSeconds}s"
}

function Start-LoggedProcess {
    param(
        [Parameter(Mandatory = $true)][string]$FilePath,
        [Parameter(Mandatory = $true)][string[]]$ArgumentList,
        [Parameter(Mandatory = $true)][string]$WorkingDirectory,
        [Parameter(Mandatory = $true)][hashtable]$Environment,
        [Parameter(Mandatory = $true)][string]$StdoutPath,
        [Parameter(Mandatory = $true)][string]$StderrPath
    )
    # Windows PowerShell has no per-child environment map. Scope the values
    # around creation, restore them immediately, and never log their contents --
    # this map carries the database password.
    $previous = @{}
    foreach ($entry in $Environment.GetEnumerator()) {
        $current = Get-Item -LiteralPath "Env:$($entry.Key)" -ErrorAction SilentlyContinue
        if ($null -eq $current) { $previous[$entry.Key] = $null } else { $previous[$entry.Key] = $current.Value }
        Set-Item -LiteralPath "Env:$($entry.Key)" -Value ([string]$entry.Value)
    }
    try {
        return Start-Process -FilePath $FilePath -ArgumentList $ArgumentList -WorkingDirectory $WorkingDirectory `
            -RedirectStandardOutput $StdoutPath -RedirectStandardError $StderrPath -PassThru -WindowStyle Hidden
    } finally {
        foreach ($entry in $previous.GetEnumerator()) {
            if ($null -eq $entry.Value) { Remove-Item -LiteralPath "Env:$($entry.Key)" -ErrorAction SilentlyContinue }
            else { Set-Item -LiteralPath "Env:$($entry.Key)" -Value $entry.Value }
        }
    }
}

function Stop-ProcessTree([int[]]$RootPids) {
    foreach ($ownedProcessId in (Get-DescendantPids $RootPids | Sort-Object -Descending)) {
        Stop-Process -Id $ownedProcessId -Force -ErrorAction SilentlyContinue
    }
}

# --- Environment store -------------------------------------------------------
#
# The stack's environment is the one thing no script owned before: it lived only
# in the shell that happened to start each process, so a restart meant reading it
# back out of a running process's PEB. It is kept here instead, DPAPI-protected
# (user- and machine-scoped, the same mechanism as `secrets.dpapi.xml`) because
# it carries the database password. One SecureString per component holds that
# component's environment as JSON.

function Save-StackEnvironment([hashtable]$Components, [string]$Path) {
    $protected = @{}
    foreach ($entry in $Components.GetEnumerator()) {
        $json = $entry.Value | ConvertTo-Json -Depth 4 -Compress
        $protected[$entry.Key] = ConvertTo-SecureString -String $json -AsPlainText -Force
    }
    $directory = Split-Path -Parent $Path
    if (-not (Test-Path -LiteralPath $directory)) { New-Item -ItemType Directory -Path $directory -Force | Out-Null }
    $protected | Export-Clixml -LiteralPath $Path
}

function Read-StackEnvironment([string]$Path) {
    if (-not (Test-Path -LiteralPath $Path)) {
        throw "local stack: no environment store at $Path. Seed it once with -ImportEnvironment <directory>."
    }
    $protected = Import-Clixml -LiteralPath $Path
    $components = @{}
    foreach ($entry in $protected.GetEnumerator()) {
        $pointer = [System.Runtime.InteropServices.Marshal]::SecureStringToBSTR($entry.Value)
        try {
            $json = [System.Runtime.InteropServices.Marshal]::PtrToStringBSTR($pointer)
        } finally {
            [System.Runtime.InteropServices.Marshal]::ZeroFreeBSTR($pointer)
        }
        $map = @{}
        foreach ($property in ($json | ConvertFrom-Json).PSObject.Properties) { $map[$property.Name] = [string]$property.Value }
        $components[$entry.Key] = $map
    }
    return $components
}
