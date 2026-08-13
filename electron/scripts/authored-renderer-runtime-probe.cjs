#!/usr/bin/env node
"use strict";

const fs = require("node:fs");
const path = require("node:path");
const crypto = require("node:crypto");
const {
  MANIFEST_NAME: POLICY_PROVENANCE_NAME,
  manifestPath: policyProvenancePath,
  verifyPolicyProvenance,
} = require("./authored-renderer-policy-provenance.cjs");

const OUTPUT_SCHEMA = "authored-electron-security-observation/v1";
const PACKAGE_SCHEMA = "authored-electron-probe-package/v2";

function sha256(filePath) {
  return crypto.createHash("sha256").update(fs.readFileSync(filePath)).digest("hex");
}

function argument(name) {
  const index = process.argv.indexOf(name);
  if (index === -1 || !process.argv[index + 1]) throw new Error(`${name} is required`);
  return path.resolve(process.argv[index + 1]);
}

function outputPath() {
  return argument("--output");
}

function writeAtomic(target, value) {
  fs.mkdirSync(path.dirname(target), { recursive: true });
  const temporary = `${target}.tmp-${process.pid}`;
  fs.writeFileSync(temporary, `${JSON.stringify(value, null, 2)}\n`, {
    encoding: "utf8",
    mode: 0o600,
  });
  fs.renameSync(temporary, target);
}

function linuxSandboxStatus(pid) {
  const status = fs.readFileSync(`/proc/${pid}/status`, "utf8");
  return {
    noNewPrivileges: /^NoNewPrivs:\s+1$/mu.test(status),
    seccompFiltering: /^Seccomp:\s+2$/mu.test(status),
  };
}

function findPackagedExecutable(root) {
  const expected = process.platform === "win32"
    ? "authored-renderer-probe.exe"
    : "authored-renderer-probe";
  const pending = [root];
  while (pending.length > 0) {
    const directory = pending.pop();
    for (const entry of fs.readdirSync(directory, { withFileTypes: true })) {
      const target = path.join(directory, entry.name);
      if (entry.isDirectory()) pending.push(target);
      else if (entry.name === expected) return target;
    }
  }
  throw new Error(`packaged probe executable ${expected} was not produced`);
}

async function buildPackagedProbe() {
  const packageOutput = argument("--package-output");
  const manifest = argument("--manifest");
  const project = `${packageOutput}-source`;
  const scriptTarget = path.join(project, "scripts", path.basename(__filename));
  const policySource = path.resolve(__dirname, "../app_dist/utils/authored-renderer-policy.js");
  const policyTarget = path.join(project, "app_dist", "utils", "authored-renderer-policy.js");
  const electronRoot = path.resolve(__dirname, "..");
  const policyProvenance = verifyPolicyProvenance(electronRoot);
  const provenanceSource = policyProvenancePath(electronRoot);
  const provenanceTarget = path.join(project, "app_dist", POLICY_PROVENANCE_NAME);
  const helperTarget = path.join(project, "scripts", "authored-renderer-policy-provenance.cjs");
  fs.mkdirSync(path.dirname(scriptTarget), { recursive: true });
  fs.mkdirSync(path.dirname(policyTarget), { recursive: true });
  fs.copyFileSync(__filename, scriptTarget);
  fs.copyFileSync(path.join(__dirname, "authored-renderer-policy-provenance.cjs"), helperTarget);
  fs.copyFileSync(policySource, policyTarget);
  fs.copyFileSync(provenanceSource, provenanceTarget);
  fs.writeFileSync(path.join(project, "package.json"), JSON.stringify({
    name: "authored-renderer-probe",
    productName: "Authored Renderer Probe",
    version: "1.0.0",
    main: `scripts/${path.basename(__filename)}`,
  }, null, 2));
  const { Arch, Platform, build } = require("electron-builder");
  const target = process.platform === "win32"
    ? Platform.WINDOWS.createTarget(["dir"], Arch.x64)
    : Platform.LINUX.createTarget(["dir"], Arch.x64);
  await build({
    projectDir: project,
    targets: target,
    publish: "never",
    config: {
      appId: "PresentonAI.AuthoredRendererProbe",
      productName: "Authored Renderer Probe",
      asar: true,
      electronVersion: require("../node_modules/electron/package.json").version,
      npmRebuild: false,
      directories: { output: packageOutput },
      files: ["package.json", "scripts/**/*", "app_dist/**/*"],
      linux: { target: ["dir"], executableName: "authored-renderer-probe" },
      win: { target: ["dir"], executableName: "authored-renderer-probe" },
    },
  });
  const executable = findPackagedExecutable(packageOutput);
  writeAtomic(manifest, {
    schema_version: PACKAGE_SCHEMA,
    executable,
    executable_sha256: sha256(executable),
    platform: process.platform,
    probe_source_sha256: sha256(__filename),
    policy_sha256: sha256(policySource),
    policy_provenance_manifest_sha256: sha256(provenanceSource),
    policy_provenance: policyProvenance,
  });
}

async function observe(app, BrowserWindow, session) {
  const target = outputPath();
  const observerErrors = [];
  const packagedPolicyProvenance = verifyPolicyProvenance(path.resolve(__dirname, ".."), {
    packaged: true,
  });
  const partition = `authored-security-${crypto.randomUUID()}`;
  const renderSession = session.fromPartition(partition, { cache: false });
  renderSession.setPermissionCheckHandler(() => false);
  renderSession.setPermissionRequestHandler((_contents, _permission, callback) => callback(false));
  renderSession.setDevicePermissionHandler(() => false);
  renderSession.setSpellCheckerEnabled(false);

  const { getIsolatedMetadataWebPreferences } = require(
    "../app_dist/utils/authored-renderer-policy.js"
  );
  const configuredPreferences = getIsolatedMetadataWebPreferences(partition);
  const window = new BrowserWindow({
    width: 320,
    height: 180,
    show: false,
    paintWhenInitiallyHidden: true,
    webPreferences: configuredPreferences,
  });
  window.webContents.setWindowOpenHandler(() => ({ action: "deny" }));
  window.webContents.on("will-navigate", (event) => event.preventDefault());

  try {
    const document = [
      "<!doctype html><html><head><meta charset=utf-8>",
      "<meta http-equiv=Content-Security-Policy content=\"default-src 'none'; script-src 'none'; connect-src 'none'; frame-src 'none'; object-src 'none'\">",
      "</head><body><main id=probe>isolated</main></body></html>",
    ].join("");
    await window.loadURL(`data:text/html;charset=utf-8,${encodeURIComponent(document)}`);
    const rendererPid = window.webContents.getOSProcessId();
    const actualPreferences = window.webContents.getLastWebPreferences();
    const globals = await window.webContents.executeJavaScript(
      "({requireType:typeof require,processType:typeof process,electronType:typeof window.electron})",
      true,
    );
    const ipcNegative = await window.webContents.executeJavaScript(`(() => {
      const probes = [
        () => require("electron").ipcRenderer.send("authored-negative-probe"),
        () => process.mainModule.require("electron").ipcRenderer.send("authored-negative-probe"),
        () => window.electron.ipcRenderer.send("authored-negative-probe"),
      ];
      let blocked = 0;
      for (const probe of probes) {
        try { probe(); } catch { blocked += 1; }
      }
      return { attempted: probes.length, blocked };
    })()`, true);
    const metric = app.getAppMetrics().find((item) => item.pid === rendererPid);
    let osSandboxed = metric?.sandboxed === true;
    if (process.platform === "linux") {
      try {
        const linux = linuxSandboxStatus(rendererPid);
        osSandboxed = linux.noNewPrivileges && linux.seccompFiltering;
      } catch (error) {
        observerErrors.push(`linux-sandbox-status:${error?.code || error?.name || "error"}`);
      }
    }

    const controls = {
      renderer_pid_observed: Number.isInteger(rendererPid) && rendererPid > 0,
      renderer_process_metric_observed: metric?.type === "Tab",
      packaged_runtime_observed: app.isPackaged === true,
      os_sandboxed: osSandboxed,
      actual_web_preferences_observed: actualPreferences && typeof actualPreferences === "object",
      sandbox_preference: actualPreferences.sandbox === true,
      web_security: actualPreferences.webSecurity === true,
      context_isolation: actualPreferences.contextIsolation === true,
      node_integration_disabled: actualPreferences.nodeIntegration !== true,
      subframe_node_integration_disabled: actualPreferences.nodeIntegrationInSubFrames !== true,
      worker_node_integration_disabled: actualPreferences.nodeIntegrationInWorker !== true,
      preload_absent: !actualPreferences.preload,
      configured_preferences_match_actual:
        configuredPreferences.sandbox === actualPreferences.sandbox &&
        configuredPreferences.webSecurity === actualPreferences.webSecurity &&
        configuredPreferences.contextIsolation === actualPreferences.contextIsolation &&
        configuredPreferences.nodeIntegration === (actualPreferences.nodeIntegration === true) &&
        configuredPreferences.nodeIntegrationInSubFrames === (actualPreferences.nodeIntegrationInSubFrames === true) &&
        configuredPreferences.nodeIntegrationInWorker === (actualPreferences.nodeIntegrationInWorker === true),
      ipc_negative_probe_blocked:
        ipcNegative.attempted === 3 && ipcNegative.blocked === ipcNegative.attempted,
      privileged_bridge_absent:
        globals.requireType === "undefined" &&
        globals.processType === "undefined" &&
        globals.electronType === "undefined",
      ephemeral_session: !partition.startsWith("persist:") && window.webContents.session === renderSession,
      compiled_policy_matches_build_provenance: true,
    };
    const observation = {
      schema_version: OUTPUT_SCHEMA,
      run_id: `electron-${Date.now()}`,
      electron_version: process.versions.electron,
      platform: process.platform,
      executable_sha256: crypto.createHash("sha256").update(fs.readFileSync(process.execPath)).digest("hex"),
      policy_provenance_manifest_sha256: sha256(
        policyProvenancePath(path.resolve(__dirname, ".."))
      ),
      policy_provenance: packagedPolicyProvenance,
      controls,
      observer_errors: observerErrors,
    };
    writeAtomic(target, observation);
    return observerErrors.length === 0 && Object.values(controls).every(Boolean) ? 0 : 1;
  } finally {
    if (!window.isDestroyed()) window.destroy();
  }
}

if (process.argv.includes("--build-packaged")) {
  buildPackagedProbe().catch((error) => {
    console.error(error instanceof Error ? error.message : "packaged probe build failed");
    process.exitCode = 1;
  });
} else {
  const { app, BrowserWindow, session } = require("electron");
  app.enableSandbox();
  app.commandLine.appendSwitch("site-per-process");
  app.whenReady().then(async () => {
    let code = 1;
    try {
      code = await observe(app, BrowserWindow, session);
    } catch (error) {
      try {
        writeAtomic(outputPath(), {
          schema_version: OUTPUT_SCHEMA,
          run_id: `electron-error-${Date.now()}`,
          controls: {},
          observer_errors: [error?.name || "runtime-probe-error"],
        });
      } catch {
        // The caller will also fail on the missing observation artifact.
      }
    } finally {
      app.exit(code);
    }
  });
}
