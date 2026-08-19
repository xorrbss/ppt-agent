import assert from "node:assert/strict";
import fs from "node:fs/promises";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

import {
  resolveAuthoredHybridChromeExecutable,
  runAuthoredHybridChrome,
  runTrustedSelfContainedAuthoredHybridChrome,
} from "./chrome-runner.ts";
import { instrumentAuthoredHtml } from "./html-instrumentation.ts";
import {
  applyAuthoredRendererCsp,
  authoredRendererChromeArguments,
  authoredRendererEnvironment,
  authoredRendererIsolationPolicy,
  preserveLastKnownGoodArtifact,
} from "./renderer-isolation.ts";
import {
  AUTHORED_RENDER_LIMITS,
  inspectAuthoredHtmlResources,
  preflightAuthoredHtmlForHybrid,
  preflightTrustedSelfContainedAuthoredHtml,
} from "./security.ts";

const moduleDirectory = path.dirname(fileURLToPath(import.meta.url));
const repositoryRoot = path.resolve(moduleDirectory, "../../../..");
const corpusRoot = path.join(
  repositoryRoot,
  "docs/experiments/authored-vnext/04-renderer-security/corpus"
);
const PNG =
  "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNgYAAAAAMAASsJTYQAAAAASUVORK5CYII=";

test("renderer contract contains no unsafe Chrome option and strips application secrets", () => {
  const args = authoredRendererChromeArguments("C:\\isolated-profile", {
    width: 1280,
    height: 720,
  });
  assert.ok(args.includes("--remote-debugging-pipe"));
  assert.equal(args.some((arg) => arg === "--no-sandbox"), false);
  assert.equal(args.some((arg) => arg === "--allow-file-access-from-files"), false);
  assert.equal(args.some((arg) => arg.startsWith("--proxy-server")), false);
  assert.equal(args.some((arg) => arg.startsWith("file://")), false);
  assert.equal(args.at(-1), "about:blank");

  const environment = authoredRendererEnvironment({
    NODE_ENV: "development",
    PATH: "render-path",
    OPENAI_API_KEY: "must-not-cross",
    FAST_API_INTERNAL_URL: "must-not-cross",
    USER_CONFIG_PATH: "must-not-cross",
  });
  assert.equal(environment.PATH, "render-path");
  assert.equal(environment.NODE_ENV, "production");
  assert.equal(environment.OPENAI_API_KEY, undefined);
  assert.equal(environment.FAST_API_INTERNAL_URL, undefined);
  assert.equal(environment.USER_CONFIG_PATH, undefined);
});

test("renderer flags are secure by default, kill-switch fail closed, and LKG commits only success", async () => {
  const previousEnabled = process.env.AUTHORED_VNEXT_RENDERER_ENABLED;
  const previousLegacy = process.env.AUTHORED_LEGACY_FILE_RENDERER_ENABLED;
  const previousGlobalKillSwitch = process.env.AUTHORED_RENDERER_KILL_SWITCH;
  try {
    delete process.env.AUTHORED_VNEXT_RENDERER_ENABLED;
    delete process.env.AUTHORED_LEGACY_FILE_RENDERER_ENABLED;
    delete process.env.AUTHORED_RENDERER_KILL_SWITCH;
    assert.deepEqual(authoredRendererIsolationPolicy(), {
      rendererEnabled: false,
      legacyFileFallbackEnabled: false,
      browserSandbox: true,
      webSecurity: true,
      inputTransport: "cdp-pipe",
      networkPolicy: "cdp-abort-all",
      productionOsIsolation: "development-unattested",
      executionBoundary: "development-local",
    });
    process.env.AUTHORED_VNEXT_RENDERER_ENABLED = "true";
    assert.equal(authoredRendererIsolationPolicy().rendererEnabled, true);
    process.env.AUTHORED_VNEXT_RENDERER_ENABLED = "false";
    await assert.rejects(
      runAuthoredHybridChrome({
        html: "<p>kill switch</p>",
        dumpDom: true,
        screenshot: false,
        chromeExecutable: "C:\\must-not-run\\chrome.exe",
      }),
      /renderer is disabled/
    );
    process.env.AUTHORED_LEGACY_FILE_RENDERER_ENABLED = "true";
    assert.deepEqual(
      {
        rendererEnabled: authoredRendererIsolationPolicy().rendererEnabled,
        legacyFileFallbackEnabled:
          authoredRendererIsolationPolicy().legacyFileFallbackEnabled,
      },
      { rendererEnabled: false, legacyFileFallbackEnabled: true }
    );
    await assert.rejects(
      runAuthoredHybridChrome({
        html: "<p>must not downgrade</p>",
        dumpDom: true,
        screenshot: false,
        chromeExecutable: "C:\\must-not-run\\chrome.exe",
      }),
      /renderer is disabled/
    );
    process.env.AUTHORED_LEGACY_FILE_RENDERER_ENABLED = "false";
    process.env.AUTHORED_VNEXT_RENDERER_ENABLED = "true";
    process.env.AUTHORED_RENDERER_KILL_SWITCH = "true";
    assert.equal(authoredRendererIsolationPolicy().rendererEnabled, false);
    await assert.rejects(
      runAuthoredHybridChrome({
        html: "<p>global kill switch</p>",
        dumpDom: true,
        screenshot: false,
        chromeExecutable: "C:\\must-not-run\\chrome.exe",
      }),
      /renderer is disabled/
    );
    const lastKnownGood = Buffer.from("known-good");
    assert.equal(preserveLastKnownGoodArtifact(lastKnownGood, undefined), lastKnownGood);
    const candidate = Buffer.from("new-success");
    assert.equal(preserveLastKnownGoodArtifact(lastKnownGood, candidate), candidate);
  } finally {
    if (previousEnabled === undefined) delete process.env.AUTHORED_VNEXT_RENDERER_ENABLED;
    else process.env.AUTHORED_VNEXT_RENDERER_ENABLED = previousEnabled;
    if (previousLegacy === undefined) delete process.env.AUTHORED_LEGACY_FILE_RENDERER_ENABLED;
    else process.env.AUTHORED_LEGACY_FILE_RENDERER_ENABLED = previousLegacy;
    if (previousGlobalKillSwitch === undefined) delete process.env.AUTHORED_RENDERER_KILL_SWITCH;
    else process.env.AUTHORED_RENDERER_KILL_SWITCH = previousGlobalKillSwitch;
  }
});

test("production fails closed without the non-root read-only tmpfs network-none contract", () => {
  const previousNodeEnv = process.env.NODE_ENV;
  const previousIsolation = process.env.AUTHORED_RENDERER_OS_ISOLATION;
  const previousBoundary = process.env.AUTHORED_RENDERER_EXECUTION_BOUNDARY;
  try {
    process.env.NODE_ENV = "production";
    delete process.env.AUTHORED_RENDERER_OS_ISOLATION;
    assert.throws(authoredRendererIsolationPolicy, /nonroot-readonly-tmpfs-network-none/);
    process.env.AUTHORED_RENDERER_OS_ISOLATION = "proxy-and-csp-only";
    assert.throws(authoredRendererIsolationPolicy, /nonroot-readonly-tmpfs-network-none/);
    process.env.AUTHORED_RENDERER_OS_ISOLATION =
      "nonroot-readonly-tmpfs-network-none";
    delete process.env.AUTHORED_RENDERER_EXECUTION_BOUNDARY;
    assert.throws(authoredRendererIsolationPolicy, /external authored renderer worker/);
    process.env.AUTHORED_RENDERER_EXECUTION_BOUNDARY = "external-worker";
    assert.equal(
      authoredRendererIsolationPolicy().productionOsIsolation,
      "nonroot-readonly-tmpfs-network-none"
    );
    assert.equal(authoredRendererIsolationPolicy().executionBoundary, "external-worker");
    assert.equal(authoredRendererIsolationPolicy().rendererEnabled, false);
  } finally {
    if (previousNodeEnv === undefined) delete process.env.NODE_ENV;
    else process.env.NODE_ENV = previousNodeEnv;
    if (previousIsolation === undefined) delete process.env.AUTHORED_RENDERER_OS_ISOLATION;
    else process.env.AUTHORED_RENDERER_OS_ISOLATION = previousIsolation;
    if (previousBoundary === undefined) delete process.env.AUTHORED_RENDERER_EXECUTION_BOUNDARY;
    else process.env.AUTHORED_RENDERER_EXECUTION_BOUNDARY = previousBoundary;
  }
});

test("the local native profile is unattested, twice-declared, and kill-switchable", () => {
  const previous = {
    nodeEnv: process.env.NODE_ENV,
    isolation: process.env.AUTHORED_RENDERER_OS_ISOLATION,
    boundary: process.env.AUTHORED_RENDERER_EXECUTION_BOUNDARY,
    renderer: process.env.AUTHORED_VNEXT_RENDERER_ENABLED,
    killSwitch: process.env.AUTHORED_RENDERER_KILL_SWITCH,
  };
  try {
    process.env.NODE_ENV = "production";
    process.env.AUTHORED_VNEXT_RENDERER_ENABLED = "true";
    delete process.env.AUTHORED_RENDERER_KILL_SWITCH;

    // Declaring the profile once must not be enough to run local Chrome.
    process.env.AUTHORED_RENDERER_OS_ISOLATION = "local-native-unattested";
    delete process.env.AUTHORED_RENDERER_EXECUTION_BOUNDARY;
    assert.throws(authoredRendererIsolationPolicy, /EXECUTION_BOUNDARY=local-native/);
    process.env.AUTHORED_RENDERER_EXECUTION_BOUNDARY = "external-worker";
    assert.throws(authoredRendererIsolationPolicy, /EXECUTION_BOUNDARY=local-native/);

    process.env.AUTHORED_RENDERER_EXECUTION_BOUNDARY = "local-native";
    const policy = authoredRendererIsolationPolicy();
    assert.equal(policy.productionOsIsolation, "local-native-unattested");
    assert.equal(policy.executionBoundary, "local-native");
    assert.equal(policy.rendererEnabled, true);
    assert.equal(policy.browserSandbox, true);
    assert.equal(policy.webSecurity, true);

    // The operations kill switch still wins over the local profile.
    process.env.AUTHORED_RENDERER_KILL_SWITCH = "true";
    assert.equal(authoredRendererIsolationPolicy().rendererEnabled, false);
    delete process.env.AUTHORED_RENDERER_KILL_SWITCH;

    // The boundary value alone never unlocks the profile.
    process.env.AUTHORED_RENDERER_OS_ISOLATION = "proxy-and-csp-only";
    assert.throws(authoredRendererIsolationPolicy, /nonroot-readonly-tmpfs-network-none/);
  } finally {
    for (const [name, value] of [
      ["NODE_ENV", previous.nodeEnv],
      ["AUTHORED_RENDERER_OS_ISOLATION", previous.isolation],
      ["AUTHORED_RENDERER_EXECUTION_BOUNDARY", previous.boundary],
      ["AUTHORED_VNEXT_RENDERER_ENABLED", previous.renderer],
      ["AUTHORED_RENDERER_KILL_SWITCH", previous.killSwitch],
    ]) {
      if (value === undefined) delete process.env[name];
      else process.env[name] = value;
    }
  }
});

test("attested production still forbids driving Chrome from Next.js", () => {
  const previous = {
    nodeEnv: process.env.NODE_ENV,
    isolation: process.env.AUTHORED_RENDERER_OS_ISOLATION,
    boundary: process.env.AUTHORED_RENDERER_EXECUTION_BOUNDARY,
    renderer: process.env.AUTHORED_VNEXT_RENDERER_ENABLED,
  };
  try {
    process.env.NODE_ENV = "production";
    process.env.AUTHORED_RENDERER_OS_ISOLATION =
      "nonroot-readonly-tmpfs-network-none";
    process.env.AUTHORED_RENDERER_EXECUTION_BOUNDARY = "external-worker";
    process.env.AUTHORED_VNEXT_RENDERER_ENABLED = "true";
    assert.equal(authoredRendererIsolationPolicy().rendererEnabled, false);
  } finally {
    for (const [name, value] of [
      ["NODE_ENV", previous.nodeEnv],
      ["AUTHORED_RENDERER_OS_ISOLATION", previous.isolation],
      ["AUTHORED_RENDERER_EXECUTION_BOUNDARY", previous.boundary],
      ["AUTHORED_VNEXT_RENDERER_ENABLED", previous.renderer],
    ]) {
      if (value === undefined) delete process.env[name];
      else process.env[name] = value;
    }
  }
});

test("the inert 04 hostile corpus is rejected by the product preflight", async () => {
  const manifest = JSON.parse(
    await fs.readFile(path.join(corpusRoot, "manifest.json"), "utf8")
  );
  for (const fixture of manifest.cases) {
    let html = await fs.readFile(path.join(corpusRoot, fixture.file), "utf8");
    if (fixture.id === "oversized-dom") {
      // The corpus stores a small semantic shape. Materialize only inert nodes;
      // no hostile case is ever sent to Chrome by this test.
      html = `<main>${"<span>x</span>".repeat(AUTHORED_RENDER_LIMITS.maxNodes + 1)}</main>`;
    }
    const result = preflightAuthoredHtmlForHybrid(html);
    assert.equal(result.ok, fixture.id === "safe-control", `${fixture.id}: ${result.reason}`);
  }
});

test("asset digest, byte, pixel, DOM, and collector-font limits are enforced", () => {
  const inspected = inspectAuthoredHtmlResources(`<img src="${PNG}" alt="safe">`);
  assert.equal(inspected.ok, true);
  assert.equal(inspected.imageDigests?.length, 1);
  assert.match(inspected.imageDigests?.[0] ?? "", /^[a-f0-9]{64}$/);
  assert.equal(inspected.aggregateImageBytes, 68);

  const woff2 = `data:font/woff2;base64,${Buffer.concat([
    Buffer.from("wOF2"),
    Buffer.alloc(32),
  ]).toString("base64")}`;
  const collected = `<style>@font-face{font-family:Collected;src:url("${woff2}") format("woff2")}.x{font-family:Collected}</style><p class="x">safe</p>`;
  assert.equal(preflightAuthoredHtmlForHybrid(collected).ok, false);
  assert.deepEqual(preflightTrustedSelfContainedAuthoredHtml(collected), { ok: true });

  const deep = `${"<div>".repeat(AUTHORED_RENDER_LIMITS.maxDomDepth + 1)}x${"</div>".repeat(AUTHORED_RENDER_LIMITS.maxDomDepth + 1)}`;
  assert.equal(preflightAuthoredHtmlForHybrid(deep).reason, "dom-depth-limit");
});

test("CSP authorizes only hashed internal instrumentation", () => {
  const raw = "<!doctype html><html><body><p>safe authored fixture</p></body></html>";
  const instrumented = instrumentAuthoredHtml(raw, { completionToken: "fixture-token" });
  assert.equal(preflightAuthoredHtmlForHybrid(raw).ok, true);
  assert.equal(preflightAuthoredHtmlForHybrid(instrumented).ok, false);
  const protectedHtml = applyAuthoredRendererCsp(instrumented);
  assert.match(protectedHtml, /default-src 'none'/);
  assert.match(protectedHtml, /connect-src 'none'/);
  assert.match(protectedHtml, /script-src 'sha256-[^']+' 'sha256-[^']+'/);
  assert.doesNotMatch(protectedHtml, /script-src[^;]*'unsafe-inline'/);
});

test("safe authored fixture renders through the CDP pipe and trusted scripts stay internal", async (t) => {
  const previousEnabled = process.env.AUTHORED_VNEXT_RENDERER_ENABLED;
  process.env.AUTHORED_VNEXT_RENDERER_ENABLED = "true";
  t.after(() => {
    if (previousEnabled === undefined) delete process.env.AUTHORED_VNEXT_RENDERER_ENABLED;
    else process.env.AUTHORED_VNEXT_RENDERER_ENABLED = previousEnabled;
  });
  const chromeExecutable = await resolveAuthoredHybridChromeExecutable();
  if (!chromeExecutable) {
    if (process.env.CI === "true") assert.fail("CI must provide isolated authored Chrome");
    t.skip("Chrome/Chromium is unavailable");
    return;
  }
  const raw = `<!doctype html><html><head><style>html,body{margin:0;width:100%;height:100%;background:#123456;color:white}</style></head><body><h1>safe authored fixture</h1><img src="${PNG}" alt="pixel"></body></html>`;
  const direct = await runAuthoredHybridChrome({
    chromeExecutable,
    timeoutMs: 20_000,
    html: raw,
    dumpDom: true,
    screenshot: true,
  });
  assert.match(direct.serializedDom ?? "", /safe authored fixture/);
  assert.ok((direct.screenshotPng?.length ?? 0) > 100);
  assert.equal(direct.blockedRequestCount, 0);

  const completionToken = "trusted-fixture-token";
  const trusted = await runTrustedSelfContainedAuthoredHybridChrome({
    chromeExecutable,
    timeoutMs: 20_000,
    html: instrumentAuthoredHtml(raw, { completionToken }),
    domCompletionToken: completionToken,
    dumpDom: true,
    screenshot: false,
  });
  assert.match(trusted.serializedDom ?? "", new RegExp(completionToken));
  assert.equal(trusted.blockedRequestCount, 0);
});
