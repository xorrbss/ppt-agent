#!/usr/bin/env node

import { existsSync, mkdirSync } from "node:fs";
import { delimiter, dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { platform, tmpdir } from "node:os";
import { spawnSync } from "node:child_process";

const root = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const isWindows = platform() === "win32";
const npm = isWindows ? "npm.cmd" : "npm";
const npx = isWindows ? "npx.cmd" : "npx";
const uv = isWindows ? "uv.exe" : "uv";
const docker = isWindows ? "docker.exe" : "docker";
const bash = "bash";
const argv = new Set(process.argv.slice(2));
const knownArguments = new Set([
  "--all",
  "--dry-run",
  "--help",
  "--with-cypress",
  "--with-electron",
  "--with-fastapi-binary",
  "--with-fidelity",
  "--with-g4",
  "--with-postgres",
]);

const unknownArguments = [...argv].filter((argument) => !knownArguments.has(argument));
if (unknownArguments.length > 0) {
  console.error(`Unknown argument(s): ${unknownArguments.join(", ")}`);
  process.exit(2);
}

if (argv.has("--help")) {
  console.log(`Usage: npm run test:local -- [options]

The default profile runs the portable CI contracts, locked FastAPI suite, and
Next.js Node/lint/build gates. Capability-specific gates are never silently
treated as passed.

Options:
  --dry-run              Print commands, preconditions, and NOT RUN gates only
  --with-fastapi-binary  Build the PyInstaller binary used by Ubuntu CI
  --with-cypress         Run the gated Cypress component specs
  --with-fidelity        Require visual tools and run host-OS export fidelity
  --with-postgres        Require PPT_AGENT_POSTGRES_TEST_URL and run live DB tests
  --with-electron        Require Windows and run the Windows release gate
  --with-g4              Require Linux, Bash, and Docker; run both G4 round-trips
  --all                  Request every capability gate (fails if one is unavailable)
  --help                 Show this help
`);
  process.exit(0);
}

const dryRun = argv.has("--dry-run");
const requestAll = argv.has("--all");
const requested = (flag) => requestAll || argv.has(flag);
const runtimeRoot = join(tmpdir(), "ppt-agent-ci-local");
const appDataDirectory = join(runtimeRoot, "app-data");
const tempDirectory = join(runtimeRoot, "temp");
const artifactDirectory = join(runtimeRoot, "artifacts", `template-v2-${platform()}`);
const sqlitePath = join(runtimeRoot, "test.db").replaceAll("\\", "/");
const fastapiDirectory = join(root, "servers", "fastapi");
const nextDirectory = join(root, "servers", "nextjs");
const electronDirectory = join(root, "electron");
const results = [];

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

function findBrowser() {
  const configured = process.env.PUPPETEER_EXECUTABLE_PATH;
  if (configured && existsSync(configured)) return configured;

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
runStep({
  name: "Root locked npm install",
  command: npm,
  args: ["ci"],
  precondition: () => nodeMajor === 22 ? null : `Node 22 required; found ${process.versions.node}`,
  preconditionDescription: "Node 22 and npm",
});
runStep({
  name: "presentation-export v0.4.2 synchronization contract",
  command: npm,
  args: ["run", "test:presentation-export-sync"],
});
runStep({
  name: "Synchronize pinned presentation-export runtime",
  command: npm,
  args: ["run", "sync:presentation-export"],
  preconditionDescription: "network access only when the pinned v0.4.2 runtime is absent or stale",
});
runStep({
  name: "Verify pinned presentation-export runtime",
  command: npm,
  args: ["run", "check:presentation-export"],
});
runStep({
  name: "Static upstream compatibility verifier",
  command: process.execPath,
  args: ["--test", "scripts/verify-upstream-compatibility.test.mjs"],
});
runStep({
  name: "Offline upstream intake fixture",
  command: process.execPath,
  args: ["--test", "scripts/intake-upstream-main.test.mjs"],
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
  args: ["run", "pytest", "-q", "--no-header"],
  cwd: fastapiDirectory,
  precondition: () => browserPath ? null : "Chrome/Chromium is required because CI=true forbids Chrome smoke skips",
  preconditionDescription: "locked dependencies and Chrome/Chromium; feature flags remain default OFF",
});
runStep({
  name: "Next.js locked dependency install",
  command: npm,
  args: ["ci"],
  cwd: nextDirectory,
});
runStep({
  name: "Shared Next.js CI Node suite",
  command: npm,
  args: ["run", "test:next-ci"],
  precondition: () => browserPath ? null : "Chrome/Chromium is required because CI=true forbids Chrome smoke skips",
  preconditionDescription: "Next.js npm ci and Chrome/Chromium",
});
runStep({
  name: "Next.js lint",
  command: npm,
  args: ["run", "lint"],
  cwd: nextDirectory,
});
runStep({
  name: "Next.js production build",
  command: npm,
  args: ["run", "build"],
  cwd: nextDirectory,
  env: {
    NEXT_PUBLIC_FAST_API: "http://localhost:8000",
    NEXT_PUBLIC_URL: "http://localhost:3000",
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
  preconditionDescription: "npm ci and cached Cypress binary",
});
const cypressSpecs = [
  "**/AdaptiveBlockControls.cy.tsx",
  "**/AdaptivePropertyControls.cy.tsx",
  "**/ChartLeaf.cy.tsx",
  "**/Variants.cy.tsx",
  "**/UploadPage.cy.tsx",
  "**/ThemeComposer.cy.tsx",
  "**/TemplateSelection.cy.tsx",
  "**/presentationGenUpload.cy.ts",
  "**/presentation-generation.cy.ts",
  "**/api/authored.cy.ts",
  "**/usePresentationGeneration.cy.tsx",
  "**/PresentationHeader.cy.tsx",
  "**/TemplateV2Studio.cy.tsx",
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
  precondition: () => !isWindows && !findOnPath("xvfb-run") ? "xvfb-run is required on Linux" : null,
  preconditionDescription: "Cypress binary; xvfb-run on Linux",
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
  precondition: () => {
    if (!browserPath) return "Chrome/Chromium not found";
    if (!sofficePath) return "LibreOffice/soffice not found";
    if (!pdfToCairoPath) return "Poppler pdftocairo not found";
    return null;
  },
  preconditionDescription: "Chrome/Chromium, LibreOffice, and Poppler pdftocairo",
});

const postgresSelected = requested("--with-postgres");
const postgresUrl = process.env.PPT_AGENT_POSTGRES_TEST_URL;
runStep({
  name: "Template V2 real PostgreSQL integration",
  command: uv,
  args: [
    "run",
    "pytest",
    "-q",
    "--no-header",
    "tests/integration/test_postgresql_template_v2_migrations.py",
    "tests/integration/test_postgresql_template_v2_canary_rollback.py",
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
const electronPrecondition = () => isWindows ? null : "the release gate is Windows-only";
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
for (const script of [
  "test:standalone-copy",
  "test:package-preflight",
  "test:build-config",
  "test:update-channel",
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

const g4Selected = requested("--with-g4");
const g4Script = String.raw`set -euo pipefail
override="$(mktemp)"
cleanup() {
  docker compose -f docker-compose.yml -f "$override" down -v || true
  rm -f "$override"
}
trap cleanup EXIT
cat > "$override" <<'YML'
services:
  development:
    environment:
      - DISABLE_AUTH=true
      - MEM0_ENABLED=false
      - DISABLE_IMAGE_GENERATION=true
      - CYPRESS_INSTALL_BINARY=0
YML
docker build --target assets-builder --tag presenton-export-runtime-gate .
docker run --rm presenton-export-runtime-gate sh -lc 'node /app/scripts/sync-presentation-export.cjs --check-only && node --check /app/presentation-export/index.cjs && test -x /app/presentation-export/py/convert-linux-x64'
docker compose -f docker-compose.yml -f "$override" up -d development
ready=0
for i in $(seq 1 120); do
  fa=$(docker compose exec -T development bash -lc 'curl -s -o /dev/null -w "%{http_code}" http://127.0.0.1:8000/openapi.json' 2>/dev/null || true)
  ng=$(docker compose exec -T development bash -lc 'curl -s -o /dev/null -w "%{http_code}" http://127.0.0.1:80/' 2>/dev/null || true)
  echo "try $i fastapi=$fa nginx=$ng"
  if [ -n "$fa" ] && [ "$fa" != "000" ] && [ -n "$ng" ] && [ "$ng" != "000" ] && [ "$ng" != "502" ] && [ "$ng" != "504" ]; then
    ready=1
    break
  fi
  sleep 10
done
if [ "$ready" != "1" ]; then
  docker compose logs --tail=60 development
  exit 1
fi
docker compose exec -T -e RUN_PPTX_ROUNDTRIP=1 -e APP_DATA_DIRECTORY=/app_data -e NEXT_PUBLIC_FAST_API=http://127.0.0.1 development bash -lc 'export PATH=$PATH:/root/.local/bin; cd /app/servers/fastapi && uv run python ../../scripts/check_adaptive_pptx_roundtrip.py'
docker compose exec -T -e RUN_PPTX_ROUNDTRIP=1 -e APP_DATA_DIRECTORY=/app_data -e NEXT_PUBLIC_FAST_API=http://127.0.0.1 development bash -lc 'export PATH=$PATH:/root/.local/bin; cd /app/servers/fastapi && uv run python ../../scripts/check_legacy_pptx_roundtrip.py'`;
runStep({
  name: "Linux Docker production runtime + adaptive and legacy G4 round-trips",
  command: bash,
  args: ["-lc", g4Script],
  selected: g4Selected,
  notRunReason: "requires --with-g4; GitHub runs this Linux/Docker gate",
  precondition: () => {
    if (platform() !== "linux") return `Linux required; current host is ${platform()}`;
    if (!findOnPath("bash")) return "bash not found";
    if (!findOnPath("docker")) return "docker not found";
    const info = spawnSync(docker, ["info"], { stdio: "ignore", windowsHide: true });
    if (info.status !== 0) return "Docker daemon is not available";
    return null;
  },
  preconditionDescription: "Linux, Bash, Docker Compose, and a running Docker daemon",
  displayCommand: "bash -lc <production-runtime + compose readiness + adaptive G4 + legacy G4 + teardown>",
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
