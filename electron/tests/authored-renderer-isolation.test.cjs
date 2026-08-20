const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const os = require("node:os");

const {
  verifyPolicyProvenance,
  writePolicyProvenance,
} = require("../scripts/authored-renderer-policy-provenance.cjs");

const {
  getAppWebPreferences,
  getIsolatedMetadataWebPreferences,
  isTrustedIpcSenderUrl,
  shouldBlockTrustedMetadataRequest,
} = require("../app_dist/utils/authored-renderer-policy.js");

const ELECTRON_ROOT = path.join(__dirname, "..");
const REPO_ROOT = path.join(ELECTRON_ROOT, "..");

test("app and installer windows keep web security and renderer sandboxing enabled", () => {
  const preferences = getAppWebPreferences("C:\\preload.js");
  assert.deepEqual(preferences, {
    webSecurity: true,
    allowRunningInsecureContent: false,
    contextIsolation: true,
    nodeIntegration: false,
    nodeIntegrationInSubFrames: false,
    nodeIntegrationInWorker: false,
    sandbox: true,
    webviewTag: false,
    preload: "C:\\preload.js",
  });
});

test("hidden metadata window has no preload and an ephemeral partition contract", () => {
  const isolated = getIsolatedMetadataWebPreferences("slide-metadata-random-id");
  assert.equal(isolated.webSecurity, true);
  assert.equal(isolated.contextIsolation, true);
  assert.equal(isolated.nodeIntegration, false);
  assert.equal(isolated.nodeIntegrationInSubFrames, false);
  assert.equal(isolated.nodeIntegrationInWorker, false);
  assert.equal(isolated.sandbox, true);
  assert.equal(isolated.webviewTag, false);
  assert.equal(isolated.partition, "slide-metadata-random-id");
  assert.equal(Object.hasOwn(isolated, "preload"), false);
});

test("metadata navigation and privileged IPC require the exact trusted origin", () => {
  const origin = "http://127.0.0.1:43127";
  assert.equal(shouldBlockTrustedMetadataRequest(`${origin}/presentation?id=1`, origin), false);
  for (const url of [
    "http://127.0.0.1:9/probe",
    "https://127.0.0.1:43127/presentation?id=1",
    "file:///etc/passwd",
    "data:text/html,hostile",
    "blob:http://127.0.0.1:43127/id",
  ]) {
    assert.equal(shouldBlockTrustedMetadataRequest(url, origin), true, url);
  }

  assert.equal(isTrustedIpcSenderUrl(`${origin}/presentation?id=1`, origin), true);
  assert.equal(isTrustedIpcSenderUrl("file:///tmp/hostile.html", origin), false);
  assert.equal(isTrustedIpcSenderUrl("http://127.0.0.1:9/probe", origin), false);
});

test("Electron authored preview is PNG-only and never constructs raw authored DOM", () => {
  const preview = fs.readFileSync(
    path.join(
      REPO_ROOT,
      "servers",
      "nextjs",
      "app",
      "(presentation-generator)",
      "components",
      "AuthoredSlidePreview.tsx",
    ),
    "utf8",
  );
  assert.match(preview, /srcDoc=\{isolatedHtmlSource\}/);
  assert.match(preview, /sandbox=""/);
  assert.doesNotMatch(preview, /allow-scripts|allow-same-origin/);
  assert.match(preview, /useState<"unknown" \| "web" \| "electron">/);
  assert.match(preview, /setRuntime\(window\.electron \? "electron" : "web"\)/);
  assert.match(
    preview,
    /canRenderHtml\s*=\s*\n?\s*runtime === "web" && preferHtml && htmlSource\.length > 0/,
  );
  assert.match(preview, /data-authored-runtime=\{runtime\}/);
  assert.match(preview, /backend worker's PNG artifact/);
  assert.match(preview, /\) : imageSrc \? \(/);
  assert.match(preview, /Authored slide preview is unavailable/);
  for (const directive of [
    "default-src 'none'",
    "connect-src 'none'",
    "frame-src 'none'",
    "script-src 'none'",
  ]) {
    assert.ok(preview.includes(directive), directive);
  }

  const contentRender = fs.readFileSync(
    path.join(
      REPO_ROOT,
      "servers",
      "nextjs",
      "app",
      "(presentation-generator)",
      "components",
      "PresentationContentRender.tsx",
    ),
    "utf8",
  );
  const authoredBranch = contentRender.slice(
    contentRender.indexOf("const AuthoredRenderer"),
    contentRender.indexOf("const TemplateV2Renderer"),
  );
  assert.match(authoredBranch, /AuthoredSlidePreview/);
  assert.doesNotMatch(authoredBranch, /SmartHtmlEditor|innerHTML|driver_id/);

  const main = fs.readFileSync(path.join(ELECTRON_ROOT, "app", "main.ts"), "utf8");
  assert.match(main, /app\.enableSandbox\(\)/);
  assert.match(main, /appendSwitch\("site-per-process"\)/);

  const policy = fs.readFileSync(
    path.join(ELECTRON_ROOT, "app", "utils", "authored-renderer-policy.ts"),
    "utf8",
  );
  assert.match(policy, /nodeIntegrationInSubFrames:\s*false/);
  assert.match(policy, /nodeIntegrationInWorker:\s*false/);
  assert.match(policy, /never constructs authored srcDoc/);

  const preload = fs.readFileSync(path.join(ELECTRON_ROOT, "app", "preloads", "index.ts"), "utf8");
  assert.doesNotMatch(preload, /authored|htmlContent|html_content|rawHtml/i);
});

test("product sources contain zero unsafe Chromium or Electron options", () => {
  const sourceFiles = [];
  const visit = (directory) => {
    for (const entry of fs.readdirSync(directory, { withFileTypes: true })) {
      const target = path.join(directory, entry.name);
      if (entry.isDirectory()) visit(target);
      else if (entry.name.endsWith(".ts")) sourceFiles.push(target);
    }
  };
  visit(path.join(ELECTRON_ROOT, "app"));
  const productSource = sourceFiles.map((file) => fs.readFileSync(file, "utf8")).join("\n");
  const packageSource = fs.readFileSync(path.join(ELECTRON_ROOT, "package.json"), "utf8");
  assert.doesNotMatch(productSource, new RegExp("webSecurity\\s*:\\s*" + "false"));
  assert.doesNotMatch(productSource, new RegExp("sandbox\\s*:\\s*" + "false"));
  assert.doesNotMatch(
    `${productSource}\n${packageSource}`,
    new RegExp([
      "--no-" + "sandbox",
      "disable-web-" + "security",
      "allow-file-access-" + "from-files",
      "disable-site-" + "isolation-trials",
      "disable-features=.*siteperprocess",
      "process-per-" + "site",
    ].join("|"), "i"),
  );
});

test("slide metadata IPC uses sender validation and the preload-less window factory", () => {
  const source = fs.readFileSync(
    path.join(ELECTRON_ROOT, "app", "ipc", "slide_metadata.ts"),
    "utf8",
  );
  assert.match(source, /assertTrustedIpcSender/);
  assert.match(source, /assertTrustedMetadataUrl/);
  assert.match(source, /createTrustedMetadataWindow/);
  assert.doesNotMatch(
    source,
    new RegExp("preloads/index|webSecurity\\s*:\\s*" + "false|new BrowserWindow"),
  );
  const isolation = fs.readFileSync(
    path.join(ELECTRON_ROOT, "app", "utils", "authored-renderer-isolation.ts"),
    "utf8",
  );
  assert.match(isolation, /event\.senderFrame !== event\.sender\.mainFrame/);
});

test("disposable runtime probe records a real PID and Linux sandbox state", () => {
  const probe = fs.readFileSync(
    path.join(ELECTRON_ROOT, "scripts", "authored-renderer-runtime-probe.cjs"),
    "utf8",
  );
  assert.match(probe, /getOSProcessId\(\)/);
  assert.match(probe, /configuredPreferences/);
  assert.match(probe, /getLastWebPreferences\(\)/);
  assert.match(probe, /app\.getAppMetrics\(\)/);
  assert.match(probe, /NoNewPrivs:\\s\+1/);
  assert.match(probe, /Seccomp:\\s\+2/);
  assert.match(probe, /preload_absent/);
  assert.match(probe, /privileged_bridge_absent/);
  assert.match(probe, /packaged_runtime_observed/);
  assert.match(probe, /actual_web_preferences_observed/);
  assert.match(probe, /ipc_negative_probe_blocked/);
  assert.match(probe, /compiled_policy_matches_build_provenance/);
  assert.match(probe, /policy_provenance_manifest_sha256/);
  assert.match(probe, /--build-packaged/);
  assert.match(probe, /electron-builder/);
  assert.doesNotMatch(probe, /--no-sandbox|disable-web-security|allow-file-access-from-files/i);
});

test("packaged policy provenance rejects stale TypeScript and compiled output", (context) => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "authored-policy-provenance-"));
  context.after(() => fs.rmSync(root, { recursive: true, force: true }));
  const source = path.join(root, "app", "utils", "authored-renderer-policy.ts");
  const compiled = path.join(root, "app_dist", "utils", "authored-renderer-policy.js");
  fs.mkdirSync(path.dirname(source), { recursive: true });
  fs.mkdirSync(path.dirname(compiled), { recursive: true });
  fs.writeFileSync(path.join(root, "tsconfig.json"), "{}\n");
  fs.writeFileSync(source, "export const sandbox = true;\n");
  fs.writeFileSync(compiled, '"use strict"; exports.sandbox = true;\n');

  writePolicyProvenance(root);
  assert.doesNotThrow(() => verifyPolicyProvenance(root));
  fs.appendFileSync(source, "// stale source\n");
  assert.throws(() => verifyPolicyProvenance(root), /source changed after build:ts/);

  writePolicyProvenance(root);
  fs.appendFileSync(compiled, "// stale output\n");
  assert.throws(() => verifyPolicyProvenance(root), /compiled changed after build:ts/);
});
