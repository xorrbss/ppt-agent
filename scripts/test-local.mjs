#!/usr/bin/env node

import { existsSync, mkdirSync, readdirSync, rmSync } from "node:fs";
import { delimiter, dirname, join, relative, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { homedir, platform, tmpdir } from "node:os";
import { spawnSync } from "node:child_process";

const root = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const isWindows = platform() === "win32";
const npm = isWindows ? "npm.cmd" : "npm";
const npx = isWindows ? "npx.cmd" : "npx";
const uv = isWindows ? "uv.exe" : "uv";
const argv = new Set(process.argv.slice(2));
const knownArguments = new Set([
  "--all",
  "--dry-run",
  "--help",
  "--with-cypress",
  "--with-electron",
  "--with-fastapi-binary",
  "--with-fidelity",
  "--with-postgres",
]);

const unknownArguments = [...argv].filter((argument) => !knownArguments.has(argument));
if (unknownArguments.length > 0) {
  console.error(`Unknown argument(s): ${unknownArguments.join(", ")}`);
  process.exit(2);
}

if (argv.has("--help")) {
  console.log(`Usage: npm run test:local -- [options]

The default profile runs the portable CI contracts, locked FastAPI suite,
Next.js Node/lint/build gates, and the isolated chat-first browser E2E suite.
Capability-specific gates are never silently
treated as passed.

Options:
  --dry-run              Print commands, preconditions, and NOT RUN gates only
  --with-fastapi-binary  Build the PyInstaller binary used by Ubuntu CI
  --with-cypress         Run the gated Cypress component specs
  --with-fidelity        Require visual tools and run host-OS export fidelity
  --with-postgres        Require PPT_AGENT_POSTGRES_TEST_URL and run live DB tests
  --with-electron        Require Windows and run the Windows release gate
  --all                  Request every capability gate (fails if one is unavailable)
  --help                 Show this help
`);
  process.exit(0);
}

const dryRun = argv.has("--dry-run");
const requestAll = argv.has("--all");
const requested = (flag) => requestAll || argv.has(flag);
const runtimeRoot = join(tmpdir(), "ppt-agent-ci-local", `run-${process.pid}-${Date.now()}`);
const appDataDirectory = join(runtimeRoot, "app-data");
const tempDirectory = join(runtimeRoot, "temp");
const pytestBaseDirectory = join(runtimeRoot, "pytest");
const artifactDirectory = join(runtimeRoot, "artifacts", `template-v2-${platform()}`);
const sqlitePath = join(runtimeRoot, "test.db").replaceAll("\\", "/");
const fastapiDirectory = join(root, "servers", "fastapi");
const nextDirectory = join(root, "servers", "nextjs");
const electronDirectory = join(root, "electron");
const results = [];

function cleanupLocalRuntime() {
  const failures = results.filter((result) => result.status === "FAIL");
  if (failures.length > 0) {
    console.error(`[diagnostics] Preserving failed local run artifacts at ${runtimeRoot}`);
    return;
  }
  const localRoot = resolve(tmpdir(), "ppt-agent-ci-local");
  const runtimeRelative = relative(localRoot, runtimeRoot);
  if (!runtimeRelative || runtimeRelative.startsWith("..") || resolve(localRoot, runtimeRelative) !== resolve(runtimeRoot)) {
    console.error(`[cleanup] Refusing to remove unexpected local test path: ${runtimeRoot}`);
    return;
  }
  try {
    rmSync(runtimeRoot, {
      recursive: true,
      force: true,
      maxRetries: isWindows ? 12 : 3,
      retryDelay: isWindows ? 250 : 100,
    });
  } catch (error) {
    const code = error?.code || "unknown";
    console.error(
      `[cleanup] Could not remove isolated local test path ${runtimeRoot} (${code}): ${error.message}`,
    );
    if (isWindows && (code === "EPERM" || code === "EACCES" || code === "EBUSY")) {
      console.error(
        "[cleanup] A child process or scanner still owns a handle. The isolated path was preserved; " +
        "close the reported process and remove only that run directory.",
      );
    }
  }
}

if (!dryRun) process.once("exit", cleanupLocalRuntime);

function executableCandidates(name) {
  if (!isWindows) return [name];
  const extensions = (process.env.PATHEXT || ".EXE;.CMD;.BAT")
    .split(";")
    .filter(Boolean);
  return extensions.map((extension) => `${name}${extension.toLowerCase()}`);
}

function findOnPath(name) {
  if (name.includes("/") || name.includes("\\")) {
    return existsSync(name) ? name : null;
  }
  for (const directory of (process.env.PATH || "").split(delimiter)) {
    if (!directory) continue;
    for (const candidate of executableCandidates(name)) {
      const fullPath = join(directory, candidate);
      if (existsSync(fullPath)) return fullPath;
    }
  }
  return null;
}

function findPlaywrightHeadlessShell() {
  const roots = [];
  const configuredRoot = process.env.PLAYWRIGHT_BROWSERS_PATH?.trim();
  if (configuredRoot && configuredRoot !== "0") roots.push(configuredRoot);
  if (process.env.LOCALAPPDATA) {
    roots.push(join(process.env.LOCALAPPDATA, "ms-playwright"));
  }
  roots.push(
    join(homedir(), ".cache", "ms-playwright"),
    join(homedir(), "Library", "Caches", "ms-playwright"),
  );

  const revisions = [];
  for (const root of new Set(roots.map((candidate) => resolve(candidate)))) {
    let entries;
    try {
      entries = readdirSync(root, { withFileTypes: true });
    } catch {
      continue;
    }
    for (const entry of entries) {
      const match = /^chromium_headless_shell-(\d+)$/.exec(entry.name);
      if (!entry.isDirectory() || !match) continue;
      revisions.push({
        directory: join(root, entry.name),
        revision: Number(match[1]),
      });
    }
  }

  revisions.sort(
    (left, right) =>
      right.revision - left.revision ||
      left.directory.localeCompare(right.directory),
  );
  for (const { directory } of revisions) {
    let platformDirectories;
    try {
      platformDirectories = readdirSync(directory, { withFileTypes: true });
    } catch {
      continue;
    }
    for (const platformDirectory of platformDirectories) {
      if (!platformDirectory.isDirectory()) continue;
      for (const executableName of [
        "chrome-headless-shell.exe",
        "chrome-headless-shell",
      ]) {
        const candidate = join(
          directory,
          platformDirectory.name,
          executableName,
        );
        if (existsSync(candidate)) return candidate;
      }
    }
  }
  return null;
}

function findBrowser() {
  const configured = process.env.PUPPETEER_EXECUTABLE_PATH;
  if (configured && existsSync(configured)) return configured;

  const headlessShell = findPlaywrightHeadlessShell();
  if (headlessShell) return headlessShell;

  const pathNames = isWindows
    ? ["chrome", "msedge", "chromium"]
    : ["chromium", "chromium-browser", "google-chrome", "google-chrome-stable"];
  for (const name of pathNames) {
    const candidate = findOnPath(name);
    if (candidate) return candidate;
  }

  if (isWindows) {
    const roots = [process.env.PROGRAMFILES, process.env["PROGRAMFILES(X86)"], process.env.LOCALAPPDATA]
      .filter(Boolean);
    const relativePaths = [
      join("Google", "Chrome", "Application", "chrome.exe"),
      join("Microsoft", "Edge", "Application", "msedge.exe"),
    ];
    for (const base of roots) {
      for (const relativePath of relativePaths) {
        const candidate = join(base, relativePath);
        if (existsSync(candidate)) return candidate;
      }
    }
  }
  return null;
}

function findVisualTool(envName, names) {
  const configured = process.env[envName];
  if (configured && existsSync(configured)) return configured;
  for (const name of names) {
    const candidate = findOnPath(name);
    if (candidate) return candidate;
  }
  if (isWindows && envName === "SOFFICE_PATH") {
    const candidate = join(process.env.PROGRAMFILES || "", "LibreOffice", "program", "soffice.exe");
    if (existsSync(candidate)) return candidate;
  }
  return null;
}

const browserPath = dryRun ? null : findBrowser();
const sofficePath = dryRun ? null : findVisualTool("SOFFICE_PATH", ["soffice", "libreoffice"]);
const pdfToCairoPath = dryRun ? null : findVisualTool("PDFTOCAIRO_PATH", ["pdftocairo"]);

const commonEnv = {
  CI: "true",
  APP_DATA_DIRECTORY: appDataDirectory,
  TEMP_DIRECTORY: tempDirectory,
  TEMP: tempDirectory,
  TMP: tempDirectory,
  TMPDIR: tempDirectory,
  PYTEST_DEBUG_TEMPROOT: pytestBaseDirectory,
  USER_CONFIG_PATH: join(appDataDirectory, "userConfig.json"),
  DATABASE_URL: `sqlite+aiosqlite:///${sqlitePath}`,
  DISABLE_ANONYMOUS_TRACKING: "true",
  DISABLE_IMAGE_GENERATION: "true",
  ...(browserPath ? { PUPPETEER_EXECUTABLE_PATH: browserPath } : {}),
};

function formatCommand(command, args) {
  const quote = (value) => (/[\s"'()]/u.test(value) ? JSON.stringify(value) : value);
  return [command, ...args].map(quote).join(" ");
}

function addResult(status, name, detail) {
  results.push({ status, name, detail });
  console.log(`[${status}] ${name}${detail ? ` — ${detail}` : ""}`);
}

function runStep({
  name,
  command,
  args = [],
  cwd = root,
  env = {},
  selected = true,
  notRunReason,
  precondition,
  preconditionDescription,
  displayCommand,
}) {
  if (!selected) {
    addResult("NOT RUN", name, notRunReason);
    return;
  }

  const shownCommand = displayCommand || formatCommand(command, args);
  if (dryRun) {
    addResult(
      "PLAN",
      name,
      `${shownCommand} (cwd: ${cwd}; requires: ${preconditionDescription || "selected core profile"})`,
    );
    return;
  }

  const failedPrecondition = precondition?.();
  if (failedPrecondition) {
    addResult("FAIL", name, `precondition: ${failedPrecondition}`);
    return;
  }

  console.log(`\n>>> ${name}`);
  console.log(`$ ${shownCommand}`);
  const outcome = spawnSync(command, args, {
    cwd,
    env: { ...process.env, ...commonEnv, ...env },
    stdio: "inherit",
    windowsHide: true,
    // Node does not launch .cmd/.bat shims directly on every supported
    // Windows release. All commands and arguments reaching this branch are
    // fixed by this file rather than supplied by the caller.
    shell: isWindows && /\.(?:cmd|bat)$/iu.test(command),
  });
  if (outcome.error) {
    addResult("FAIL", name, outcome.error.message);
  } else if (outcome.status !== 0) {
    addResult("FAIL", name, `exit ${outcome.status ?? "unknown"}`);
  } else {
    addResult("PASS", name, "exit 0");
  }
}

if (!dryRun) {
  mkdirSync(appDataDirectory, { recursive: true });
  mkdirSync(tempDirectory, { recursive: true });
  mkdirSync(pytestBaseDirectory, { recursive: true });
  mkdirSync(artifactDirectory, { recursive: true });
}

console.log(`Local CI parity runner`);
console.log(`root=${root}`);
console.log(`host=${platform()} node=${process.versions.node} profile=${requestAll ? "all" : "core"}`);
console.log(
  "PASS means the command ran successfully; FAIL means a selected gate failed; " +
    "NOT RUN means an unrequested capability gate and is never counted as parity.",
);

const nodeMajor = Number(process.versions.node.split(".")[0]);
const node22Precondition = () =>
  nodeMajor === 22 ? null : `Node 22 required; found ${process.versions.node}`;
const withNode22 = (precondition) => () => node22Precondition() || precondition?.() || null;
runStep({
  name: "Root locked npm install",
  command: npm,
  args: ["ci"],
  precondition: node22Precondition,
  preconditionDescription: "Node 22 and npm",
});
runStep({
  name: "presentation-export v0.4.3 synchronization contract",
  command: npm,
  args: ["run", "test:presentation-export-sync"],
  precondition: node22Precondition,
  preconditionDescription: "Node 22 and root npm ci",
});
runStep({
  name: "Synchronize pinned presentation-export runtime",
  command: npm,
  args: ["run", "sync:presentation-export"],
  precondition: node22Precondition,
  preconditionDescription: "network access only when the pinned v0.4.3 runtime is absent or stale",
});
runStep({
  name: "Verify pinned presentation-export runtime",
  command: npm,
  args: ["run", "check:presentation-export"],
  precondition: node22Precondition,
  preconditionDescription: "Node 22 and synchronized presentation-export runtime",
});
runStep({
  name: "Static upstream compatibility verifier",
  command: process.execPath,
  args: ["--test", "scripts/verify-upstream-compatibility.test.mjs"],
  precondition: node22Precondition,
  preconditionDescription: "Node 22",
});
runStep({
  name: "Offline upstream intake fixture",
  command: process.execPath,
  args: ["--test", "scripts/intake-upstream-main.test.mjs"],
  precondition: node22Precondition,
  preconditionDescription: "Node 22",
});
runStep({
  name: "FastAPI locked dependency install",
  command: uv,
  args: ["sync", "--frozen"],
  cwd: fastapiDirectory,
  preconditionDescription: "uv and Python 3.11",
});
runStep({
  name: "Full FastAPI SQLite suite",
  command: uv,
  args: [
    "run",
    "pytest",
    "-q",
    "--no-header",
    "--basetemp",
    join(pytestBaseDirectory, "sqlite"),
  ],
  cwd: fastapiDirectory,
  precondition: () => browserPath ? null : "Chrome/Chromium is required because CI=true forbids Chrome smoke skips",
  preconditionDescription: "locked dependencies and Chrome/Chromium; feature flags remain default OFF",
});
runStep({
  name: "Next.js locked dependency install",
  command: npm,
  args: ["ci"],
  cwd: nextDirectory,
  precondition: node22Precondition,
  preconditionDescription: "Node 22 and npm",
});
runStep({
  name: "Shared Next.js CI Node suite",
  command: npm,
  args: ["run", "test:next-ci"],
  precondition: withNode22(() => browserPath ? null : "Chrome/Chromium is required because CI=true forbids Chrome smoke skips"),
  preconditionDescription: "Node 22, Next.js npm ci, and Chrome/Chromium",
});
runStep({
  name: "Next.js lint",
  command: npm,
  args: ["run", "lint"],
  cwd: nextDirectory,
  precondition: node22Precondition,
  preconditionDescription: "Node 22 and Next.js npm ci",
});
runStep({
  name: "Next.js production build",
  command: npm,
  args: ["run", "build"],
  cwd: nextDirectory,
  precondition: node22Precondition,
  env: {
    NEXT_PUBLIC_FAST_API: "http://localhost:8000",
    NEXT_PUBLIC_URL: "http://localhost:3000",
  },
});

const workspaceE2eCommand = platform() === "linux" ? "xvfb-run" : npm;
const workspaceE2eArgs = workspaceE2eCommand === "xvfb-run"
  ? ["-a", npm, "run", "test:workspace-e2e"]
  : ["run", "test:workspace-e2e"];
runStep({
  name: "Workspace chat-first browser + FastAPI SQLite E2E",
  command: workspaceE2eCommand,
  args: workspaceE2eArgs,
  cwd: nextDirectory,
  precondition: withNode22(() => {
    if (platform() === "linux" && !findOnPath("xvfb-run")) return "xvfb-run is required on Linux";
    if (!findOnPath("uv")) return "uv is required for the isolated FastAPI+SQLite harness";
    return null;
  }),
  preconditionDescription: "Node 22, Next.js npm ci, Cypress browser, uv, and xvfb-run on Linux",
  env: {
    WORKSPACE_E2E_ARTIFACT_DIR: join(artifactDirectory, "workspace-e2e"),
  },
});

const fastapiBinarySelected = requested("--with-fastapi-binary");
runStep({
  name: "FastAPI PyInstaller binary",
  command: uv,
  args: ["run", "--with", "pyinstaller", "python", "-m", "PyInstaller", "server.spec"],
  cwd: fastapiDirectory,
  selected: fastapiBinarySelected,
  notRunReason: "requires --with-fastapi-binary; Ubuntu CI runs this required gate",
  preconditionDescription: "host build toolchain and PyInstaller download",
});

const cypressSelected = requested("--with-cypress");
runStep({
  name: "Cypress binary verification",
  command: npx,
  args: ["--no-install", "cypress", "verify"],
  cwd: nextDirectory,
  selected: cypressSelected,
  notRunReason: "requires --with-cypress; GitHub uses the pinned Cypress action with Xvfb",
  precondition: node22Precondition,
  preconditionDescription: "Node 22, npm ci, and cached Cypress binary",
});
const cypressSpecs = [
  "**/ChartLeaf.cy.tsx",
  "**/Variants.cy.tsx",
  "**/UploadPage.cy.tsx", "**/DashboardPage.cy.tsx",
  "**/ThemeComposer.cy.tsx",
  "**/TemplateSelection.cy.tsx",
  "**/presentationGenUpload.cy.ts",
  "**/presentation-generation.cy.ts",
  "**/api/authored.cy.ts",
  "**/usePresentationGeneration.cy.tsx",
  "**/PresentationHeader.cy.tsx",
  "**/Chat.cy.tsx",
  "**/WorkspaceChatPanel.cy.tsx",
  "**/SharePopover.cy.tsx",
  "**/TemplateV2Studio.cy.tsx",
  "**/TemplateV2Studio.matrix.cy.tsx",
  "**/TemplateV2SelectionToolbar.cy.tsx",
  "**/VectorToolbar.vector-geometry.cy.tsx",
  "**/ChartTableToolbar.cy.tsx",
  "**/TemplateV2PptxImportPanel.cy.tsx",
].join(",");
const cypressCommand = !isWindows && findOnPath("xvfb-run") ? "xvfb-run" : npx;
const cypressArgs = cypressCommand === "xvfb-run"
  ? ["-a", npx, "--no-install", "cypress", "run", "--component", "--spec", cypressSpecs]
  : ["--no-install", "cypress", "run", "--component", "--spec", cypressSpecs];
runStep({
  name: "Cypress component gate",
  command: cypressCommand,
  args: cypressArgs,
  cwd: nextDirectory,
  selected: cypressSelected,
  notRunReason: "requires --with-cypress",
  precondition: withNode22(() => !isWindows && !findOnPath("xvfb-run") ? "xvfb-run is required on Linux" : null),
  preconditionDescription: "Node 22, Cypress binary; xvfb-run on Linux",
});

const fidelitySelected = requested("--with-fidelity");
runStep({
  name: `Template V2 ${platform()} structural + required visual fidelity`,
  command: npm,
  args: ["run", "test:template-v2-export-fidelity"],
  cwd: nextDirectory,
  selected: fidelitySelected,
  notRunReason: "requires --with-fidelity; GitHub runs both Ubuntu and Windows independently",
  env: {
    REQUIRE_TEMPLATE_V2_VISUAL: "1",
    TEST_ARTIFACT_DIR: artifactDirectory,
    ...(sofficePath ? { SOFFICE_PATH: sofficePath } : {}),
    ...(pdfToCairoPath ? { PDFTOCAIRO_PATH: pdfToCairoPath } : {}),
  },
  precondition: withNode22(() => {
    if (!browserPath) return "Chrome/Chromium not found";
    if (!sofficePath) return "LibreOffice/soffice not found";
    if (!pdfToCairoPath) return "Poppler pdftocairo not found";
    return null;
  }),
  preconditionDescription: "Node 22, Chrome/Chromium, LibreOffice, and Poppler pdftocairo",
});
const postgresSelected = requested("--with-postgres");
const postgresUrl = process.env.PPT_AGENT_POSTGRES_TEST_URL;
runStep({
  name: "Real PostgreSQL integration suites",
  command: uv,
  args: [
    "run",
    "pytest",
    "-q",
    "--no-header",
    "--basetemp",
    join(pytestBaseDirectory, "postgres"),
    "tests/integration/test_postgresql_async_task_read_facade.py",
    "tests/integration/test_postgresql_authored_cluster_quota.py",
    "tests/integration/test_postgresql_provider_settings_sync.py",
    "tests/integration/test_postgresql_template_v2_migrations.py",
    "tests/integration/test_postgresql_template_v2_canary_rollback.py",
    "tests/integration/test_postgresql_workspace.py",
  ],
  cwd: fastapiDirectory,
  selected: postgresSelected,
  notRunReason:
    "requires --with-postgres and a disposable PPT_AGENT_POSTGRES_TEST_URL; never use a shared or managed database",
  env: {
    ...(postgresUrl ? { PPT_AGENT_POSTGRES_TEST_URL: postgresUrl } : {}),
    PPT_AGENT_REQUIRE_POSTGRES_INTEGRATION: "1",
  },
  precondition: () => {
    if (!postgresUrl) return "PPT_AGENT_POSTGRES_TEST_URL is not set";
    let databaseName;
    try {
      databaseName = decodeURIComponent(new URL(postgresUrl).pathname)
        .split("/")
        .filter(Boolean)
        .at(-1) || "";
    } catch {
      return "PPT_AGENT_POSTGRES_TEST_URL is not a valid absolute URL";
    }
    if (!/(?:test|tests)$/iu.test(databaseName)) {
      return `database name must end in test/tests; received ${databaseName || "<empty>"}`;
    }
    return null;
  },
  preconditionDescription:
    "running disposable PostgreSQL database whose name ends in test/tests; never a shared or managed database",
});
const electronSelected = requested("--with-electron");
const electronPrecondition = withNode22(() => isWindows ? null : "the release gate is Windows-only");
runStep({
  name: "Electron locked dependency install",
  command: npm,
  args: ["ci"],
  cwd: electronDirectory,
  selected: electronSelected,
  notRunReason: "requires --with-electron on Windows",
  env: { CYPRESS_INSTALL_BINARY: "0" },
  precondition: electronPrecondition,
  preconditionDescription: "Windows, Node 22, and npm",
});
runStep({
  name: "Electron release dependency audit",
  command: npm,
  args: ["run", "audit:release"],
  cwd: electronDirectory,
  selected: electronSelected,
  notRunReason: "requires --with-electron on Windows",
  precondition: electronPrecondition,
  preconditionDescription: "Windows and electron npm ci",
});
for (const script of [
  "test:audit-release",
  "test:standalone-copy",
  "test:package-preflight",
  "test:build-config",
  "test:update-channel",
  "test:runtime-settings-refresh",
]) {
  runStep({
    name: `Electron ${script}`,
    command: npm,
    args: ["run", script],
    cwd: electronDirectory,
    selected: electronSelected,
    notRunReason: "requires --with-electron on Windows",
    precondition: electronPrecondition,
    preconditionDescription: "Windows and electron npm ci",
  });
}
runStep({
  name: "Windows release artifact verifier",
  command: process.execPath,
  args: ["--test", "scripts/verify-windows-release.test.cjs"],
  cwd: electronDirectory,
  selected: electronSelected,
  notRunReason: "requires --with-electron on Windows",
  precondition: electronPrecondition,
  preconditionDescription: "Windows and electron npm ci",
});
runStep({
  name: "Electron authored-hybrid release subset",
  command: process.execPath,
  args: [
    "--experimental-strip-types",
    "--test",
    "lib/authored-hybrid/mode.test.mjs",
    "lib/authored-hybrid/pptx-archive.test.mjs",
    "lib/authored-hybrid/cleanup.test.mjs",
  ],
  cwd: nextDirectory,
  selected: electronSelected,
  notRunReason: "requires --with-electron on Windows",
  precondition: electronPrecondition,
  preconditionDescription: "Windows and Next.js npm ci",
});
for (const script of ["typecheck", "build:ts", "check:main-no-undef"]) {
  runStep({
    name: `Electron ${script}`,
    command: npm,
    args: ["run", script],
    cwd: electronDirectory,
    selected: electronSelected,
    notRunReason: "requires --with-electron on Windows",
    precondition: electronPrecondition,
    preconditionDescription: "Windows and electron npm ci",
  });
}
runStep({
  name: "Electron MSIX export runtime isolation",
  command: process.execPath,
  args: ["--test", "tests/export-msix-runtime.test.cjs"],
  cwd: electronDirectory,
  selected: electronSelected,
  notRunReason: "requires --with-electron on Windows",
  precondition: electronPrecondition,
  preconditionDescription: "Windows, compiled Electron TypeScript, and electron npm ci",
});

console.log("\nSummary");
for (const result of results) {
  console.log(`${result.status.padEnd(7)} ${result.name}${result.detail ? ` — ${result.detail}` : ""}`);
}

const failures = results.filter((result) => result.status === "FAIL");
const notRun = results.filter((result) => result.status === "NOT RUN");
if (dryRun) {
  console.log(`\nDry-run only: ${results.filter((result) => result.status === "PLAN").length} planned, ${notRun.length} NOT RUN.`);
  console.log("No command or precondition was executed.");
  process.exit(0);
}
if (failures.length > 0) {
  console.error(`\nLocal parity failed: ${failures.length} selected gate(s) failed.`);
  process.exit(1);
}
console.log(`\nSelected host profile passed; ${notRun.length} capability step(s) were NOT RUN.`);
console.log("GitHub remains authoritative for the other OS, service, packaging, and runner-image gates.");
