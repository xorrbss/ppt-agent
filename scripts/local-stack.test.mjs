import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { test } from "node:test";

const scripts = path.dirname(fileURLToPath(import.meta.url));
const quote = (value) => `'${value.replaceAll("'", "''")}'`;

for (const shell of ["powershell.exe", "pwsh.exe"]) {
  const available = process.platform === "win32" && !spawnSync(shell, [
    "-NoProfile", "-NonInteractive", "-Command", "$PSVersionTable.PSVersion.Major",
  ]).error;

  const run = (body) => {
    const source = `
      $ErrorActionPreference = 'Stop'
      $env:PYTHONDONTWRITEBYTECODE = '1'
      . ${quote(path.join(scripts, "local-stack-lib.ps1"))}
      ${body}
    `;
    const result = spawnSync(shell, ["-NoProfile", "-NonInteractive", "-EncodedCommand",
      Buffer.from(source, "utf16le").toString("base64")], {
      encoding: "utf8", timeout: 15_000,
      env: { ...process.env, PYTHONDONTWRITEBYTECODE: "1" },
    });
    assert.equal(result.status, 0, `${shell}: ${result.stderr}`);
    return JSON.parse(result.stdout.trim());
  };

  test(`${shell}: native stderr stays separate and both pipes drain`, { skip: !available }, () => {
    const result = run(`
      $child = '[Console]::Out.Write(("x" * 100000)); [Console]::Error.Write(("y" * 100000)); exit 7'
      $encoded = [Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($child))
      $result = Invoke-CapturedCommand (Get-Process -Id $PID).Path "-NoProfile -NonInteractive -EncodedCommand $encoded" (Get-Location).Path
      @{ exit = $result.ExitCode; stdout = $result.Stdout.Length; stderr = $result.Stderr.Length;
         preference = [string]$ErrorActionPreference } | ConvertTo-Json -Compress
    `);
    assert.deepEqual(result, { exit: 7, stdout: 100000, stderr: 100000, preference: "Stop" });
  });

  test(`${shell}: readiness waits through rejecting and no-response probes`, { skip: !available }, () => {
    const result = run(`
      $probeDirectory = Join-Path ([IO.Path]::GetTempPath()) ([Guid]::NewGuid().ToString())
      New-Item -ItemType Directory $probeDirectory | Out-Null
      New-Item -ItemType File (Join-Path $probeDirectory 'pg_isready.exe') | Out-Null
      try {
        $script:probes = 0
        function Invoke-CapturedCommand {
          $script:probes++
          @{ ExitCode = $(if ($script:probes -eq 1) { 1 } elseif ($script:probes -eq 2) { 2 } else { 0 }) }
        }
        $timer = [Diagnostics.Stopwatch]::StartNew()
        Wait-ForPostgresReady $probeDirectory 21099 3
        @{ probes = $script:probes; elapsed = $timer.ElapsedMilliseconds } | ConvertTo-Json -Compress
      } finally { Remove-Item -LiteralPath (Join-Path $probeDirectory 'pg_isready.exe'); [IO.Directory]::Delete($probeDirectory) }
    `);
    assert.equal(result.probes, 3);
    assert.ok(result.elapsed >= 450 && result.elapsed < 3000);
  });

  test(`${shell}: readiness timeout has a named bounded failure`, { skip: !available }, () => {
    const result = run(`
      $probeDirectory = Join-Path ([IO.Path]::GetTempPath()) ([Guid]::NewGuid().ToString())
      New-Item -ItemType Directory $probeDirectory | Out-Null
      New-Item -ItemType File (Join-Path $probeDirectory 'pg_isready.exe') | Out-Null
      try {
        function Invoke-CapturedCommand { @{ ExitCode = 2 } }
        $timer = [Diagnostics.Stopwatch]::StartNew()
        $message = ''
        try { Wait-ForPostgresReady $probeDirectory 21099 1 } catch { $message = $_.Exception.Message }
        @{ message = $message; elapsed = $timer.ElapsedMilliseconds } | ConvertTo-Json -Compress
      } finally { Remove-Item -LiteralPath (Join-Path $probeDirectory 'pg_isready.exe'); [IO.Directory]::Delete($probeDirectory) }
    `);
    assert.match(result.message, /postgres_readiness_timeout.*21099.*1s.*exit 2/u);
    assert.ok(result.elapsed >= 900 && result.elapsed < 2000);
  });
}
