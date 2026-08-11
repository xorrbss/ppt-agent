#!/usr/bin/env node

import { spawn, spawnSync } from "node:child_process";
import {
  closeSync,
  createWriteStream,
  fstatSync,
  fsyncSync,
  openSync,
  writeFileSync,
} from "node:fs";
import { createHash, randomUUID } from "node:crypto";
import {
  mkdir,
  readFile,
  readdir,
  readlink,
  rename,
  rm,
  stat,
  writeFile,
} from "node:fs/promises";
import { createServer } from "node:net";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";

const LOOPBACK = "127.0.0.1";
const STARTUP_TIMEOUT_MS = 120_000;
// The first request to the presentation route causes Next dev/Turbopack to
// compile the largest route in this suite.  A favicon response proves only
// that the listener is open; without warming the route, Cypress can spend its
// page-load budget waiting on that cold compilation.  Keep the warm-up bounded
// by the same startup deadline so a real compile failure still fails clearly.
const ROUTE_WARMUP_TIMEOUT_MS = STARTUP_TIMEOUT_MS;
const POLL_INTERVAL_MS = 250;
const PROCESS_STOP_TIMEOUT_MS = 5_000;
// A cold Windows PowerShell/.NET startup can exceed the process-stop budget.
// Inspection is read-only, so give it a separate bound rather than making
// teardown wait longer after a child has already exited.
const PROCESS_INSPECTION_TIMEOUT_MS = 15_000;
const WINDOWS_JOB_SUPERVISOR_PROTOCOL_VERSION = 1;
const WINDOWS_JOB_SUPERVISOR_TERMINATION_MODE =
  "windows_job_supervisor_v1";
const WINDOWS_JOB_SUPERVISOR_SPEC_ENV =
  "PRESENTON_WORKSPACE_JOB_SUPERVISOR_SPEC_BASE64";
const WINDOWS_JOB_SUPERVISOR_SCRIPT = String.raw`
$ErrorActionPreference = "Stop"
try {
  Add-Type -TypeDefinition @'
using System;
using System.Globalization;
using System.IO;
using System.Runtime.InteropServices;
using System.Text;

public static class WorkspaceJobSupervisor
{
    private const uint CREATE_SUSPENDED = 0x00000004;
    private const uint STARTF_USESTDHANDLES = 0x00000100;
    private const uint JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000;
    private const int JobObjectExtendedLimitInformation = 9;
    private const uint PROCESS_QUERY_LIMITED_INFORMATION = 0x00001000;
    private const uint SYNCHRONIZE = 0x00100000;
    private const uint WAIT_OBJECT_0 = 0x00000000;
    private const uint WAIT_FAILED = 0xFFFFFFFF;
    private const uint INFINITE = 0xFFFFFFFF;

    [StructLayout(LayoutKind.Sequential)]
    private struct FILETIME
    {
        public uint dwLowDateTime;
        public uint dwHighDateTime;
    }

    [StructLayout(LayoutKind.Sequential)]
    private struct IO_COUNTERS
    {
        public ulong ReadOperationCount;
        public ulong WriteOperationCount;
        public ulong OtherOperationCount;
        public ulong ReadTransferCount;
        public ulong WriteTransferCount;
        public ulong OtherTransferCount;
    }

    [StructLayout(LayoutKind.Sequential)]
    private struct JOBOBJECT_BASIC_LIMIT_INFORMATION
    {
        public long PerProcessUserTimeLimit;
        public long PerJobUserTimeLimit;
        public uint LimitFlags;
        public UIntPtr MinimumWorkingSetSize;
        public UIntPtr MaximumWorkingSetSize;
        public uint ActiveProcessLimit;
        public UIntPtr Affinity;
        public uint PriorityClass;
        public uint SchedulingClass;
    }

    [StructLayout(LayoutKind.Sequential)]
    private struct JOBOBJECT_EXTENDED_LIMIT_INFORMATION
    {
        public JOBOBJECT_BASIC_LIMIT_INFORMATION BasicLimitInformation;
        public IO_COUNTERS IoInfo;
        public UIntPtr ProcessMemoryLimit;
        public UIntPtr JobMemoryLimit;
        public UIntPtr PeakProcessMemoryUsed;
        public UIntPtr PeakJobMemoryUsed;
    }

    [StructLayout(LayoutKind.Sequential, CharSet = CharSet.Unicode)]
    private struct STARTUPINFO
    {
        public int cb;
        public string lpReserved;
        public string lpDesktop;
        public string lpTitle;
        public uint dwX;
        public uint dwY;
        public uint dwXSize;
        public uint dwYSize;
        public uint dwXCountChars;
        public uint dwYCountChars;
        public uint dwFillAttribute;
        public uint dwFlags;
        public short wShowWindow;
        public short cbReserved2;
        public IntPtr lpReserved2;
        public IntPtr hStdInput;
        public IntPtr hStdOutput;
        public IntPtr hStdError;
    }

    [StructLayout(LayoutKind.Sequential)]
    private struct PROCESS_INFORMATION
    {
        public IntPtr hProcess;
        public IntPtr hThread;
        public uint dwProcessId;
        public uint dwThreadId;
    }

    [DllImport("kernel32.dll", SetLastError = true)]
    private static extern IntPtr OpenProcess(uint access, bool inheritHandle, uint processId);

    [DllImport("kernel32.dll", SetLastError = true)]
    private static extern bool GetProcessTimes(IntPtr process, out FILETIME creation, out FILETIME exit, out FILETIME kernel, out FILETIME user);

    [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    private static extern bool QueryFullProcessImageName(IntPtr process, int flags, StringBuilder executable, ref uint size);

    [DllImport("kernel32.dll", SetLastError = true)]
    private static extern IntPtr CreateJobObject(IntPtr attributes, string name);

    [DllImport("kernel32.dll", SetLastError = true)]
    private static extern bool SetInformationJobObject(IntPtr job, int infoClass, IntPtr info, uint length);

    [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    private static extern bool CreateProcessW(string applicationName, StringBuilder commandLine, IntPtr processAttributes, IntPtr threadAttributes, bool inheritHandles, uint creationFlags, IntPtr environment, string currentDirectory, ref STARTUPINFO startupInfo, out PROCESS_INFORMATION processInformation);

    [DllImport("kernel32.dll", SetLastError = true)]
    private static extern bool AssignProcessToJobObject(IntPtr job, IntPtr process);

    [DllImport("kernel32.dll", SetLastError = true)]
    private static extern uint ResumeThread(IntPtr thread);

    [DllImport("kernel32.dll", SetLastError = true)]
    private static extern uint WaitForMultipleObjects(uint count, IntPtr[] handles, bool waitAll, uint milliseconds);

    [DllImport("kernel32.dll", SetLastError = true)]
    private static extern uint WaitForSingleObject(IntPtr handle, uint milliseconds);

    [DllImport("kernel32.dll", SetLastError = true)]
    private static extern bool GetExitCodeProcess(IntPtr process, out uint exitCode);

    [DllImport("kernel32.dll", SetLastError = true)]
    private static extern bool TerminateJobObject(IntPtr job, uint exitCode);

    [DllImport("kernel32.dll", SetLastError = true)]
    private static extern bool TerminateProcess(IntPtr process, uint exitCode);

    [DllImport("kernel32.dll", SetLastError = true)]
    private static extern bool CloseHandle(IntPtr handle);

    [DllImport("kernel32.dll", SetLastError = true)]
    private static extern IntPtr GetStdHandle(int standardHandle);

    private static void Win32(bool succeeded, string operation)
    {
        if (!succeeded) throw new System.ComponentModel.Win32Exception(Marshal.GetLastWin32Error(), operation);
    }

    private static string BirthId(FILETIME creation)
    {
        ulong value = ((ulong)creation.dwHighDateTime << 32) | creation.dwLowDateTime;
        return value.ToString(CultureInfo.InvariantCulture);
    }

    private static void VerifyParent(IntPtr parent, string expectedBirthId, string expectedExecutable)
    {
        FILETIME creation, exit, kernel, user;
        Win32(GetProcessTimes(parent, out creation, out exit, out kernel, out user), "GetProcessTimes(parent)");
        if (!String.Equals(BirthId(creation), expectedBirthId, StringComparison.Ordinal))
            throw new InvalidOperationException("The workspace runner parent birth identity changed before child creation.");

        uint capacity = 32768;
        StringBuilder actualExecutable = new StringBuilder((int)capacity);
        Win32(QueryFullProcessImageName(parent, 0, actualExecutable, ref capacity), "QueryFullProcessImageName(parent)");
        string actualPath = Path.GetFullPath(actualExecutable.ToString());
        string expectedPath = Path.GetFullPath(expectedExecutable);
        if (!String.Equals(actualPath, expectedPath, StringComparison.OrdinalIgnoreCase))
            throw new InvalidOperationException("The workspace runner parent executable changed before child creation.");
    }

    public static int Run(string applicationName, string commandLine, string currentDirectory, uint parentPid, string parentBirthId, string parentExecutable)
    {
        IntPtr parent = IntPtr.Zero;
        IntPtr job = IntPtr.Zero;
        PROCESS_INFORMATION child = new PROCESS_INFORMATION();
        bool childCreated = false;
        bool childAssigned = false;
        try
        {
            parent = OpenProcess(SYNCHRONIZE | PROCESS_QUERY_LIMITED_INFORMATION, false, parentPid);
            if (parent == IntPtr.Zero) throw new System.ComponentModel.Win32Exception(Marshal.GetLastWin32Error(), "OpenProcess(parent)");
            VerifyParent(parent, parentBirthId, parentExecutable);

            job = CreateJobObject(IntPtr.Zero, null);
            if (job == IntPtr.Zero) throw new System.ComponentModel.Win32Exception(Marshal.GetLastWin32Error(), "CreateJobObject");
            JOBOBJECT_EXTENDED_LIMIT_INFORMATION limits = new JOBOBJECT_EXTENDED_LIMIT_INFORMATION();
            limits.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE;
            int limitsSize = Marshal.SizeOf(typeof(JOBOBJECT_EXTENDED_LIMIT_INFORMATION));
            IntPtr limitsPointer = Marshal.AllocHGlobal(limitsSize);
            try
            {
                Marshal.StructureToPtr(limits, limitsPointer, false);
                Win32(SetInformationJobObject(job, JobObjectExtendedLimitInformation, limitsPointer, (uint)limitsSize), "SetInformationJobObject(KILL_ON_JOB_CLOSE)");
            }
            finally
            {
                Marshal.FreeHGlobal(limitsPointer);
            }

            STARTUPINFO startup = new STARTUPINFO();
            startup.cb = Marshal.SizeOf(typeof(STARTUPINFO));
            startup.dwFlags = STARTF_USESTDHANDLES;
            startup.hStdInput = GetStdHandle(-10);
            startup.hStdOutput = GetStdHandle(-11);
            startup.hStdError = GetStdHandle(-12);
            Win32(CreateProcessW(applicationName, new StringBuilder(commandLine), IntPtr.Zero, IntPtr.Zero, true, CREATE_SUSPENDED, IntPtr.Zero, currentDirectory, ref startup, out child), "CreateProcessW(CREATE_SUSPENDED)");
            childCreated = true;
            Win32(AssignProcessToJobObject(job, child.hProcess), "AssignProcessToJobObject");
            childAssigned = true;
            if (ResumeThread(child.hThread) == UInt32.MaxValue)
                throw new System.ComponentModel.Win32Exception(Marshal.GetLastWin32Error(), "ResumeThread");
            CloseHandle(child.hThread);
            child.hThread = IntPtr.Zero;

            uint wait = WaitForMultipleObjects(2, new IntPtr[] { child.hProcess, parent }, false, INFINITE);
            if (wait == WAIT_FAILED)
                throw new System.ComponentModel.Win32Exception(Marshal.GetLastWin32Error(), "WaitForMultipleObjects");
            if (wait == WAIT_OBJECT_0 + 1)
            {
                Win32(TerminateJobObject(job, 143), "TerminateJobObject(parent exit)");
                WaitForSingleObject(child.hProcess, 5000);
                return 143;
            }
            if (wait != WAIT_OBJECT_0)
                throw new InvalidOperationException("Unexpected wait result from the workspace child supervisor.");
            uint exitCode;
            Win32(GetExitCodeProcess(child.hProcess, out exitCode), "GetExitCodeProcess");
            return unchecked((int)exitCode);
        }
        finally
        {
            if (child.hThread != IntPtr.Zero) CloseHandle(child.hThread);
            if (childCreated && !childAssigned && child.hProcess != IntPtr.Zero) TerminateProcess(child.hProcess, 125);
            if (child.hProcess != IntPtr.Zero) CloseHandle(child.hProcess);
            if (job != IntPtr.Zero) CloseHandle(job);
            if (parent != IntPtr.Zero) CloseHandle(parent);
        }
    }
}
'@
  $encodedSpec = [Environment]::GetEnvironmentVariable("${WINDOWS_JOB_SUPERVISOR_SPEC_ENV}", "Process")
  if ([String]::IsNullOrWhiteSpace($encodedSpec)) { throw "Missing workspace job supervisor specification." }
  [Environment]::SetEnvironmentVariable("${WINDOWS_JOB_SUPERVISOR_SPEC_ENV}", $null, "Process")
  $specJson = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($encodedSpec))
  $spec = $specJson | ConvertFrom-Json
  $result = [WorkspaceJobSupervisor]::Run(
    [string]$spec.applicationName,
    [string]$spec.commandLine,
    [string]$spec.currentDirectory,
    [uint32]$spec.parentPid,
    [string]$spec.parentBirthId,
    [string]$spec.parentExecutable
  )
  [Environment]::Exit($result)
} catch {
  [Console]::Error.WriteLine("workspace job supervisor failed: " + $_.Exception.ToString())
  [Environment]::Exit(125)
}
`;
const CYPRESS_TIMEOUT_MS = Number(
  process.env.WORKSPACE_E2E_CYPRESS_TIMEOUT_MS ?? 240_000,
);
const scriptDirectory = path.dirname(fileURLToPath(import.meta.url));
const appDirectory = path.resolve(scriptDirectory, "..");
const fastApiDirectory = path.resolve(appDirectory, "..", "fastapi");
const cacheRoot = path.join(appDirectory, ".next-workspace-e2e");
const runnerLockDirectory = path.join(cacheRoot, "workspace-e2e.lock");
const RUNNER_OWNER_FILE_PREFIX = "owner.";
const RUNNER_RECOVERY_CLAIM_FILE_PREFIX = "recovery.claim.";
const RUNNER_RECOVERY_OWNER_FILE_PREFIX = "recovery.owner.";
const JSON_FILE_SUFFIX = ".json";
const LOCK_TOKEN_PATTERN =
  /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/iu;
const runnerLockSnapshotFile = path.join(
  runnerLockDirectory,
  "project-files.snapshot.json",
);
const runnerLockGeneratedSnapshotFile = path.join(
  runnerLockDirectory,
  "project-files.runner-generated.json",
);
const runnerLockRunPlanFile = path.join(
  runnerLockDirectory,
  "project-files.run-plan.json",
);
const RUNNER_LOCK_INITIALIZATION_GRACE_MS = 60_000;
const RELATIVE_RUN_DIRECTORY_PATTERN =
  /^\.next-workspace-e2e\/run-([1-9][0-9]*)-([1-9][0-9]*)$/u;
const artifactDirectory = path.resolve(
  process.env.WORKSPACE_E2E_ARTIFACT_DIR ??
    path.join(appDirectory, "test-results", "workspace-e2e"),
);
const nextCli = path.join(
  appDirectory,
  "node_modules",
  "next",
  "dist",
  "bin",
  "next",
);
const cypressCli = path.join(
  appDirectory,
  "node_modules",
  "cypress",
  "bin",
  "cypress",
);
const nextGeneratedProjectFiles = [
  path.join(appDirectory, "tsconfig.json"),
  path.join(appDirectory, "next-env.d.ts"),
];

let nextProcess;
let liveBackendProcess;
let cypressProcess;
let runDirectory;
let cleanupPromise;
let receivedSignal;
let projectFileSnapshots;
let runnerLockToken;
let plannedRelativeRunDirectory;
let runnerSetupPromise;
let stopChildrenPromise;
const activeChildRecords = new Set();
const childRecordByProcess = new WeakMap();
const pendingChildLifecycleFailures = new Set();

function assertLockToken(token) {
  if (typeof token !== "string" || !LOCK_TOKEN_PATTERN.test(token)) {
    throw new Error(`Invalid workspace E2E runner lock token.`);
  }
  return token;
}

export function runnerOwnerFileName(token) {
  return `${RUNNER_OWNER_FILE_PREFIX}${assertLockToken(token)}${JSON_FILE_SUFFIX}`;
}

function recoveryClaimFileName(token) {
  return `${RUNNER_RECOVERY_CLAIM_FILE_PREFIX}${assertLockToken(token)}${JSON_FILE_SUFFIX}`;
}

function recoveryOwnerFileName(token) {
  return `${RUNNER_RECOVERY_OWNER_FILE_PREFIX}${assertLockToken(token)}${JSON_FILE_SUFFIX}`;
}

function runnerOwnerFile(lockDirectory, token) {
  return path.join(lockDirectory, runnerOwnerFileName(token));
}

function recoveryClaimFile(lockDirectory, token) {
  return path.join(lockDirectory, recoveryClaimFileName(token));
}

function recoveryOwnerFile(lockDirectory, token) {
  return path.join(lockDirectory, recoveryOwnerFileName(token));
}

function parseTokenizedFileName(fileName, prefix) {
  if (!fileName.startsWith(prefix) || !fileName.endsWith(JSON_FILE_SUFFIX)) {
    return undefined;
  }
  const token = fileName.slice(prefix.length, -JSON_FILE_SUFFIX.length);
  return LOCK_TOKEN_PATTERN.test(token) ? token : undefined;
}

function fsyncDirectorySync(directory) {
  let descriptor;
  try {
    descriptor = openSync(directory, "r");
    fsyncSync(descriptor);
  } catch (error) {
    if (
      process.platform === "win32" &&
      ["EACCES", "EINVAL", "EISDIR", "ENOTSUP", "EPERM"].includes(error?.code)
    ) {
      return;
    }
    throw error;
  } finally {
    if (descriptor !== undefined) closeSync(descriptor);
  }
}

function writeDurableFileSync(file, contents, flag = "wx") {
  let descriptor;
  try {
    descriptor = openSync(file, flag);
    writeFileSync(descriptor, contents);
    fsyncSync(descriptor);
  } finally {
    if (descriptor !== undefined) closeSync(descriptor);
  }
  fsyncDirectorySync(path.dirname(file));
}

function normalizeExecutablePath(executable) {
  if (typeof executable !== "string" || executable.trim().length === 0) {
    return undefined;
  }
  const normalized = path.normalize(executable.trim());
  return process.platform === "win32" ? normalized.toLowerCase() : normalized;
}

function hasProcessIdentityShape(identity) {
  return (
    identity &&
    typeof identity.platform === "string" &&
    typeof identity.birthId === "string" &&
    identity.birthId.length > 0 &&
    typeof normalizeExecutablePath(identity.executable) === "string"
  );
}

export function processIdentitiesMatch(expected, actual) {
  return Boolean(
    hasProcessIdentityShape(expected) &&
    hasProcessIdentityShape(actual) &&
    expected.platform === actual.platform &&
    expected.birthId === actual.birthId &&
    normalizeExecutablePath(expected.executable) ===
      normalizeExecutablePath(actual.executable),
  );
}

function quoteWindowsCommandLineArgument(value) {
  const argument = String(value);
  if (argument.length > 0 && !/[\s"]/u.test(argument)) return argument;
  let quoted = '"';
  let pendingBackslashes = 0;
  for (const character of argument) {
    if (character === "\\") {
      pendingBackslashes += 1;
      continue;
    }
    if (character === '"') {
      quoted += "\\".repeat(pendingBackslashes * 2 + 1) + '"';
      pendingBackslashes = 0;
      continue;
    }
    quoted += "\\".repeat(pendingBackslashes) + character;
    pendingBackslashes = 0;
  }
  return `${quoted}${"\\".repeat(pendingBackslashes * 2)}"`;
}

function resolveWindowsExecutable(command, cwd, env) {
  const requested = String(command);
  if (path.isAbsolute(requested)) return path.normalize(requested);
  if (requested.includes("/") || requested.includes("\\")) {
    return path.resolve(cwd, requested);
  }
  const result = spawnSync("where.exe", [requested], {
    cwd,
    encoding: "utf8",
    env,
    timeout: PROCESS_STOP_TIMEOUT_MS,
    windowsHide: true,
  });
  if (result.error) throw result.error;
  const executable = result.stdout
    ?.split(/\r?\n/u)
    .map((entry) => entry.trim())
    .find(Boolean);
  if (result.status !== 0 || !executable || !/\.(?:com|exe)$/iu.test(executable)) {
    throw new Error(
      `Could not resolve ${requested} to a native Windows executable for safe Job Object supervision.`,
    );
  }
  return path.normalize(executable);
}

export function createWindowsJobSupervisorSpawnSpec(
  applicationName,
  args,
  { cwd, env, parentPid = process.pid, parentIdentity },
) {
  if (
    !path.isAbsolute(applicationName) ||
    !Number.isSafeInteger(Number(parentPid)) ||
    Number(parentPid) <= 0 ||
    parentIdentity?.platform !== "win32" ||
    !hasProcessIdentityShape(parentIdentity)
  ) {
    throw new Error(
      `Cannot create a Windows Job Object supervisor without exact parent and target identities.`,
    );
  }
  const commandLine = [applicationName, ...args]
    .map(quoteWindowsCommandLineArgument)
    .join(" ");
  const supervisorSpec = {
    applicationName,
    commandLine,
    currentDirectory: path.resolve(cwd),
    parentPid: Number(parentPid),
    parentBirthId: parentIdentity.birthId,
    parentExecutable: parentIdentity.executable,
  };
  return {
    command: "powershell.exe",
    args: [
      "-NoLogo",
      "-NoProfile",
      "-NonInteractive",
      "-Command",
      WINDOWS_JOB_SUPERVISOR_SCRIPT,
    ],
    options: {
      cwd,
      env: {
        ...env,
        [WINDOWS_JOB_SUPERVISOR_SPEC_ENV]: Buffer.from(
          JSON.stringify(supervisorSpec),
          "utf8",
        ).toString("base64"),
      },
      stdio: "pipe",
      windowsHide: true,
      detached: false,
    },
    protocolVersion: WINDOWS_JOB_SUPERVISOR_PROTOCOL_VERSION,
    target: supervisorSpec,
    terminationMode: WINDOWS_JOB_SUPERVISOR_TERMINATION_MODE,
  };
}

export function windowsSupervisorRecoveryDisposition(
  metadata,
  currentIdentity,
  now = Date.now(),
) {
  if (
    metadata?.schemaVersion !== 3 ||
    metadata.terminationMode !== WINDOWS_JOB_SUPERVISOR_TERMINATION_MODE ||
    metadata.protocolVersion !== WINDOWS_JOB_SUPERVISOR_PROTOCOL_VERSION
  ) {
    throw new Error(
      `Legacy or invalid Windows child metadata cannot prove that an escaped tree is absent; refusing automatic recovery.`,
    );
  }
  if (metadata.identityState === "spawn_intent") {
    const intentAt = Date.parse(metadata.intentAt);
    if (!Number.isFinite(intentAt) || now - intentAt < RUNNER_LOCK_INITIALIZATION_GRACE_MS) {
      return { recoverable: false, reason: "spawn_intent_grace" };
    }
    return { recoverable: true, reason: "supervisor_parent_handle_closed" };
  }
  if (metadata.identityState === "pending") {
    return currentIdentity
      ? { recoverable: false, reason: "pending_pid_occupied" }
      : { recoverable: true, reason: "supervisor_handle_closed" };
  }
  if (
    metadata.identityState !== "verified" ||
    !hasProcessIdentityShape(metadata.processIdentity) ||
    normalizeExecutablePath(metadata.expectedExecutable) !==
      normalizeExecutablePath(metadata.processIdentity.executable)
  ) {
    throw new Error(
      `Invalid Windows supervisor identity; refusing automatic recovery.`,
    );
  }
  return processIdentitiesMatch(metadata.processIdentity, currentIdentity)
    ? { recoverable: false, reason: "exact_supervisor_still_live" }
    : { recoverable: true, reason: "supervisor_exited_or_pid_replaced" };
}

function parseLinuxProcessStat(rawStat) {
  const commandEnd = rawStat.lastIndexOf(")");
  if (commandEnd < 0) {
    throw new Error(`Could not parse Linux process stat data.`);
  }
  // Fields after comm begin with field 3 (state); starttime is field 22.
  const fields = rawStat
    .slice(commandEnd + 1)
    .trim()
    .split(/\s+/u);
  const startTicks = fields[19];
  if (!startTicks) {
    throw new Error(`Linux process stat data has no start time.`);
  }
  return startTicks;
}

function runPowerShellJson(script) {
  const utf8Script =
    `[Console]::OutputEncoding = [Text.UTF8Encoding]::new($false); ` + script;
  const result = spawnSync(
    "powershell.exe",
    ["-NoLogo", "-NoProfile", "-NonInteractive", "-Command", utf8Script],
    {
      encoding: "utf8",
      timeout: PROCESS_INSPECTION_TIMEOUT_MS,
      windowsHide: true,
    },
  );
  if (result.error) throw result.error;
  if (result.status === 3) return undefined;
  if (result.status !== 0) {
    throw new Error(
      `PowerShell process inspection failed with status ${result.status}: ${result.stderr?.trim() || "no stderr"}`,
    );
  }
  const output = result.stdout.trim();
  return output ? JSON.parse(output) : undefined;
}

function windowsProcessIdentity(pid) {
  const processRecord = runPowerShellJson(
    `$processRecord = Get-Process -Id ${pid} -ErrorAction SilentlyContinue; ` +
      `if ($null -eq $processRecord) { exit 3 }; ` +
      `[ordered]@{ ProcessId = [int]$processRecord.Id; ` +
      `CreationFileTime = [Convert]::ToString($processRecord.StartTime.ToUniversalTime().ToFileTimeUtc(), [Globalization.CultureInfo]::InvariantCulture); ` +
      `ExecutablePath = [string]$processRecord.Path } | ` +
      `ConvertTo-Json -Compress`,
  );
  if (!processRecord) return undefined;
  if (!processRecord.CreationFileTime || !processRecord.ExecutablePath) {
    throw new Error(
      `Windows did not expose a complete identity for pid=${pid}.`,
    );
  }
  return {
    platform: "win32",
    birthId: processRecord.CreationFileTime,
    executable: processRecord.ExecutablePath,
  };
}

export async function readProcessIdentity(pid) {
  if (!Number.isSafeInteger(pid) || pid <= 0) return undefined;
  if (process.platform === "win32") return windowsProcessIdentity(pid);
  if (process.platform === "linux") {
    try {
      const [rawStat, executable, bootId] = await Promise.all([
        readFile(`/proc/${pid}/stat`, "utf8"),
        readlink(`/proc/${pid}/exe`),
        readFile("/proc/sys/kernel/random/boot_id", "utf8"),
      ]);
      return {
        platform: "linux",
        birthId: `${bootId.trim()}:${parseLinuxProcessStat(rawStat)}`,
        executable,
      };
    } catch (error) {
      if (error?.code === "ENOENT" || error?.code === "ESRCH") return undefined;
      throw error;
    }
  }

  // `ps lstart` is only second-resolution on several POSIX implementations.
  // Killing by that identity can target a same-second PID reuse, so unsupported
  // platforms fail closed instead of attempting destructive cleanup.
  throw new Error(
    `Workspace E2E process fencing is unsupported on ${process.platform}; use Linux or Windows.`,
  );
}

async function captureSpawnedProcessIdentity(child, name) {
  let lastError;
  for (let attempt = 0; attempt < 10; attempt += 1) {
    try {
      const identity = await readProcessIdentity(child.pid);
      if (identity) return identity;
    } catch (error) {
      lastError = error;
    }
    if (child.exitCode !== null || child.signalCode !== null) break;
    await delay(25 * 2 ** Math.min(attempt, 4));
  }
  throw new Error(`Could not capture the OS birth identity for ${name}.`, {
    cause: lastError,
  });
}

async function ownerProcessIsCurrent(owner) {
  const pid = Number(owner?.pid);
  if (!Number.isSafeInteger(pid) || pid <= 0) return false;
  const currentIdentity = await readProcessIdentity(pid);
  if (
    hasProcessIdentityShape(owner.processIdentity) &&
    normalizeExecutablePath(owner.expectedExecutable) ===
      normalizeExecutablePath(owner.processIdentity.executable)
  ) {
    return processIdentitiesMatch(owner.processIdentity, currentIdentity);
  }
  // Older or partially written owner records cannot prove PID birth identity.
  // A currently occupied PID is therefore ambiguous and must block takeover.
  return Boolean(currentIdentity);
}

async function tokenizedFiles(lockDirectory, prefix) {
  let entries;
  try {
    entries = await readdir(lockDirectory, { withFileTypes: true });
  } catch (error) {
    if (error?.code === "ENOENT") return [];
    throw error;
  }
  return entries
    .filter((entry) => entry.isFile())
    .map((entry) => ({
      fileName: entry.name,
      token: parseTokenizedFileName(entry.name, prefix),
    }))
    .filter((entry) => entry.token);
}

async function readRunnerLockOwner(lockDirectory = runnerLockDirectory) {
  const ownerFiles = await tokenizedFiles(
    lockDirectory,
    RUNNER_OWNER_FILE_PREFIX,
  );
  if (ownerFiles.length > 1) {
    throw new Error(`The workspace E2E runner lock has multiple owners.`);
  }
  if (ownerFiles.length === 0) return undefined;
  const entry = ownerFiles[0];
  const owner = await readJsonFile(path.join(lockDirectory, entry.fileName));
  if (!owner || owner.token !== entry.token) {
    throw new Error(`The workspace E2E runner lock owner is invalid.`);
  }
  return owner;
}

async function readJsonFile(file) {
  try {
    return JSON.parse(await readFile(file, "utf8"));
  } catch (error) {
    if (error?.code === "ENOENT") return undefined;
    throw error;
  }
}

function projectFileRelativePath(file) {
  const relative = path.relative(appDirectory, file).split(path.sep).join("/");
  if (!relative || relative.startsWith("../") || path.isAbsolute(relative)) {
    throw new Error(
      `Refusing to snapshot a project file outside the Next app: ${file}`,
    );
  }
  return relative;
}

function validateDurableSnapshot(snapshot) {
  if (![1, 2].includes(snapshot?.schemaVersion) || !Array.isArray(snapshot.files)) {
    throw new Error(
      `The workspace E2E runner lock contains an invalid project snapshot.`,
    );
  }
  const expected = new Set(
    nextGeneratedProjectFiles.map(projectFileRelativePath),
  );
  if (snapshot.files.length !== expected.size) {
    throw new Error(
      `The workspace E2E runner lock project snapshot is incomplete.`,
    );
  }
  return snapshot.files.map((entry) => {
    if (
      !entry ||
      typeof entry.path !== "string" ||
      !expected.delete(entry.path)
    ) {
      throw new Error(
        `The workspace E2E runner lock project snapshot has an unexpected path.`,
      );
    }
    if (
      typeof entry.contentsBase64 !== "string" ||
      typeof entry.sha256 !== "string"
    ) {
      throw new Error(
        `The workspace E2E runner lock project snapshot has invalid contents.`,
      );
    }
    const contents = Buffer.from(entry.contentsBase64, "base64");
    const sha256 = createHash("sha256").update(contents).digest("hex");
    if (sha256 !== entry.sha256) {
      throw new Error(
        `The workspace E2E runner lock project snapshot failed its checksum.`,
      );
    }
    const gitHeadObject = entry.gitHeadObject;
    if (
      snapshot.schemaVersion === 2 &&
      gitHeadObject !== null &&
      (typeof gitHeadObject !== "string" ||
        !/^(?:[0-9a-f]{40}|[0-9a-f]{64})$/iu.test(gitHeadObject))
    ) {
      throw new Error(
        `The workspace E2E runner lock project snapshot has invalid Git HEAD proof.`,
      );
    }
    return {
      file: path.join(appDirectory, ...entry.path.split("/")),
      contents,
      gitHeadObject:
        snapshot.schemaVersion === 2 ? (gitHeadObject ?? undefined) : undefined,
    };
  });
}

function parseRelativeRunDirectory(relativeRunDirectory, expectedPid) {
  if (typeof relativeRunDirectory !== "string") return undefined;
  const match = RELATIVE_RUN_DIRECTORY_PATTERN.exec(relativeRunDirectory);
  if (!match) return undefined;
  const pid = Number(match[1]);
  const port = Number(match[2]);
  if (
    !Number.isSafeInteger(pid) ||
    pid <= 0 ||
    !Number.isSafeInteger(port) ||
    port <= 0 ||
    port > 65_535 ||
    (expectedPid !== undefined && pid !== Number(expectedPid))
  ) {
    return undefined;
  }
  return { pid, port };
}

function uniqueBuffers(buffers) {
  const seen = new Set();
  return buffers.filter((contents) => {
    const key = contents.toString("base64");
    if (seen.has(key)) return false;
    seen.add(key);
    return true;
  });
}

function nextEnvLineEnding(contents) {
  const text = contents.toString("utf8");
  const firstLf = text.indexOf("\n", 1);
  if (firstLf === -1) return os.EOL;
  return text[firstLf - 1] === "\r" ? "\r\n" : "\n";
}

function plannedNextEnvCandidates(originalContents, relativeRunDirectory) {
  const original = originalContents.toString("utf8");
  const recognizedReferences = [
    '/// <reference types="next" />',
    '/// <reference types="next/image-types/global" />',
    '/// <reference types="next/navigation-types/compat/navigation" />',
  ].filter((line) => original.includes(line));
  if (recognizedReferences[0] !== '/// <reference types="next" />') {
    return [originalContents];
  }
  const strictRouteSuffixes = [
    "types/cache-life.d.ts",
    "types/validator.ts",
    "types/link.d.ts",
  ].filter((suffix) => original.includes(`/${suffix}`));
  const documentationSurface = original.includes(
    "nextjs.org/docs/pages/api-reference/config/typescript",
  )
    ? "pages"
    : "app";
  const eol = nextEnvLineEnding(originalContents);
  const candidates = [originalContents];
  for (const developmentSegment of ["/dev", ""]) {
    const distDirectory = `${relativeRunDirectory}${developmentSegment}`;
    const lines = [
      ...recognizedReferences,
      `import "./${distDirectory}/types/routes.d.ts";`,
      ...strictRouteSuffixes.map(
        (suffix) => `import "./${distDirectory}/${suffix}";`,
      ),
      "",
      "// NOTE: This file should not be edited",
      `// see https://nextjs.org/docs/${documentationSurface}/api-reference/config/typescript for more information.`,
    ];
    candidates.push(Buffer.from(`${lines.join(eol)}${eol}`));
  }
  return uniqueBuffers(candidates);
}

function plannedTsConfigCandidates(originalContents, relativeRunDirectory) {
  const candidates = [originalContents];
  try {
    const parsed = JSON.parse(originalContents.toString("utf8"));
    if (!Array.isArray(parsed.include)) return candidates;
    const expectedIncludes = [
      `${relativeRunDirectory}/types/**/*.ts`,
      `${relativeRunDirectory}/dev/types/**/*.ts`,
    ];
    const missingIncludes = expectedIncludes.filter(
      (entry) => !parsed.include.includes(entry),
    );
    if (missingIncludes.length === 0) return candidates;
    const generated = {
      ...parsed,
      include: [...parsed.include, ...missingIncludes],
    };
    candidates.push(
      Buffer.from(`${JSON.stringify(generated, null, 2)}${os.EOL}`),
    );
  } catch {
    // If the file is JSON-with-comments or otherwise cannot be reproduced
    // byte-for-byte, the only safe planned state is the original itself.
  }
  return uniqueBuffers(candidates);
}

export function plannedRunnerGeneratedProjectFileCandidates(
  relativePath,
  originalContents,
  relativeRunDirectory,
) {
  if (
    !Buffer.isBuffer(originalContents) ||
    !parseRelativeRunDirectory(relativeRunDirectory)
  ) {
    return [];
  }
  if (relativePath === "tsconfig.json") {
    return plannedTsConfigCandidates(originalContents, relativeRunDirectory);
  }
  if (relativePath === "next-env.d.ts") {
    return plannedNextEnvCandidates(originalContents, relativeRunDirectory);
  }
  return [];
}

export function isExpectedRunnerGeneratedProjectFile(
  relativePath,
  originalContents,
  generatedContents,
  relativeRunDirectory,
) {
  if (!Buffer.isBuffer(generatedContents)) return false;
  return plannedRunnerGeneratedProjectFileCandidates(
    relativePath,
    originalContents,
    relativeRunDirectory,
  ).some((candidate) => candidate.equals(generatedContents));
}

export function inferRelativeRunDirectoryFromNextEnv(
  originalContents,
  generatedContents,
  expectedPid,
) {
  if (originalContents.equals(generatedContents)) return undefined;
  const generated = generatedContents.toString("utf8").replaceAll("\r\n", "\n");
  const candidatePattern =
    /^import "\.\/(\.next-workspace-e2e\/run-[1-9][0-9]*-[1-9][0-9]*)\/(?:dev\/)?types\/routes\.d\.ts";$/gmu;
  const candidates = [...generated.matchAll(candidatePattern)];
  if (candidates.length !== 1) return undefined;
  const relativeRunDirectory = candidates[0][1];
  if (!parseRelativeRunDirectory(relativeRunDirectory, expectedPid)) {
    return undefined;
  }
  return isExpectedRunnerGeneratedProjectFile(
    "next-env.d.ts",
    originalContents,
    generatedContents,
    relativeRunDirectory,
  )
    ? relativeRunDirectory
    : undefined;
}

function exactBytesDescriptor(contents) {
  return {
    contentsBase64: contents.toString("base64"),
    sha256: createHash("sha256").update(contents).digest("hex"),
  };
}

function decodeExactBytesDescriptor(descriptor, label) {
  if (
    !descriptor ||
    typeof descriptor.contentsBase64 !== "string" ||
    typeof descriptor.sha256 !== "string"
  ) {
    throw new Error(`${label} has invalid exact-byte contents.`);
  }
  const contents = Buffer.from(descriptor.contentsBase64, "base64");
  if (createHash("sha256").update(contents).digest("hex") !== descriptor.sha256) {
    throw new Error(`${label} failed its exact-byte checksum.`);
  }
  return contents;
}

function expectedCandidateSet(original, relativePath, relativeRunDirectory) {
  return plannedRunnerGeneratedProjectFileCandidates(
    relativePath,
    original.contents,
    relativeRunDirectory,
  );
}

function validateDurableRunnerGeneratedSnapshot(
  snapshot,
  originalSnapshots,
  owner,
) {
  if (
    ![1, 2].includes(snapshot?.schemaVersion) ||
    snapshot.ownerToken !== owner?.token ||
    !Array.isArray(snapshot.files) ||
    !parseRelativeRunDirectory(snapshot.relativeRunDirectory, owner?.pid)
  ) {
    throw new Error(
      `The workspace E2E runner lock contains an invalid generated-file snapshot.`,
    );
  }
  const originalsByPath = new Map(
    originalSnapshots.map((entry) => [
      projectFileRelativePath(entry.file),
      entry,
    ]),
  );
  if (snapshot.files.length !== originalsByPath.size) {
    throw new Error(
      `The workspace E2E runner lock generated-file snapshot is incomplete.`,
    );
  }
  const candidatesByPath = new Map();
  for (const entry of snapshot.files) {
    const original = originalsByPath.get(entry?.path);
    if (!original) {
      throw new Error(
        `The workspace E2E runner lock generated-file snapshot has an unexpected path.`,
      );
    }
    const expectedCandidates = expectedCandidateSet(
      original,
      entry.path,
      snapshot.relativeRunDirectory,
    );
    let candidates;
    let observed;
    if (snapshot.schemaVersion === 1) {
      observed = decodeExactBytesDescriptor(
        entry,
        `The legacy generated snapshot for ${entry.path}`,
      );
      candidates = expectedCandidates;
    } else {
      if (
        !["planned", "observed"].includes(snapshot.state) ||
        !Array.isArray(entry.candidates)
      ) {
        throw new Error(
          `The workspace E2E runner lock generated-file plan is invalid.`,
        );
      }
      candidates = entry.candidates.map((candidate) =>
        decodeExactBytesDescriptor(
          candidate,
          `The generated-file candidate for ${entry.path}`,
        ),
      );
      const expectedKeys = new Set(
        expectedCandidates.map((candidate) => candidate.toString("base64")),
      );
      const actualKeys = new Set(
        candidates.map((candidate) => candidate.toString("base64")),
      );
      if (
        expectedKeys.size !== actualKeys.size ||
        [...expectedKeys].some((key) => !actualKeys.has(key))
      ) {
        throw new Error(
          `The generated-file plan for ${entry.path} does not match the exact pre-spawn byte candidates.`,
        );
      }
      if (snapshot.state === "observed") {
        observed = decodeExactBytesDescriptor(
          entry.observed,
          `The observed generated snapshot for ${entry.path}`,
        );
      }
    }
    if (
      observed &&
      !candidates.some((candidate) => candidate.equals(observed))
    ) {
      throw new Error(
        `The workspace E2E runner lock contains an unplanned generated project file.`,
      );
    }
    if (observed) original.runnerGeneratedContents = observed;
    candidatesByPath.set(entry.path, candidates);
    originalsByPath.delete(entry.path);
  }
  if (originalsByPath.size !== 0) {
    throw new Error(
      `The workspace E2E runner lock generated-file snapshot is incomplete.`,
    );
  }
  return {
    candidatesByPath,
    relativeRunDirectory: snapshot.relativeRunDirectory,
    snapshot,
    state: snapshot.schemaVersion === 1 ? "observed" : snapshot.state,
  };
}

function validateDurableRunPlan(plan, owner) {
  if (
    plan?.schemaVersion !== 1 ||
    plan.ownerToken !== owner?.token ||
    Number(plan.ownerPid) !== Number(owner?.pid) ||
    !parseRelativeRunDirectory(plan.relativeRunDirectory, owner?.pid)
  ) {
    throw new Error(
      `The workspace E2E runner lock contains an invalid run plan.`,
    );
  }
  return plan.relativeRunDirectory;
}

async function persistProjectFileRunPlan(relativeRunDirectory) {
  if (!parseRelativeRunDirectory(relativeRunDirectory, process.pid)) {
    throw new Error(`Refusing to persist an invalid workspace E2E run plan.`);
  }
  const ownerToken = assertLockToken(runnerLockToken);
  const runPlan = {
    schemaVersion: 1,
    ownerToken,
    ownerPid: process.pid,
    relativeRunDirectory,
    createdAt: new Date().toISOString(),
  };
  const temporaryFile = path.join(
    runnerLockDirectory,
    `project-files.run-plan.${ownerToken}.${randomUUID()}.tmp`,
  );
  writeDurableFileSync(
    temporaryFile,
    `${JSON.stringify(runPlan, null, 2)}\n`,
  );
  await rename(temporaryFile, runnerLockRunPlanFile);
  fsyncDirectorySync(runnerLockDirectory);
}

async function classifyRunnerGeneratedProjectFiles(
  snapshots,
  relativeRunDirectory,
  candidatesByPath,
) {
  const generatedFiles = [];
  for (const snapshot of snapshots) {
    const contents = await readFile(snapshot.file);
    const relativePath = projectFileRelativePath(snapshot.file);
    const candidates =
      candidatesByPath?.get(relativePath) ??
      plannedRunnerGeneratedProjectFileCandidates(
        relativePath,
        snapshot.contents,
        relativeRunDirectory,
      );
    if (!candidates.some((candidate) => candidate.equals(contents))) {
      throw new Error(
        `Refusing to classify concurrent or unexpected changes to ${snapshot.file} as runner-generated.`,
      );
    }
    generatedFiles.push({ snapshot, relativePath, contents });
  }
  return generatedFiles;
}

function buildPlannedRunnerGeneratedSnapshot(
  snapshots,
  relativeRunDirectory,
  ownerToken,
) {
  return {
    schemaVersion: 2,
    state: "planned",
    ownerToken: assertLockToken(ownerToken),
    relativeRunDirectory,
    plannedAt: new Date().toISOString(),
    files: snapshots.map((snapshot) => {
      const relativePath = projectFileRelativePath(snapshot.file);
      return {
        path: relativePath,
        candidates: plannedRunnerGeneratedProjectFileCandidates(
          relativePath,
          snapshot.contents,
          relativeRunDirectory,
        ).map(exactBytesDescriptor),
      };
    }),
  };
}

async function writeRunnerGeneratedSnapshot(durableSnapshot, ownerToken) {
  const token = assertLockToken(ownerToken);
  const temporaryFile = path.join(
    runnerLockDirectory,
    `project-files.runner-generated.${token}.${randomUUID()}.tmp`,
  );
  writeDurableFileSync(
    temporaryFile,
    `${JSON.stringify(durableSnapshot, null, 2)}\n`,
  );
  await rename(temporaryFile, runnerLockGeneratedSnapshotFile);
  fsyncDirectorySync(runnerLockDirectory);
}

async function persistPlannedRunnerGeneratedProjectFiles(
  snapshots,
  relativeRunDirectory,
  ownerToken = runnerLockToken,
) {
  const durableSnapshot = buildPlannedRunnerGeneratedSnapshot(
    snapshots,
    relativeRunDirectory,
    ownerToken,
  );
  await writeRunnerGeneratedSnapshot(durableSnapshot, ownerToken);
  return durableSnapshot;
}

async function persistRunnerGeneratedProjectFiles(
  generatedFiles,
  relativeRunDirectory,
  ownerToken,
  validatedPlan,
) {
  const plannedSnapshot =
    validatedPlan?.snapshot?.schemaVersion === 2
      ? validatedPlan.snapshot
      : buildPlannedRunnerGeneratedSnapshot(
          generatedFiles.map(({ snapshot }) => snapshot),
          relativeRunDirectory,
          ownerToken,
        );
  const observedByPath = new Map(
    generatedFiles.map(({ relativePath, contents }) => [
      relativePath,
      contents,
    ]),
  );
  const durableSnapshot = {
    ...plannedSnapshot,
    schemaVersion: 2,
    state: "observed",
    ownerToken: assertLockToken(ownerToken),
    relativeRunDirectory,
    capturedAt: new Date().toISOString(),
    files: plannedSnapshot.files.map((entry) => {
      const contents = observedByPath.get(entry.path);
      if (!contents) {
        throw new Error(
          `Cannot persist an incomplete observed generated-file snapshot.`,
        );
      }
      return { ...entry, observed: exactBytesDescriptor(contents) };
    }),
  };
  await writeRunnerGeneratedSnapshot(durableSnapshot, ownerToken);
}

function attachRunnerGeneratedContents(generatedFiles) {
  for (const { snapshot, contents } of generatedFiles) {
    snapshot.runnerGeneratedContents = contents;
  }
}

async function persistProjectFileSnapshots(snapshots) {
  const temporaryFile = path.join(
    runnerLockDirectory,
    `project-files.snapshot.${runnerLockToken}.tmp`,
  );
  const snapshot = {
    schemaVersion: 2,
    createdAt: new Date().toISOString(),
    files: snapshots.map(({ file, contents, gitHeadObject }) => ({
      path: projectFileRelativePath(file),
      contentsBase64: contents.toString("base64"),
      sha256: createHash("sha256").update(contents).digest("hex"),
      gitHeadObject: gitHeadObject ?? null,
    })),
  };
  writeDurableFileSync(temporaryFile, `${JSON.stringify(snapshot, null, 2)}\n`);
  await rename(temporaryFile, runnerLockSnapshotFile);
  fsyncDirectorySync(runnerLockDirectory);
}

async function captureAndPersistRunnerGeneratedProjectFiles(
  snapshots,
  relativeRunDirectory,
  ownerToken = runnerLockToken,
) {
  const owner = { token: ownerToken, pid: process.pid };
  const rawPlannedSnapshot = await readJsonFile(
    runnerLockGeneratedSnapshotFile,
  );
  if (!rawPlannedSnapshot) {
    throw new Error(
      `The exact generated-file plan was not persisted before child spawn.`,
    );
  }
  const validatedPlan = validateDurableRunnerGeneratedSnapshot(
    rawPlannedSnapshot,
    snapshots,
    owner,
  );
  const generatedFiles = await classifyRunnerGeneratedProjectFiles(
    snapshots,
    relativeRunDirectory,
    validatedPlan.candidatesByPath,
  );
  await persistRunnerGeneratedProjectFiles(
    generatedFiles,
    relativeRunDirectory,
    ownerToken,
    validatedPlan,
  );
  attachRunnerGeneratedContents(generatedFiles);
}

async function currentSnapshotsMatchOriginal(snapshots) {
  const currentFiles = await Promise.all(
    snapshots.map(async (snapshot) => ({
      snapshot,
      contents: await readOptionalFile(snapshot.file),
    })),
  );
  if (
    currentFiles.some(
      ({ snapshot, contents }) => !contents?.equals(snapshot.contents),
    )
  ) {
    return false;
  }
  attachRunnerGeneratedContents(currentFiles);
  return true;
}

async function inferLegacyRelativeRunDirectory(snapshots, owner) {
  const nextEnvSnapshot = snapshots.find(
    (snapshot) => projectFileRelativePath(snapshot.file) === "next-env.d.ts",
  );
  if (!nextEnvSnapshot) return undefined;
  const current = await readOptionalFile(nextEnvSnapshot.file);
  if (!current) return undefined;
  return inferRelativeRunDirectoryFromNextEnv(
    nextEnvSnapshot.contents,
    current,
    owner.pid,
  );
}

async function restoreDurableProjectSnapshot(owner) {
  const ownerToken = assertLockToken(owner?.token);
  let rawSnapshot;
  try {
    rawSnapshot = await readJsonFile(runnerLockSnapshotFile);
  } catch (error) {
    throw new Error(
      `The stale workspace E2E lock has an unreadable project snapshot; refusing automatic takeover. ` +
        `Restore tsconfig.json and next-env.d.ts manually before removing ${runnerLockDirectory}.`,
      { cause: error },
    );
  }
  if (!rawSnapshot) {
    throw new Error(
      `The stale workspace E2E lock has no durable project snapshot; refusing automatic takeover. ` +
        `Restore tsconfig.json and next-env.d.ts manually before removing ${runnerLockDirectory}.`,
    );
  }
  let snapshots;
  try {
    snapshots = validateDurableSnapshot(rawSnapshot);
  } catch (error) {
    throw new Error(
      `The stale workspace E2E lock has an invalid project snapshot; refusing automatic takeover. ` +
        `Restore tsconfig.json and next-env.d.ts manually before removing ${runnerLockDirectory}.`,
      { cause: error },
    );
  }
  const rawGeneratedSnapshot = await readJsonFile(
    runnerLockGeneratedSnapshotFile,
  ).catch((error) => {
    throw new Error(
      `The stale workspace E2E lock has an unreadable generated-file snapshot; refusing automatic takeover.`,
      { cause: error },
    );
  });
  if (rawGeneratedSnapshot) {
    const validatedPlan = validateDurableRunnerGeneratedSnapshot(
      rawGeneratedSnapshot,
      snapshots,
      owner,
    );
    if (validatedPlan.state === "planned") {
      const generatedFiles = await classifyRunnerGeneratedProjectFiles(
        snapshots,
        validatedPlan.relativeRunDirectory,
        validatedPlan.candidatesByPath,
      );
      // Complete a pre-spawn plan before the first restore CAS. A crash at any
      // point after this durable replace can replay only the observed bytes.
      await persistRunnerGeneratedProjectFiles(
        generatedFiles,
        validatedPlan.relativeRunDirectory,
        ownerToken,
        validatedPlan,
      );
      attachRunnerGeneratedContents(generatedFiles);
    }
  } else if (!(await currentSnapshotsMatchOriginal(snapshots))) {
    let rawRunPlan;
    try {
      rawRunPlan = await readJsonFile(runnerLockRunPlanFile);
    } catch (error) {
      throw new Error(
        `The stale workspace E2E lock has an unreadable run plan; refusing automatic takeover.`,
        { cause: error },
      );
    }
    const relativeRunDirectory = rawRunPlan
      ? validateDurableRunPlan(rawRunPlan, owner)
      : await inferLegacyRelativeRunDirectory(snapshots, owner);
    if (!relativeRunDirectory) {
      throw new Error(
        `The stale workspace E2E lock has changed project files but no provable run plan; refusing automatic takeover.`,
      );
    }
    const generatedFiles = await classifyRunnerGeneratedProjectFiles(
      snapshots,
      relativeRunDirectory,
    );
    // Persist the reconstructed exact bytes before the first restore CAS so a
    // second crash can replay recovery without inference.
    await persistRunnerGeneratedProjectFiles(
      generatedFiles,
      relativeRunDirectory,
      ownerToken,
    );
    attachRunnerGeneratedContents(generatedFiles);
  }
  await restoreProjectFiles(snapshots, {
    lockDirectory: runnerLockDirectory,
    ownerToken,
  });
}

async function readRecoveryClaim(lockDirectory = runnerLockDirectory) {
  const claimFiles = await tokenizedFiles(
    lockDirectory,
    RUNNER_RECOVERY_CLAIM_FILE_PREFIX,
  );
  if (claimFiles.length > 1) {
    throw new Error(
      `The workspace E2E runner lock has multiple recovery claims.`,
    );
  }
  if (claimFiles.length === 0) return undefined;
  const entry = claimFiles[0];
  const claimFile = path.join(lockDirectory, entry.fileName);
  const [staleOwner, recoveryOwner, claimStat] = await Promise.all([
    readJsonFile(claimFile),
    readJsonFile(recoveryOwnerFile(lockDirectory, entry.token)),
    stat(claimFile),
  ]);
  if (
    !staleOwner ||
    !LOCK_TOKEN_PATTERN.test(staleOwner.token) ||
    !Number.isSafeInteger(Number(staleOwner.pid)) ||
    Number(staleOwner.pid) <= 0
  ) {
    throw new Error(`The workspace E2E recovery claim has no stale owner.`);
  }
  return {
    claimFile,
    claimToken: entry.token,
    claimStat,
    recoveryOwner,
    staleOwner,
  };
}

export async function claimRunnerOwnerByToken(
  lockDirectory,
  observedOwner,
  claimToken,
) {
  assertLockToken(observedOwner?.token);
  assertLockToken(claimToken);
  const source = runnerOwnerFile(lockDirectory, observedOwner.token);
  const destination = recoveryClaimFile(lockDirectory, claimToken);
  await rename(source, destination);
  fsyncDirectorySync(lockDirectory);
  const movedOwner = await readJsonFile(destination);
  if (
    !movedOwner ||
    movedOwner.token !== observedOwner.token ||
    Number(movedOwner.pid) !== Number(observedOwner.pid)
  ) {
    throw new Error(
      `The workspace E2E lock owner did not match the token-specific recovery source; refusing takeover.`,
    );
  }
  return { claimFile: destination, staleOwner: movedOwner };
}

async function takeOverRecoveryClaim(existingClaim, claimToken) {
  assertLockToken(existingClaim?.claimToken);
  assertLockToken(claimToken);
  const destination = recoveryClaimFile(runnerLockDirectory, claimToken);
  await rename(existingClaim.claimFile, destination);
  fsyncDirectorySync(runnerLockDirectory);
  const movedOwner = await readJsonFile(destination);
  if (
    !movedOwner ||
    movedOwner.token !== existingClaim.staleOwner.token ||
    Number(movedOwner.pid) !== Number(existingClaim.staleOwner.pid)
  ) {
    throw new Error(
      `The workspace E2E stale recovery claim changed during token takeover.`,
    );
  }
  return { claimFile: destination, staleOwner: movedOwner };
}

async function recoveryOwnerIsCurrent(recoveryOwner, claimToken) {
  const pid = Number(recoveryOwner?.pid);
  if (!Number.isSafeInteger(pid) || pid <= 0) return false;
  const currentIdentity = await readProcessIdentity(pid);
  if (
    recoveryOwner?.schemaVersion === 2 &&
    recoveryOwner.claimToken === claimToken &&
    hasProcessIdentityShape(recoveryOwner.processIdentity) &&
    normalizeExecutablePath(recoveryOwner.expectedExecutable) ===
      normalizeExecutablePath(recoveryOwner.processIdentity.executable)
  ) {
    return processIdentitiesMatch(
      recoveryOwner.processIdentity,
      currentIdentity,
    );
  }
  // A legacy or torn recovery-owner record with a live PID is ambiguous. Do
  // not let a second reaper race it; an operator can resolve the retained lock.
  return Boolean(currentIdentity);
}

async function claimStaleLockRecovery({ observedOwner, existingClaim }) {
  const claimToken = randomUUID();
  const claim = existingClaim
    ? await takeOverRecoveryClaim(existingClaim, claimToken)
    : await claimRunnerOwnerByToken(
        runnerLockDirectory,
        observedOwner,
        claimToken,
      );
  const processIdentity = await readProcessIdentity(process.pid);
  if (!hasProcessIdentityShape(processIdentity)) {
    throw new Error(
      `Could not capture the recovery runner's OS birth identity; the tokenized claim was retained.`,
    );
  }
  const recoveryOwner = {
    schemaVersion: 2,
    claimToken,
    pid: process.pid,
    startedAt: new Date().toISOString(),
    processIdentity,
    expectedExecutable: processIdentity.executable,
  };
  writeDurableFileSync(
    recoveryOwnerFile(runnerLockDirectory, claimToken),
    `${JSON.stringify(recoveryOwner, null, 2)}\n`,
  );
  return { ...claim, claimToken, recoveryOwner };
}

async function assertRecoveryOwnership(recovery) {
  const [claimOwner, recoveryOwner, currentOwner] = await Promise.all([
    readJsonFile(recovery.claimFile),
    readJsonFile(recoveryOwnerFile(runnerLockDirectory, recovery.claimToken)),
    readRunnerLockOwner(),
  ]);
  if (
    currentOwner ||
    !claimOwner ||
    claimOwner.token !== recovery.staleOwner.token ||
    Number(claimOwner.pid) !== Number(recovery.staleOwner.pid) ||
    !recoveryOwner ||
    recoveryOwner.claimToken !== recovery.claimToken ||
    Number(recoveryOwner.pid) !== process.pid ||
    !(await recoveryOwnerIsCurrent(recoveryOwner, recovery.claimToken))
  ) {
    throw new Error(
      `The workspace E2E stale recovery token is no longer authoritative; refusing destructive work.`,
    );
  }
}

async function recoverStaleRunnerLock({ observedOwner, existingClaim }) {
  const recovery = await claimStaleLockRecovery({
    observedOwner,
    existingClaim,
  });
  await assertRecoveryOwnership(recovery);
  await terminateRecordedChildProcesses(recovery.staleOwner.token);
  await assertRecoveryOwnership(recovery);
  await restoreDurableProjectSnapshot(recovery.staleOwner);
  await assertRecoveryOwnership(recovery);

  const recoveredLockDirectory = path.join(
    cacheRoot,
    `workspace-e2e.lock.recovered.${recovery.claimToken}`,
  );
  await rename(runnerLockDirectory, recoveredLockDirectory);
  fsyncDirectorySync(cacheRoot);
  const movedClaim = await readJsonFile(
    recoveryClaimFile(recoveredLockDirectory, recovery.claimToken),
  );
  if (movedClaim?.token !== recovery.staleOwner.token) {
    throw new Error(
      `The retired workspace E2E lock failed its post-rename ownership check.`,
    );
  }
  await rm(recoveredLockDirectory, {
    recursive: true,
    force: true,
    maxRetries: 3,
    retryDelay: 200,
  });
  fsyncDirectorySync(cacheRoot);
}

async function acquireRunnerLock() {
  await mkdir(cacheRoot, { recursive: true });
  const processIdentity = await readProcessIdentity(process.pid);
  if (!hasProcessIdentityShape(processIdentity)) {
    throw new Error(`Could not capture the workspace E2E runner OS identity.`);
  }

  for (let attempt = 0; attempt < 2; attempt += 1) {
    try {
      await mkdir(runnerLockDirectory);
      fsyncDirectorySync(cacheRoot);
      const token = randomUUID();
      const owner = {
        schemaVersion: 2,
        pid: process.pid,
        token,
        startedAt: new Date().toISOString(),
        processIdentity,
        expectedExecutable: processIdentity.executable,
      };
      try {
        writeDurableFileSync(
          runnerOwnerFile(runnerLockDirectory, token),
          `${JSON.stringify(owner, null, 2)}\n`,
        );
      } catch (ownerError) {
        await rm(runnerLockDirectory, { recursive: true, force: true }).catch(
          () => undefined,
        );
        throw ownerError;
      }
      runnerLockToken = token;
      return;
    } catch (error) {
      if (error?.code !== "EEXIST") throw error;

      const [owner, recoveryClaim] = await Promise.all([
        readRunnerLockOwner(),
        readRecoveryClaim(),
      ]);
      if (owner && recoveryClaim) {
        throw new Error(
          `The workspace E2E lock has both a live-owner file and a recovery claim; refusing takeover.`,
        );
      }
      if (owner && (await ownerProcessIsCurrent(owner))) {
        const locked = new Error(
          `Another workspace E2E run (pid=${owner.pid}) owns ${runnerLockDirectory}. Wait for it to finish before retrying.`,
        );
        locked.exitCode = 2;
        throw locked;
      }
      if (
        recoveryClaim &&
        (await recoveryOwnerIsCurrent(
          recoveryClaim.recoveryOwner,
          recoveryClaim.claimToken,
        ))
      ) {
        const recovering = new Error(
          `Another workspace E2E runner (pid=${recoveryClaim.recoveryOwner.pid}) is recovering the stale lock. Retry after it finishes.`,
        );
        recovering.exitCode = 2;
        throw recovering;
      }

      if (!owner && !recoveryClaim) {
        const lockStat = await stat(runnerLockDirectory);
        if (
          Date.now() - lockStat.mtimeMs <
          RUNNER_LOCK_INITIALIZATION_GRACE_MS
        ) {
          const initializing = new Error(
            `Another workspace E2E run is initializing ${runnerLockDirectory}. Wait for it to finish before retrying.`,
          );
          initializing.exitCode = 2;
          throw initializing;
        }
      }
      if (
        recoveryClaim &&
        !recoveryClaim.recoveryOwner &&
        Date.now() - recoveryClaim.claimStat.mtimeMs <
          RUNNER_LOCK_INITIALIZATION_GRACE_MS
      ) {
        const initializing = new Error(
          `Another workspace E2E runner is initializing stale-lock recovery. Retry shortly.`,
        );
        initializing.exitCode = 2;
        throw initializing;
      }

      if (attempt === 0) {
        if (!owner && !recoveryClaim) {
          throw new Error(
            `The stale workspace E2E lock has no readable owner or recovery claim; refusing automatic takeover of ${runnerLockDirectory}.`,
          );
        }
        await recoverStaleRunnerLock({
          observedOwner: owner,
          existingClaim: recoveryClaim,
        });
        continue;
      }
      throw new Error(
        `Could not acquire the workspace E2E runner lock at ${runnerLockDirectory}.`,
      );
    }
  }
}

async function releaseRunnerLock() {
  if (!runnerLockToken) return;
  const owner = await readRunnerLockOwner();
  if (
    !owner ||
    owner.token !== runnerLockToken ||
    !(await ownerProcessIsCurrent(owner))
  ) {
    throw new Error(
      `Refusing to release a workspace E2E runner lock owned by another process.`,
    );
  }
  await rm(runnerLockDirectory, {
    recursive: true,
    force: true,
    maxRetries: 3,
    retryDelay: 200,
  });
  fsyncDirectorySync(cacheRoot);
  runnerLockToken = undefined;
}

function safeArtifactName(name) {
  return name.replace(/[^a-z0-9._-]/gi, "-");
}

function cypressArtifactArguments(suiteName) {
  const suiteDirectory = path.join(
    artifactDirectory,
    "cypress",
    safeArtifactName(suiteName),
  );
  const screenshotsFolder = path
    .join(suiteDirectory, "screenshots")
    .split(path.sep)
    .join("/");
  const videosFolder = path
    .join(suiteDirectory, "videos")
    .split(path.sep)
    .join("/");
  return [
    "--config",
    `screenshotsFolder=${screenshotsFolder},videosFolder=${videosFolder},video=true,trashAssetsBeforeRuns=false`,
  ];
}

export function forwardChildOutputToLog(
  child,
  logFile,
  {
    name = "child",
    stdout = process.stdout,
    stderr = process.stderr,
    onFailure = () => undefined,
  } = {},
) {
  const destination = createWriteStream(logFile, { flags: "a" });
  let readySettled = false;
  let completionSettled = false;
  let resolveCompletion;
  const completion = new Promise((resolve) => {
    resolveCompletion = resolve;
  });
  const settleCompletion = () => {
    if (completionSettled) return;
    completionSettled = true;
    resolveCompletion();
  };
  let resolveReady;
  let rejectReady;
  const ready = new Promise((resolve, reject) => {
    resolveReady = resolve;
    rejectReady = reject;
  });
  destination.once("open", () => {
    try {
      if (!fstatSync(destination.fd).isFile()) {
        destination.destroy(
          new Error(`The ${name} artifact log target is not a regular file.`),
        );
        return;
      }
    } catch (error) {
      destination.destroy(error);
      return;
    }
    readySettled = true;
    resolveReady();
  });
  destination.once("close", settleCompletion);
  // This permanent listener converts late EACCES/ENOSPC/EIO failures into the
  // runner lifecycle rather than letting EventEmitter terminate the process.
  destination.on("error", (error) => {
    const wrapped = new Error(
      `Could not persist the ${name} artifact log at ${logFile}.`,
      { cause: error },
    );
    onFailure(wrapped);
    if (!readySettled) {
      readySettled = true;
      rejectReady(wrapped);
    }
    destination.destroy();
    settleCompletion();
  });
  const forward = (stream, output) => {
    stream.on("data", (chunk) => {
      try {
        if (!destination.destroyed) destination.write(chunk);
        output.write(chunk);
      } catch (error) {
        onFailure(
          new Error(`Could not forward output for the ${name} child.`, {
            cause: error,
          }),
        );
        destination.destroy();
      }
    });
  };
  forward(child.stdout, stdout);
  forward(child.stderr, stderr);
  const finish = () => {
    if (!destination.destroyed && !destination.writableEnded) destination.end();
  };
  child.once("close", finish);
  child.once("error", finish);
  return { completion, ready };
}

function recordChildLifecycleFailure(record, error) {
  if (record.lifecycleError) return;
  record.lifecycleError = error;
  pendingChildLifecycleFailures.add(record);
  record.resolveLifecycleFailure({ error, record });
}

function observeChildLifecycleFailure(record) {
  if (!record?.lifecycleError) return undefined;
  record.lifecycleErrorObserved = true;
  pendingChildLifecycleFailures.delete(record);
  return record.lifecycleError;
}

function pendingLifecycleFailurePromise() {
  const pending = pendingChildLifecycleFailures.values().next().value;
  if (pending) {
    return Promise.reject(observeChildLifecycleFailure(pending));
  }
  const records = [...activeChildRecords];
  if (records.length === 0) return new Promise(() => undefined);
  return Promise.race(
    records.map((record) => record.lifecycleFailurePromise),
  ).then(({ error, record }) => {
    observeChildLifecycleFailure(record);
    throw error;
  });
}

async function pipeChildOutput(record, child, name) {
  const forwarding = forwardChildOutputToLog(
    child,
    path.join(artifactDirectory, `${safeArtifactName(name)}.log`),
    {
      name,
      stderr: process.stderr,
      stdout: process.stdout,
      onFailure: (error) => recordChildLifecycleFailure(record, error),
    },
  );
  record.outputCompletionPromise = forwarding.completion;
  await forwarding.ready;
}

function recordChildSpawnIntent(record) {
  const {
    name,
    protocolVersion,
    requestedExecutable,
    terminationMode,
  } = record;
  if (!runnerLockToken) {
    throw new Error(
      `Cannot record the ${name} spawn intent before the workspace E2E runner lock is ready.`,
    );
  }
  const metadataFile = path.join(
    runnerLockDirectory,
    `child-${safeArtifactName(name)}-intent-${randomUUID()}.json`,
  );
  record.metadataFile = metadataFile;
  writeDurableFileSync(
    metadataFile,
    `${JSON.stringify(
      {
        schemaVersion: process.platform === "win32" ? 3 : 2,
        identityState: "spawn_intent",
        ownerToken: runnerLockToken,
        name,
        intentAt: new Date().toISOString(),
        protocolVersion,
        requestedExecutable,
        terminationMode,
      },
      null,
      2,
    )}\n`,
  );
  record.metadataRecorded = true;
}

function recordPendingChildProcess(record) {
  const {
    child,
    name,
    protocolVersion,
    requestedExecutable,
    terminationMode,
  } = record;
  if (!record.metadataFile || !runnerLockToken || !child?.pid) {
    throw new Error(
      `Cannot record ${name} before the workspace E2E runner lock is ready.`,
    );
  }
  writeDurableFileSync(
    record.metadataFile,
    `${JSON.stringify(
      {
        schemaVersion: process.platform === "win32" ? 3 : 2,
        identityState: "pending",
        ownerToken: runnerLockToken,
        name,
        pid: child.pid,
        processGroupId: process.platform === "win32" ? undefined : child.pid,
        spawnedAt: new Date().toISOString(),
        protocolVersion,
        requestedExecutable,
        terminationMode,
      },
      null,
      2,
    )}\n`,
    "w",
  );
}

function recordVerifiedChildProcess(record) {
  const {
    child,
    name,
    processIdentity,
    protocolVersion,
    requestedExecutable,
    terminationMode,
  } = record;
  if (
    !record.metadataFile ||
    !runnerLockToken ||
    !child?.pid ||
    !hasProcessIdentityShape(processIdentity)
  ) {
    throw new Error(`Cannot verify incomplete child metadata for ${name}.`);
  }
  const metadata = {
    schemaVersion: process.platform === "win32" ? 3 : 2,
    identityState: "verified",
    ownerToken: runnerLockToken,
    name,
    pid: child.pid,
    processGroupId: process.platform === "win32" ? undefined : child.pid,
    spawnedAt: new Date().toISOString(),
    processIdentity,
    expectedExecutable: processIdentity.executable,
    protocolVersion,
    requestedExecutable,
    terminationMode,
  };
  writeDurableFileSync(
    record.metadataFile,
    `${JSON.stringify(metadata, null, 2)}\n`,
    "w",
  );
  record.identityVerified = true;
}

async function removeTrackedChildMetadata(record) {
  if (record.metadataFile) {
    await rm(record.metadataFile, { force: true });
    fsyncDirectorySync(runnerLockDirectory);
    record.metadataFile = undefined;
  }
  activeChildRecords.delete(record);
}

async function tryFinalizeNaturallyExitedChild(record) {
  if (
    !record.closed ||
    !record.metadataRecorded ||
    record.finalizationPromise
  ) {
    return record.finalizationPromise;
  }
  record.finalizationPromise = (async () => {
    await record.outputCompletionPromise;
    if (await trackedTreeHasPotentialMembers(record)) return;
    await removeTrackedChildMetadata(record);
  })();
  try {
    await record.finalizationPromise;
  } catch (error) {
    record.lifecycleError = error;
    throw error;
  } finally {
    record.finalizationPromise = undefined;
  }
}

function delay(milliseconds) {
  return new Promise((resolve) => setTimeout(resolve, milliseconds));
}

function throwIfInterrupted() {
  const pendingFailure = pendingChildLifecycleFailures.values().next().value;
  if (pendingFailure) throw observeChildLifecycleFailure(pendingFailure);
  if (receivedSignal) {
    throw new Error(`Workspace E2E run interrupted by ${receivedSignal}.`);
  }
}

function createChildRegistrationBarrier(record) {
  record.registrationPromise = new Promise((resolve) => {
    record.resolveRegistration = resolve;
  });
}

function settleChildRegistration(record) {
  if (record.registrationSettled) return;
  record.registrationSettled = true;
  record.resolveRegistration();
}

export async function stopRegisteredChildRecords(records, stopRecord) {
  for (const record of [...records].reverse()) {
    await record.registrationPromise;
    await stopRecord(record);
  }
}

function terminateTrackedProcessTreeOnce(record) {
  if (!record.terminationPromise) {
    record.terminationPromise = terminateTrackedProcessTree(record);
  }
  return record.terminationPromise;
}

async function prepareTrackedSpawn(command, args, { cwd, env }) {
  if (process.platform !== "win32") {
    return {
      args,
      command,
      options: {
        cwd,
        detached: true,
        env,
        stdio: "pipe",
        windowsHide: true,
      },
      protocolVersion: 1,
      terminationMode: "posix_process_group_v1",
    };
  }
  const parentIdentity = await readProcessIdentity(process.pid);
  if (parentIdentity?.platform !== "win32") {
    throw new Error(
      `Could not capture the Windows runner identity before creating a supervised child.`,
    );
  }
  const applicationName = resolveWindowsExecutable(command, cwd, env);
  return createWindowsJobSupervisorSpawnSpec(applicationName, args, {
    cwd,
    env,
    parentIdentity,
  });
}

function reserveEphemeralPort() {
  return new Promise((resolve, reject) => {
    const server = createServer();
    server.once("error", reject);
    server.listen({ host: LOOPBACK, port: 0, exclusive: true }, () => {
      const address = server.address();
      if (!address || typeof address === "string") {
        server.close();
        reject(new Error("Could not allocate an isolated loopback port."));
        return;
      }
      server.close((error) => {
        if (error) reject(error);
        else resolve(address.port);
      });
    });
  });
}

async function spawnTrackedProcess(command, args, { cwd, env, name }) {
  // Keep the signal check in the same synchronous turn as intent journaling and
  // spawn. Once a signal handler sets receivedSignal, no later child may enter
  // the tree after stopActiveChildren() has taken its cleanup snapshot.
  throwIfInterrupted();
  const spawnSpec = await prepareTrackedSpawn(command, args, { cwd, env });
  throwIfInterrupted();
  const record = {
    child: undefined,
    closed: false,
    metadataRecorded: false,
    name,
    protocolVersion: spawnSpec.protocolVersion,
    requestedExecutable: command,
    terminationMode: spawnSpec.terminationMode,
  };
  createChildRegistrationBarrier(record);
  record.lifecycleFailurePromise = new Promise((resolve) => {
    record.resolveLifecycleFailure = resolve;
  });
  recordChildSpawnIntent(record);
  activeChildRecords.add(record);

  try {
    const child = spawn(spawnSpec.command, spawnSpec.args, spawnSpec.options);
    record.child = child;
    childRecordByProcess.set(child, record);
    child.once("close", () => {
      record.closed = true;
      void tryFinalizeNaturallyExitedChild(record).catch(() => undefined);
    });
    recordPendingChildProcess(record);
    await pipeChildOutput(record, child, name);
    record.processIdentity = await captureSpawnedProcessIdentity(child, name);
    recordVerifiedChildProcess(record);
    settleChildRegistration(record);
    throwIfInterrupted();
    if (record.closed) await tryFinalizeNaturallyExitedChild(record);
    return child;
  } catch (registrationError) {
    if (record.lifecycleError === registrationError) {
      observeChildLifecycleFailure(record);
    }
    const registrationErrors = [registrationError];
    if (record.child?.pid && !hasProcessIdentityShape(record.processIdentity)) {
      try {
        record.processIdentity = await captureSpawnedProcessIdentity(
          record.child,
          name,
        );
      } catch (identityError) {
        registrationErrors.push(identityError);
      }
    }
    settleChildRegistration(record);
    let terminationError;
    try {
      await terminateTrackedProcessTreeOnce(record);
    } catch (error) {
      terminationError = error;
    }
    if (!terminationError) {
      await removeTrackedChildMetadata(record).catch((error) => {
        terminationError = error;
      });
    }
    if (terminationError) {
      throw new AggregateError(
        [...registrationErrors, terminationError],
        `Could not durably record or prove termination of the ${name} child; the runner lock must be retained.`,
      );
    }
    throw new AggregateError(
      registrationErrors,
      `Could not durably record the ${name} child process; its complete process tree was stopped and verified.`,
    );
  }
}

async function spawnNode(script, args, env, name) {
  return spawnTrackedProcess(process.execPath, [script, ...args], {
    cwd: appDirectory,
    env,
    name,
  });
}

async function spawnCommand(command, args, { cwd, env, name }) {
  return spawnTrackedProcess(command, args, { cwd, env, name });
}

function waitForExit(child) {
  if (child.exitCode !== null || child.signalCode !== null) {
    return Promise.resolve({ code: child.exitCode, signal: child.signalCode });
  }
  return new Promise((resolve, reject) => {
    child.once("error", reject);
    child.once("exit", (code, signal) => resolve({ code, signal }));
  });
}

async function waitForExitWithTimeout(
  child,
  name,
  timeoutMs = CYPRESS_TIMEOUT_MS,
) {
  let timer;
  try {
    const result = await Promise.race([
      waitForExit(child),
      pendingLifecycleFailurePromise(),
      new Promise((_, reject) => {
        timer = setTimeout(
          () =>
            reject(
              new Error(
                `${name} did not finish within ${Math.round(timeoutMs / 1_000)} seconds.`,
              ),
            ),
          timeoutMs,
        );
      }),
    ]);
    const record = childRecordByProcess.get(child);
    await record?.outputCompletionPromise;
    const lifecycleError = observeChildLifecycleFailure(record);
    if (lifecycleError) throw lifecycleError;
    return result;
  } finally {
    clearTimeout(timer);
  }
}

async function waitForHttp(
  url,
  child,
  name,
  readinessPath,
  { timeoutMs = STARTUP_TIMEOUT_MS, requestTimeoutMs = 2_000 } = {},
) {
  const deadline = Date.now() + timeoutMs;
  const pollCancellation = new AbortController();
  const earlyExit = waitForExit(child).then(({ code, signal }) => {
    throw new Error(
      `${name} exited before becoming ready (code=${code}, signal=${signal}).`,
    );
  });

  const poll = async () => {
    while (!pollCancellation.signal.aborted && Date.now() < deadline) {
      const controller = new AbortController();
      const cancelRequest = () => controller.abort();
      pollCancellation.signal.addEventListener("abort", cancelRequest, {
        once: true,
      });
      const timeout = setTimeout(() => controller.abort(), requestTimeoutMs);
      try {
        const response = await fetch(`${url}${readinessPath}`, {
          redirect: "manual",
          signal: controller.signal,
        });
        if (!response.ok)
          throw new Error(`${name} readiness returned ${response.status}.`);
        await response.body?.cancel();
        return;
      } catch {
        if (!pollCancellation.signal.aborted) {
          await Promise.race([
            delay(POLL_INTERVAL_MS),
            new Promise((resolve) =>
              pollCancellation.signal.addEventListener("abort", resolve, {
                once: true,
              }),
            ),
          ]);
        }
      } finally {
        clearTimeout(timeout);
        pollCancellation.signal.removeEventListener("abort", cancelRequest);
      }
    }
    if (pollCancellation.signal.aborted) return;
    throw new Error(
      `${name} did not become ready within ${timeoutMs / 1_000} seconds.`,
    );
  };

  const pollPromise = poll();
  try {
    await Promise.race([
      pollPromise,
      earlyExit,
      pendingLifecycleFailurePromise(),
    ]);
  } finally {
    pollCancellation.abort();
    await pollPromise.catch(() => undefined);
  }
}

function isProcessGroupAlive(processGroupId) {
  if (
    !Number.isSafeInteger(processGroupId) ||
    processGroupId <= 0 ||
    process.platform === "win32"
  ) {
    return false;
  }
  try {
    process.kill(-processGroupId, 0);
    return true;
  } catch (error) {
    if (error?.code === "ESRCH") return false;
    if (error?.code === "EPERM") return true;
    throw error;
  }
}

async function trackedTreeHasPotentialMembers(record) {
  const pid = Number(record?.child?.pid ?? record?.pid);
  if (!Number.isSafeInteger(pid) || pid <= 0) return false;
  if (process.platform !== "win32") return isProcessGroupAlive(pid);
  if (
    record.terminationMode !== WINDOWS_JOB_SUPERVISOR_TERMINATION_MODE ||
    !record.child
  ) {
    throw new Error(
      `A Windows process tree without its retained Job Object supervisor handle is unverifiable; refusing cleanup.`,
    );
  }
  return (
    !record.closed &&
    record.child.exitCode === null &&
    record.child.signalCode === null
  );
}

async function terminatePosixProcessGroup(record) {
  const processGroupId = Number(record.processGroupId ?? record.pid);
  if (!isProcessGroupAlive(processGroupId)) return;
  const currentIdentity = await readProcessIdentity(Number(record.pid));
  if (
    !processIdentitiesMatch(record.processIdentity, currentIdentity) ||
    normalizeExecutablePath(record.expectedExecutable) !==
      normalizeExecutablePath(currentIdentity?.executable)
  ) {
    throw new Error(
      `Process group ${processGroupId} is still present but its leader birth identity or executable changed; refusing to signal a possibly unrelated tree.`,
    );
  }

  try {
    process.kill(-processGroupId, "SIGTERM");
  } catch (error) {
    if (error?.code !== "ESRCH") throw error;
  }
  let deadline = Date.now() + PROCESS_STOP_TIMEOUT_MS;
  while (isProcessGroupAlive(processGroupId) && Date.now() < deadline) {
    await delay(100);
  }
  if (isProcessGroupAlive(processGroupId)) {
    try {
      process.kill(-processGroupId, "SIGKILL");
    } catch (error) {
      if (error?.code !== "ESRCH") throw error;
    }
    deadline = Date.now() + PROCESS_STOP_TIMEOUT_MS;
    while (isProcessGroupAlive(processGroupId) && Date.now() < deadline) {
      await delay(100);
    }
  }
  if (isProcessGroupAlive(processGroupId)) {
    throw new Error(
      `Process group ${processGroupId} did not stop; refusing project restore or lock release.`,
    );
  }
}

export async function terminateRetainedWindowsJobSupervisor(
  record,
  timeoutMs = PROCESS_STOP_TIMEOUT_MS,
) {
  const child = record?.child;
  if (
    record?.terminationMode !== WINDOWS_JOB_SUPERVISOR_TERMINATION_MODE ||
    !child ||
    typeof child.kill !== "function"
  ) {
    throw new Error(
      `Windows cleanup requires the retained Job Object supervisor process handle.`,
    );
  }
  if (
    record.closed ||
    child.exitCode !== null ||
    child.signalCode !== null
  ) {
    return;
  }
  const exit = waitForExit(child);
  if (!child.kill()) {
    if (
      !record.closed &&
      child.exitCode === null &&
      child.signalCode === null
    ) {
      throw new Error(
        `The retained Windows Job Object supervisor handle could not be terminated.`,
      );
    }
  }
  const completed = await Promise.race([
    exit.then(() => true),
    delay(timeoutMs).then(() => false),
  ]);
  if (!completed) {
    throw new Error(
      `The retained Windows Job Object supervisor did not stop; refusing project restore or lock release.`,
    );
  }
}

async function terminateProcessRecord(record) {
  const pid = Number(record?.pid);
  if (
    !Number.isSafeInteger(pid) ||
    pid <= 0 ||
    pid === process.pid ||
    (process.platform !== "win32" && Number(record.processGroupId) !== pid) ||
    !hasProcessIdentityShape(record.processIdentity) ||
    normalizeExecutablePath(record.expectedExecutable) !==
      normalizeExecutablePath(record.processIdentity.executable)
  ) {
    throw new Error(
      `Invalid child process identity; refusing tree termination.`,
    );
  }
  if (process.platform === "win32") {
    throw new Error(
      `Recorded Windows PIDs are never signaled; cleanup requires a retained Job Object supervisor handle.`,
    );
  } else {
    await terminatePosixProcessGroup(record);
  }
}

async function terminateTrackedProcessTree(trackedRecord) {
  const child = trackedRecord?.child;
  if (!child?.pid) {
    if (child && child.exitCode === null && child.signalCode === null) {
      await waitForExit(child).catch(() => undefined);
    }
    return;
  }
  if (process.platform === "win32") {
    await terminateRetainedWindowsJobSupervisor(trackedRecord);
    return;
  }
  if (!(await trackedTreeHasPotentialMembers(trackedRecord))) return;
  await terminateProcessRecord({
    pid: child.pid,
    processGroupId: process.platform === "win32" ? undefined : child.pid,
    processIdentity: trackedRecord.processIdentity,
    expectedExecutable: trackedRecord.processIdentity?.executable,
  });
  await Promise.race([
    waitForExit(child).catch(() => undefined),
    delay(PROCESS_STOP_TIMEOUT_MS),
  ]);
  if (await trackedTreeHasPotentialMembers(trackedRecord)) {
    throw new Error(
      `Child process tree pid=${child.pid} still has members after termination.`,
    );
  }
}

async function terminateRecordedPidTree(metadata) {
  await terminateProcessRecord(metadata);
  if (await trackedTreeHasPotentialMembers(metadata)) {
    throw new Error(
      `Recorded child process tree pid=${metadata.pid} still has potential members after termination; refusing stale-lock recovery.`,
    );
  }
}

async function proveRecordedWindowsSupervisorExited(metadata) {
  const pid = Number(metadata?.pid);
  if (
    metadata.identityState !== "spawn_intent" &&
    (!Number.isSafeInteger(pid) || pid <= 0)
  ) {
    throw new Error(
      `Invalid Windows supervisor PID in the stale workspace E2E lock.`,
    );
  }
  const deadline = Date.now() + PROCESS_STOP_TIMEOUT_MS;
  while (true) {
    const currentIdentity =
      metadata.identityState === "spawn_intent"
        ? undefined
        : await readProcessIdentity(pid);
    const disposition = windowsSupervisorRecoveryDisposition(
      metadata,
      currentIdentity,
    );
    if (disposition.recoverable) return;
    if (
      disposition.reason === "spawn_intent_grace" ||
      Date.now() >= deadline
    ) {
      throw new Error(
        `The stale Windows Job Object supervisor is still live or unverifiable (${disposition.reason}); no PID was signaled and automatic recovery is refused.`,
      );
    }
    await delay(100);
  }
}

async function terminateRecordedChildProcesses(ownerToken) {
  const entries = await readdir(runnerLockDirectory, { withFileTypes: true });
  const metadataFiles = entries
    .filter(
      (entry) =>
        entry.isFile() &&
        entry.name.startsWith("child-") &&
        entry.name.endsWith(".json"),
    )
    .map((entry) => path.join(runnerLockDirectory, entry.name));
  for (const metadataFile of metadataFiles) {
    let metadata;
    try {
      metadata = await readJsonFile(metadataFile);
    } catch (error) {
      throw new Error(
        `Unreadable child metadata in the stale workspace E2E lock; refusing recovery.`,
        {
          cause: error,
        },
      );
    }
    if (metadata?.ownerToken !== ownerToken) {
      throw new Error(
        `Invalid child metadata in the stale workspace E2E lock; refusing recovery.`,
      );
    }
    if (process.platform === "win32") {
      await proveRecordedWindowsSupervisorExited(metadata);
      continue;
    }
    if (
      metadata.schemaVersion !== 2 ||
      metadata.identityState !== "verified" ||
      !Number.isSafeInteger(Number(metadata.pid)) ||
      !hasProcessIdentityShape(metadata.processIdentity) ||
      normalizeExecutablePath(metadata.expectedExecutable) !==
        normalizeExecutablePath(metadata.processIdentity.executable)
    ) {
      throw new Error(
        `Invalid child metadata in the stale workspace E2E lock; refusing recovery.`,
      );
    }
    await terminateRecordedPidTree(metadata);
  }
}

async function snapshotProjectFiles() {
  return Promise.all(
    nextGeneratedProjectFiles.map(async (file) => {
      const contents = await readFile(file);
      return {
        file,
        contents,
        gitHeadObject: gitHeadObjectForProjectFileContents(file, contents),
      };
    }),
  );
}

function concurrentProjectFileChangeError(file, detail) {
  const error = new Error(
    `Refusing to overwrite a concurrent or unrecognized change to ${file}. ${detail} ` +
      `The runner lock and recovery bytes will be retained.`,
  );
  error.code = "EWORKSPACE_CONCURRENT_CHANGE";
  return error;
}

async function readOptionalFile(file) {
  return readFile(file).catch((error) => {
    if (error?.code === "ENOENT") return undefined;
    throw error;
  });
}

export function gitHeadObjectForProjectFileContents(file, currentContents) {
  if (typeof file !== "string" || !Buffer.isBuffer(currentContents)) {
    return undefined;
  }
  const repository = spawnSync(
    "git",
    ["-C", path.dirname(file), "rev-parse", "--show-toplevel"],
    { encoding: "utf8", windowsHide: true },
  );
  if (repository.status !== 0) return undefined;

  const repositoryRoot = repository.stdout.trim();
  const relativePath = path
    .relative(repositoryRoot, path.resolve(file))
    .split(path.sep)
    .join("/");
  if (
    !repositoryRoot ||
    !relativePath ||
    relativePath.startsWith("../") ||
    path.isAbsolute(relativePath)
  ) {
    return undefined;
  }

  // Hash the bytes through the path's Git clean filter, then compare that
  // canonical object id with HEAD. This handles a repository's declared EOL
  // conversion while avoiding status text, external diff drivers, or a path
  // passed through a shell. A dirty/staged/untracked state has no matching
  // HEAD blob and remains fail-closed.
  const currentObject = spawnSync(
    "git",
    ["-C", repositoryRoot, "hash-object", `--path=${relativePath}`, "--stdin"],
    {
      encoding: "utf8",
      input: currentContents,
      maxBuffer: 8 * 1024 * 1024,
      windowsHide: true,
    },
  );
  const trackedObject = spawnSync(
    "git",
    ["-C", repositoryRoot, "rev-parse", `HEAD:${relativePath}`],
    { encoding: "utf8", windowsHide: true },
  );
  if (
    currentObject.status === 0 &&
    trackedObject.status === 0 &&
    currentObject.stdout.trim() === trackedObject.stdout.trim()
  ) {
    return trackedObject.stdout.trim();
  }
  return undefined;
}

function snapshotAndCurrentMatchSameGitHead(snapshot, currentContents) {
  if (typeof snapshot?.gitHeadObject !== "string") return false;
  return (
    gitHeadObjectForProjectFileContents(snapshot.file, currentContents) ===
    snapshot.gitHeadObject
  );
}

export async function restoreProjectFileSnapshot(
  snapshot,
  { lockDirectory, ownerToken, afterCompareBeforeSwap },
) {
  assertLockToken(ownerToken);
  if (
    !Buffer.isBuffer(snapshot?.contents) ||
    typeof snapshot?.file !== "string"
  ) {
    throw new Error(
      `Cannot restore an invalid workspace E2E project snapshot.`,
    );
  }
  const generatedContents = snapshot.runnerGeneratedContents;
  const recoveryFile = path.join(
    lockDirectory,
    `project-file.${createHash("sha256")
      .update(path.resolve(snapshot.file))
      .digest("hex")
      .slice(0, 20)}.${ownerToken}.restore`,
  );

  const recoveryContents = await readOptionalFile(recoveryFile);
  if (recoveryContents) {
    if (
      !Buffer.isBuffer(generatedContents) ||
      !recoveryContents.equals(generatedContents)
    ) {
      throw concurrentProjectFileChangeError(
        snapshot.file,
        `The durable restore candidate does not match the runner-generated snapshot.`,
      );
    }
    const currentAfterInterruptedRestore = await readOptionalFile(
      snapshot.file,
    );
    if (
      currentAfterInterruptedRestore?.equals(snapshot.contents) ||
      snapshotAndCurrentMatchSameGitHead(
        snapshot,
        currentAfterInterruptedRestore,
      )
    ) {
      await rm(recoveryFile);
      fsyncDirectorySync(lockDirectory);
      return;
    }
    if (currentAfterInterruptedRestore) {
      throw concurrentProjectFileChangeError(
        snapshot.file,
        `A new file appeared after an interrupted compare-and-swap restore.`,
      );
    }
    writeDurableFileSync(snapshot.file, snapshot.contents, "wx");
    const verified = await readFile(snapshot.file);
    if (!verified.equals(snapshot.contents)) {
      throw new Error(
        `Post-restore byte verification failed for ${snapshot.file}.`,
      );
    }
    await rm(recoveryFile);
    fsyncDirectorySync(lockDirectory);
    return;
  }

  const current = await readOptionalFile(snapshot.file);
  if (current?.equals(snapshot.contents)) return;
  if (snapshotAndCurrentMatchSameGitHead(snapshot, current)) return;
  if (!current) {
    throw concurrentProjectFileChangeError(
      snapshot.file,
      `The tracked file disappeared without a matching restore candidate.`,
    );
  }
  if (
    !Buffer.isBuffer(generatedContents) ||
    !current.equals(generatedContents)
  ) {
    throw concurrentProjectFileChangeError(
      snapshot.file,
      `Current bytes do not match the runner-generated snapshot.`,
    );
  }

  // Atomic rename is the compare step: after it succeeds, an editor save can
  // only create a new target. The exclusive write below never overwrites that
  // new file, and the retained candidate makes an interrupted restore replayable.
  await rename(snapshot.file, recoveryFile);
  fsyncDirectorySync(path.dirname(snapshot.file));
  fsyncDirectorySync(lockDirectory);
  const moved = await readFile(recoveryFile);
  if (!moved.equals(generatedContents)) {
    throw concurrentProjectFileChangeError(
      snapshot.file,
      `The atomically moved bytes failed their generated-file comparison.`,
    );
  }
  await afterCompareBeforeSwap?.();
  try {
    writeDurableFileSync(snapshot.file, snapshot.contents, "wx");
  } catch (error) {
    if (error?.code === "EEXIST") {
      throw concurrentProjectFileChangeError(
        snapshot.file,
        `A user or editor saved the file during restoration.`,
      );
    }
    throw error;
  }
  const verified = await readFile(snapshot.file);
  if (!verified.equals(snapshot.contents)) {
    throw new Error(
      `Post-restore byte verification failed for ${snapshot.file}.`,
    );
  }
  await rm(recoveryFile);
  fsyncDirectorySync(lockDirectory);
}

async function restoreProjectFiles(
  snapshots = projectFileSnapshots,
  { lockDirectory = runnerLockDirectory, ownerToken = runnerLockToken } = {},
) {
  if (!snapshots) return;
  for (const snapshot of snapshots) {
    let restored = false;
    let lastError;
    for (let attempt = 0; attempt < 5; attempt += 1) {
      try {
        await restoreProjectFileSnapshot(snapshot, {
          lockDirectory,
          ownerToken,
        });
        restored = true;
        break;
      } catch (error) {
        lastError = error;
        if (error?.code === "EWORKSPACE_CONCURRENT_CHANGE") break;
        if (attempt < 4) await delay(100 * 2 ** attempt);
      }
    }
    if (!restored) {
      throw new Error(
        `Could not restore and verify ${snapshot.file}; the runner lock will be retained.`,
        {
          cause: lastError,
        },
      );
    }
  }
}

async function prepareCurrentProjectFilesForRestore() {
  if (
    !projectFileSnapshots ||
    projectFileSnapshots.every((snapshot) =>
      Buffer.isBuffer(snapshot.runnerGeneratedContents),
    )
  ) {
    return;
  }
  const owner = { token: runnerLockToken, pid: process.pid };
  const rawGeneratedSnapshot = await readJsonFile(
    runnerLockGeneratedSnapshotFile,
  );
  if (rawGeneratedSnapshot) {
    const validatedPlan = validateDurableRunnerGeneratedSnapshot(
      rawGeneratedSnapshot,
      projectFileSnapshots,
      owner,
    );
    if (validatedPlan.state === "planned") {
      const generatedFiles = await classifyRunnerGeneratedProjectFiles(
        projectFileSnapshots,
        validatedPlan.relativeRunDirectory,
        validatedPlan.candidatesByPath,
      );
      await persistRunnerGeneratedProjectFiles(
        generatedFiles,
        validatedPlan.relativeRunDirectory,
        runnerLockToken,
        validatedPlan,
      );
      attachRunnerGeneratedContents(generatedFiles);
    }
    return;
  }
  if (!plannedRelativeRunDirectory) {
    if (await currentSnapshotsMatchOriginal(projectFileSnapshots)) return;
    throw new Error(
      `Workspace E2E project files changed before a durable run plan was recorded.`,
    );
  }
  await captureAndPersistRunnerGeneratedProjectFiles(
    projectFileSnapshots,
    plannedRelativeRunDirectory,
    runnerLockToken,
  );
}

async function writeFailureDiagnostics(error) {
  await writeFile(
    path.join(artifactDirectory, "failure-diagnostics.json"),
    `${JSON.stringify(
      {
        timestamp: new Date().toISOString(),
        error: error?.stack || error?.message || String(error),
        platform: process.platform,
        node: process.version,
        runDirectory,
        artifactDirectory,
        timeouts: {
          startupMs: STARTUP_TIMEOUT_MS,
          routeWarmupMs: ROUTE_WARMUP_TIMEOUT_MS,
          cypressMs: CYPRESS_TIMEOUT_MS,
          processStopMs: PROCESS_STOP_TIMEOUT_MS,
        },
        processes: {
          next: nextProcess?.pid,
          liveBackend: liveBackendProcess?.pid,
          cypress: cypressProcess?.pid,
        },
      },
      null,
      2,
    )}\n`,
  );
}

async function writeRecoveryRequired(errors) {
  if (!runnerLockToken) return;
  await writeFile(
    path.join(runnerLockDirectory, "RECOVERY_REQUIRED.json"),
    `${JSON.stringify(
      {
        timestamp: new Date().toISOString(),
        message:
          "Automatic cleanup could not prove that child processes stopped and tracked project files were restored. " +
          "The lock and durable snapshot were retained; restore safely before removing the lock.",
        errors: errors.map((error) => error?.message || String(error)),
      },
      null,
      2,
    )}\n`,
  );
}

function validateRunDirectory(target) {
  const relative = path.relative(cacheRoot, target);
  const targetName = path.basename(target);
  if (
    !relative ||
    relative.startsWith("..") ||
    path.isAbsolute(relative) ||
    !targetName.startsWith("run-")
  ) {
    throw new Error(`Refusing to remove unexpected E2E cache path: ${target}`);
  }
}

async function stopActiveChildren() {
  if (stopChildrenPromise) return stopChildrenPromise;
  stopChildrenPromise = (async () => {
    const errors = [];
    const lifecycleErrors = [];
    await stopRegisteredChildRecords(activeChildRecords, async (record) => {
      try {
        await record.finalizationPromise?.catch(() => undefined);
        await terminateTrackedProcessTreeOnce(record);
        await record.outputCompletionPromise;
        await removeTrackedChildMetadata(record);
        const lifecycleError = observeChildLifecycleFailure(record);
        if (lifecycleError) lifecycleErrors.push(lifecycleError);
      } catch (error) {
        errors.push(
          record.lifecycleError && record.lifecycleError !== error
            ? new AggregateError(
                [record.lifecycleError, error],
                `Child lifecycle and cleanup both failed for ${record.name}.`,
              )
            : error,
        );
      }
    });
    if (errors.length > 0) {
      throw new AggregateError(
        errors,
        "Workspace E2E child process cleanup did not complete cleanly.",
      );
    }
    for (const record of [...pendingChildLifecycleFailures]) {
      const lifecycleError = observeChildLifecycleFailure(record);
      if (lifecycleError) lifecycleErrors.push(lifecycleError);
    }
    return lifecycleErrors;
  })();
  return stopChildrenPromise;
}

async function cleanup() {
  if (cleanupPromise) return cleanupPromise;
  cleanupPromise = (async () => {
    const safetyErrors = [];
    let lifecycleErrors = [];
    try {
      lifecycleErrors = await stopActiveChildren();
    } catch (error) {
      safetyErrors.push(error);
    }
    if (safetyErrors.length === 0) {
      try {
        await prepareCurrentProjectFilesForRestore();
        await restoreProjectFiles();
      } catch (error) {
        safetyErrors.push(error);
      }
    }
    if (safetyErrors.length > 0) {
      await writeRecoveryRequired(safetyErrors).catch((error) =>
        safetyErrors.push(error),
      );
      throw new AggregateError(
        safetyErrors,
        "Workspace E2E cleanup could not prove a safe restore; the durable runner lock was retained.",
      );
    }

    const nonSafetyErrors = [...lifecycleErrors];
    try {
      if (runDirectory) {
        validateRunDirectory(runDirectory);
        await rm(runDirectory, {
          recursive: true,
          force: true,
          maxRetries: 3,
          retryDelay: 200,
        });
      }
    } catch (error) {
      nonSafetyErrors.push(error);
    }
    try {
      await releaseRunnerLock();
    } catch (error) {
      nonSafetyErrors.push(error);
    }
    if (nonSafetyErrors.length > 0) {
      throw new AggregateError(
        nonSafetyErrors,
        "Workspace E2E cache or lock cleanup did not complete cleanly.",
      );
    }
    console.log(
      "[workspace-e2e] Child processes stopped, project files restored, and runner lock released.",
    );
  })();
  return cleanupPromise;
}

function installSignalHandlers() {
  for (const signal of ["SIGINT", "SIGTERM"]) {
    process.once(signal, () => {
      receivedSignal = signal;
      console.error(
        `\n[workspace-e2e] Received ${signal}; stopping child processes and completing durable cleanup.`,
      );
      void Promise.resolve(runnerSetupPromise)
        .catch(() => undefined)
        .then(() => cleanup())
        .catch((error) =>
          console.error(
            `[workspace-e2e] ${error.stack || error.message || error}`,
          ),
        );
      process.exitCode = signal === "SIGINT" ? 130 : 143;
    });
  }
}

async function main() {
  await mkdir(artifactDirectory, { recursive: true });
  throwIfInterrupted();
  try {
    runnerSetupPromise = (async () => {
      await acquireRunnerLock();
      projectFileSnapshots = await snapshotProjectFiles();
      await persistProjectFileSnapshots(projectFileSnapshots);
    })();
    await runnerSetupPromise;
    throwIfInterrupted();
    const port = await reserveEphemeralPort();
    const liveBackendPort = await reserveEphemeralPort();
    const baseUrl = `http://${LOOPBACK}:${port}`;
    runDirectory = path.join(cacheRoot, `run-${process.pid}-${port}`);
    const relativeRunDirectory = path
      .relative(appDirectory, runDirectory)
      .split(path.sep)
      .join("/");
    plannedRelativeRunDirectory = relativeRunDirectory;
    await persistProjectFileRunPlan(relativeRunDirectory);
    await persistPlannedRunnerGeneratedProjectFiles(
      projectFileSnapshots,
      relativeRunDirectory,
    );
    // Next dev rewrites these generated TypeScript entry points for a custom
    // distDir. Restore their exact pre-run bytes after the disposable app stops.
    const childEnvironment = {
      ...process.env,
      NEXT_TELEMETRY_DISABLED: "1",
      NEXT_PUBLIC_CHAT_FIRST_WORKBENCH_ENABLED: "true",
      DISABLE_AUTH: "true",
      CAN_CHANGE_KEYS: "false",
      DISABLE_ANONYMOUS_TRACKING: "true",
      FAST_API_INTERNAL_URL: "http://127.0.0.1:1",
      NEXT_PUBLIC_FAST_API: "",
      PRESENTON_TEST_NEXT_DIST_DIR: relativeRunDirectory,
    };

    console.log(`[workspace-e2e] Starting an isolated Next app at ${baseUrl}.`);
    console.log(
      "[workspace-e2e] Canonical route suite uses Cypress HTTP fixtures; live suite starts FastAPI+SQLite separately.",
    );

    await writeFile(
      path.join(artifactDirectory, "run-metadata.json"),
      `${JSON.stringify({ timestamp: new Date().toISOString(), platform: process.platform, node: process.version, port, liveBackendPort }, null, 2)}\n`,
    );
    throwIfInterrupted();
    nextProcess = await spawnNode(
      nextCli,
      ["dev", "--hostname", LOOPBACK, "--port", String(port)],
      childEnvironment,
      "next-server",
    );
    await waitForHttp(baseUrl, nextProcess, "Next", "/favicon.ico");
    await waitForHttp(
      baseUrl,
      nextProcess,
      "Next presentation route",
      "/presentation?id=route-workspace-deck",
      {
        timeoutMs: ROUTE_WARMUP_TIMEOUT_MS,
        // Do not abort the request that is doing the cold route compilation.
        // An abort/retry loop makes this slower and leaves Cypress racing the
        // compiler instead of receiving a fully rendered route.
        requestTimeoutMs: ROUTE_WARMUP_TIMEOUT_MS,
      },
    );
    await captureAndPersistRunnerGeneratedProjectFiles(
      projectFileSnapshots,
      relativeRunDirectory,
    );
    throwIfInterrupted();
    console.log(
      "[workspace-e2e] Next presentation route is warm; running the canonical route spec.",
    );

    cypressProcess = await spawnNode(
      cypressCli,
      [
        "run",
        "--e2e",
        "--spec",
        "cypress/e2e/canonicalConversationRoute.cy.ts",
        ...cypressArtifactArguments("route"),
      ],
      { ...childEnvironment, CYPRESS_BASE_URL: baseUrl },
      "cypress-route",
    );
    const result = await waitForExitWithTimeout(
      cypressProcess,
      "Cypress route suite",
    );
    if (result.code !== 0) {
      const error = new Error(
        `Cypress exited with code ${result.code} (signal=${result.signal}).`,
      );
      error.exitCode = result.code || 1;
      throw error;
    }

    throwIfInterrupted();
    const liveBackendUrl = `http://${LOOPBACK}:${liveBackendPort}`;
    console.log(
      `[workspace-e2e] Starting the live FastAPI+SQLite runner harness at ${liveBackendUrl}.`,
    );
    liveBackendProcess = await spawnCommand(
      process.platform === "win32" ? "uv.exe" : "uv",
      [
        "run",
        "python",
        "-m",
        "tests.support.workspace_browser_live_harness",
        "--host",
        LOOPBACK,
        "--port",
        String(liveBackendPort),
      ],
      {
        cwd: fastApiDirectory,
        env: { ...childEnvironment, PYTHONUNBUFFERED: "1" },
        name: "fastapi-live-harness",
      },
    );
    await waitForHttp(
      liveBackendUrl,
      liveBackendProcess,
      "FastAPI live harness",
      "/__test__/health",
    );
    throwIfInterrupted();
    console.log(
      "[workspace-e2e] Live harness is ready; running the component-to-backend suite.",
    );

    cypressProcess = await spawnNode(
      cypressCli,
      [
        "run",
        "--component",
        "--spec",
        "cypress/component/CanonicalWorkspaceChatPanelLiveBackend.cy.tsx",
        "--env",
        `WORKSPACE_LIVE_BACKEND=${liveBackendUrl}`,
        ...cypressArtifactArguments("live-backend"),
      ],
      childEnvironment,
      "cypress-live-backend",
    );
    const liveResult = await waitForExitWithTimeout(
      cypressProcess,
      "Cypress live-backend suite",
    );
    if (liveResult.code !== 0) {
      const error = new Error(
        `Live Cypress suite exited with code ${liveResult.code} (signal=${liveResult.signal}).`,
      );
      error.exitCode = liveResult.code || 1;
      throw error;
    }
  } catch (error) {
    await writeFailureDiagnostics(error).catch((diagnosticError) => {
      console.error(
        `[workspace-e2e] Could not write failure diagnostics: ${diagnosticError.message}`,
      );
    });
    throw error;
  } finally {
    if (
      runnerLockToken ||
      nextProcess ||
      liveBackendProcess ||
      cypressProcess ||
      activeChildRecords.size > 0
    ) {
      await cleanup();
    }
  }
}

const isDirectRun =
  process.argv[1] &&
  path.resolve(process.argv[1]) === fileURLToPath(import.meta.url);

if (isDirectRun) {
  installSignalHandlers();
  void main().catch((error) => {
    console.error(`[workspace-e2e] ${error.stack || error.message || error}`);
    if (!receivedSignal) {
      process.exitCode = Number.isInteger(error.exitCode) ? error.exitCode : 1;
    }
  });
}
