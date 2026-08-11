import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { spawn } from "node:child_process";
import { existsSync } from "node:fs";
import { createServer } from "node:http";
import { mkdir, mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

import sharp from "sharp";

import { loadTemplateV2HtmlRenderer } from "./template-v2-json-to-html.test-harness.mjs";

const REPOSITORY_ROOT = path.resolve(
  path.dirname(fileURLToPath(import.meta.url)),
  "../../..",
);
const TEMPLATE_ROOT = path.join(REPOSITORY_ROOT, "templates", "editorial");
const NEXT_PUBLIC_ROOT = path.join(REPOSITORY_ROOT, "servers", "nextjs", "public");
const TEMPLATE = JSON.parse(
  await readFile(path.join(TEMPLATE_ROOT, "template.json"), "utf8"),
);
process.env.NEXT_PUBLIC_FAST_API = "http://127.0.0.1:43127";
const RENDERER = await loadTemplateV2HtmlRenderer();
const GOLDEN_RECEIPT = JSON.parse(
  await readFile(
    path.join(
      REPOSITORY_ROOT,
      "templates",
      "manifests",
      "editorial.browser-golden.v1.json",
    ),
    "utf8",
  ),
);
const CAPTURE_EVIDENCE_PATH = path.join(
  REPOSITORY_ROOT,
  "templates",
  "manifests",
  "editorial.browser-golden.capture.v1.json",
);

const TRANSPARENT_PNG = Buffer.from(
  "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M/wHwAF/gL+XwX9WQAAAABJRU5ErkJggg==",
  "base64",
);
const TRANSPARENT_SVG = Buffer.from(
  '<svg xmlns="http://www.w3.org/2000/svg" width="1" height="1" viewBox="0 0 1 1"/>',
);

function layoutHtml(index) {
  const layout = TEMPLATE.layouts[index];
  assert.ok(layout, `unknown editorial layout index: ${index}`);
  const html = RENDERER.templateV2UiToHtml(
    { components: layout.components },
    { fonts: TEMPLATE.fonts, width: 1280, height: 720 },
  );
  return html.replace(
    "<head>",
    `<head><meta http-equiv="Content-Security-Policy" content="default-src 'none'; img-src data: http://127.0.0.1:43127; font-src data: http://127.0.0.1:43127; style-src 'unsafe-inline'; script-src 'unsafe-inline' http://127.0.0.1:43127">`,
  );
}

function chartReadinessFixture(mode) {
  const html = RENDERER.templateV2UiToHtml({
    elements: [{
      type: "chart",
      position: { x: 0, y: 0 },
      size: { width: 320, height: 180 },
      chart_type: "bar",
      categories: ["a"],
      series: [{ name: "s", values: [1] }],
    }],
  });
  const stateScript = mode === "error"
    ? `<script>window.__PRESENTON_JSON_CHARTS__={status:'error'};document.documentElement.dataset.templateV2ChartState='error';window.dispatchEvent(new Event('template-v2-chart-state'))</script>`
    : `<script>window.__PRESENTON_JSON_CHARTS__={status:'pending'};document.documentElement.dataset.templateV2ChartState='pending'</script>`;
  const replaced = html.replace(
    /<script>\s*\(function\(\)\{\s*var state=window\.__PRESENTON_JSON_CHARTS__=[\s\S]*?<\/script>/,
    stateScript,
  );
  assert.notEqual(replaced, html, "chart bootstrap fixture replacement must be exact");
  return mode === "timeout"
    ? replaced.replace("chartTimeoutMs=10000", "chartTimeoutMs=100")
    : replaced;
}

function goldenIndexHtml() {
  const frames = TEMPLATE.layouts.map(
    (layout, index) =>
      `<iframe title=${JSON.stringify(layout.id)} src="/layout/${index}"></iframe>`,
  ).join("");
  return `<!doctype html><html><head><meta charset="utf-8"><style>*{box-sizing:border-box}html,body{width:1280px;margin:0;padding:0;background:#fff}iframe{display:block;width:1280px;height:720px;border:0;margin:0;padding:0;overflow:hidden}</style></head><body>${frames}</body></html>`;
}

function contentType(file) {
  if (file.endsWith(".png")) return "image/png";
  if (file.endsWith(".svg")) return "image/svg+xml";
  if (file.endsWith(".ttf")) return "font/ttf";
  return "application/octet-stream";
}

function createGoldenServer() {
  const html = goldenIndexHtml();
  return createServer(async (request, response) => {
    try {
      const url = new URL(request.url ?? "/", "http://127.0.0.1");
      if (url.pathname === "/" || url.pathname === "/index.html") {
        response.writeHead(200, { "content-type": "text/html; charset=utf-8" });
        response.end(html);
        return;
      }
      if (url.pathname === "/health") {
        response.writeHead(200, { "content-type": "application/json" });
        response.end(JSON.stringify({ status: "ok", layouts: TEMPLATE.layouts.length }));
        return;
      }
      const layoutMatch = /^\/layout\/(\d+)$/.exec(url.pathname);
      if (layoutMatch) {
        const layout = layoutHtml(Number(layoutMatch[1]));
        response.writeHead(200, { "content-type": "text/html; charset=utf-8" });
        response.end(layout);
        return;
      }
      if (url.pathname === "/fixture/required-image") {
        const fixture = RENDERER.templateV2UiToHtml({
          elements: [{
            type: "image",
            position: { x: 0, y: 0 },
            size: { width: 100, height: 100 },
            data: "static/definitely-missing-required.png",
          }],
        });
        response.writeHead(200, { "content-type": "text/html; charset=utf-8" });
        response.end(fixture);
        return;
      }
      if (url.pathname === "/fixture/optional-image") {
        const fixture = RENDERER.templateV2UiToHtml({
          elements: [{
            type: "image",
            position: { x: 0, y: 0 },
            size: { width: 100, height: 100 },
            data: "static/definitely-missing-optional.png",
            resourcePolicy: "optional",
          }],
        });
        response.writeHead(200, { "content-type": "text/html; charset=utf-8" });
        response.end(fixture);
        return;
      }
      if (url.pathname === "/fixture/placeholder-image") {
        const fixture = RENDERER.templateV2UiToHtml({
          elements: [{
            type: "image",
            position: { x: 0, y: 0 },
            size: { width: 100, height: 100 },
            data: "static/placeholder.png",
          }],
        });
        response.writeHead(200, { "content-type": "text/html; charset=utf-8" });
        response.end(fixture);
        return;
      }
      if (url.pathname === "/fixture/required-font") {
        const fixture = RENDERER.templateV2UiToHtml(
          { components: [TEMPLATE.layouts[0].components[0]] },
          { fonts: { "Missing Required": "static/definitely-missing-required.ttf" } },
        );
        response.writeHead(200, { "content-type": "text/html; charset=utf-8" });
        response.end(fixture);
        return;
      }
      if (url.pathname === "/fixture/required-font-stylesheet") {
        const fixture = RENDERER.templateV2UiToHtml(
          { components: [TEMPLATE.layouts[0].components[0]] },
          { fonts: { "Missing Stylesheet": "static/definitely-missing-required.css" } },
        );
        response.writeHead(200, { "content-type": "text/html; charset=utf-8" });
        response.end(fixture);
        return;
      }
      if (url.pathname === "/fixture/optional-font-stylesheet") {
        const fixture = RENDERER.templateV2UiToHtml(
          { components: [TEMPLATE.layouts[0].components[0]] },
          {
            fonts: {
              "Missing Optional Stylesheet": {
                url: "static/definitely-missing-optional.css",
                resourcePolicy: "optional",
              },
            },
          },
        );
        response.writeHead(200, { "content-type": "text/html; charset=utf-8" });
        response.end(fixture);
        return;
      }
      if (url.pathname === "/fixture/chart-error") {
        response.writeHead(200, { "content-type": "text/html; charset=utf-8" });
        response.end(chartReadinessFixture("error"));
        return;
      }
      if (url.pathname === "/fixture/chart-timeout") {
        response.writeHead(200, { "content-type": "text/html; charset=utf-8" });
        response.end(chartReadinessFixture("timeout"));
        return;
      }
      const relative = decodeURIComponent(url.pathname).replace(/^\/+/, "");
      if (relative === "static/images/replaceable_template_image.png") {
        response.writeHead(200, { "content-type": "image/png" });
        response.end(TRANSPARENT_PNG);
        return;
      }
      if (relative === "static/icons/placeholder.svg") {
        response.writeHead(200, { "content-type": "image/svg+xml" });
        response.end(TRANSPARENT_SVG);
        return;
      }
      if (relative.startsWith("vendor/")) {
        const vendorAsset = path.resolve(NEXT_PUBLIC_ROOT, relative);
        if (path.relative(NEXT_PUBLIC_ROOT, vendorAsset).startsWith("..")) {
          response.writeHead(403).end();
          return;
        }
        const bytes = await readFile(vendorAsset);
        response.writeHead(200, { "content-type": "text/javascript; charset=utf-8" });
        response.end(bytes);
        return;
      }
      const asset = path.resolve(TEMPLATE_ROOT, relative);
      if (path.relative(TEMPLATE_ROOT, asset).startsWith("..")) {
        response.writeHead(403).end();
        return;
      }
      const bytes = await readFile(asset);
      response.writeHead(200, {
        "content-type": contentType(asset),
        "cache-control": "no-store",
        "access-control-allow-origin": "*",
      });
      response.end(bytes);
    } catch {
      response.writeHead(404).end();
    }
  });
}

function sha256(bytes) {
  return createHash("sha256").update(bytes).digest("hex");
}

function chromeExecutable() {
  const candidates = [
    process.env.PUPPETEER_EXECUTABLE_PATH,
    process.env.CHROME_PATH,
    process.platform === "win32"
      ? "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe"
      : undefined,
    process.platform === "darwin"
      ? "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
      : "/usr/bin/google-chrome",
    "/usr/bin/chromium",
    "/usr/bin/chromium-browser",
  ].filter(Boolean);
  return candidates.find((candidate) => existsSync(candidate));
}

class CdpSession {
  constructor(socket) {
    this.socket = socket;
    this.sequence = 0;
    this.pending = new Map();
    this.listeners = new Map();
    socket.addEventListener("message", (event) => {
      const message = JSON.parse(event.data);
      if (message.id) {
        const pending = this.pending.get(message.id);
        if (!pending) return;
        this.pending.delete(message.id);
        if (message.error) pending.reject(new Error(message.error.message));
        else pending.resolve(message.result);
        return;
      }
      for (const listener of this.listeners.get(message.method) ?? []) {
        listener(message.params ?? {});
      }
    });
  }

  static async connect(url) {
    const socket = new WebSocket(url);
    await new Promise((resolve, reject) => {
      socket.addEventListener("open", resolve, { once: true });
      socket.addEventListener("error", reject, { once: true });
    });
    return new CdpSession(socket);
  }

  send(method, params = {}) {
    const id = ++this.sequence;
    const result = new Promise((resolve, reject) => {
      this.pending.set(id, { resolve, reject });
    });
    this.socket.send(JSON.stringify({ id, method, params }));
    return result;
  }

  on(method, listener) {
    const listeners = this.listeners.get(method) ?? [];
    listeners.push(listener);
    this.listeners.set(method, listeners);
  }

  once(method, timeoutMs = 15_000) {
    return new Promise((resolve, reject) => {
      const timeout = setTimeout(() => {
        this.listeners.set(
          method,
          (this.listeners.get(method) ?? []).filter((item) => item !== listener),
        );
        reject(new Error(`${method} timed out`));
      }, timeoutMs);
      const listener = (params) => {
        clearTimeout(timeout);
        this.listeners.set(
          method,
          (this.listeners.get(method) ?? []).filter((item) => item !== listener),
        );
        resolve(params);
      };
      this.on(method, listener);
    });
  }

  close() {
    this.socket.close();
  }
}

async function launchChrome() {
  const executable = chromeExecutable();
  assert.ok(executable, "a Chromium executable is required for the release golden gate");
  const profile = await mkdtemp(path.join(tmpdir(), "editorial-golden-chrome-"));
  const child = spawn(executable, [
    "--headless=new",
    "--disable-gpu",
    "--hide-scrollbars",
    "--force-device-scale-factor=1",
    "--remote-debugging-port=0",
    `--user-data-dir=${profile}`,
    "--window-size=1280,720",
    "--no-first-run",
    "--no-default-browser-check",
    "about:blank",
  ], { windowsHide: true, stdio: ["ignore", "ignore", "pipe"] });
  const browserEndpoint = await new Promise((resolve, reject) => {
    let stderr = "";
    const timeout = setTimeout(
      () => reject(new Error(`Chrome DevTools endpoint timed out: ${stderr}`)),
      15_000,
    );
    child.stderr.on("data", (chunk) => {
      stderr += chunk.toString();
      const match = /DevTools listening on (ws:\/\/[^\s]+)/.exec(stderr);
      if (match) {
        clearTimeout(timeout);
        resolve(match[1]);
      }
    });
    child.once("error", reject);
    child.once("exit", (code) => {
      if (code !== null) reject(new Error(`Chrome exited before DevTools was ready: ${code}`));
    });
  });
  const endpoint = new URL(browserEndpoint);
  const targets = await fetch(`http://${endpoint.host}/json/list`).then((response) => response.json());
  const target = targets.find((item) => item.type === "page");
  assert.ok(target?.webSocketDebuggerUrl, "Chrome did not expose a page target");
  const cdp = await CdpSession.connect(target.webSocketDebuggerUrl);
  await Promise.all([
    cdp.send("Page.enable"),
    cdp.send("Runtime.enable"),
    cdp.send("Network.enable"),
    cdp.send("Emulation.setDeviceMetricsOverride", {
      width: 1280,
      height: 720,
      deviceScaleFactor: 1,
      mobile: false,
    }),
  ]);
  return {
    cdp,
    executable,
    async close() {
      cdp.close();
      child.kill();
      await new Promise((resolve) => {
        if (child.exitCode !== null) resolve();
        else {
          child.once("exit", resolve);
          setTimeout(resolve, 3_000).unref();
        }
      });
      await rm(profile, { recursive: true, force: true });
    },
  };
}

async function evaluate(cdp, expression) {
  const result = await cdp.send("Runtime.evaluate", {
    expression,
    awaitPromise: true,
    returnByValue: true,
  });
  if (result.exceptionDetails) {
    throw new Error(result.exceptionDetails.text ?? "browser evaluation failed");
  }
  return result.result.value;
}

async function capturePage(browser, url) {
  const requests = [];
  const exceptions = [];
  browser.cdp.on("Network.requestWillBeSent", ({ request }) => requests.push(request.url));
  browser.cdp.on("Runtime.exceptionThrown", ({ exceptionDetails }) => {
    exceptions.push(exceptionDetails.text ?? "browser exception");
  });
  const loaded = browser.cdp.once("Page.loadEventFired");
  await browser.cdp.send("Page.navigate", { url });
  await loaded;
  const deadline = Date.now() + 15_000;
  let state;
  while (Date.now() < deadline) {
    state = await evaluate(
      browser.cdp,
      `({ready:document.documentElement.dataset.templateV2RenderReady==='true',error:document.documentElement.dataset.templateV2ResourceError==='true',renderState:document.documentElement.dataset.templateV2RenderState||null,chartState:document.documentElement.dataset.templateV2ChartState||null,code:document.documentElement.dataset.templateV2ResourceErrorCode||null,failures:window.__presentonTemplateV2ResourceErrors||[]})`,
    );
    if (state.ready || state.error) break;
    await new Promise((resolve) => setTimeout(resolve, 25));
  }
  assert.ok(state?.ready || state?.error, `renderer readiness timed out for ${url}`);
  const stats = await evaluate(
    browser.cdp,
    `({imageCount:document.images.length,missingImages:Array.from(document.images).filter(image=>image.complete&&image.naturalWidth===0).length,chartCanvasCount:document.querySelectorAll('canvas[data-presenton-chart]').length,fonts:Array.from(document.fonts,face=>({family:face.family,status:face.status})),fontSetStatus:document.fonts?.status||'unsupported'})`,
  );
  const screenshot = await browser.cdp.send("Page.captureScreenshot", {
    format: "png",
    fromSurface: true,
    captureBeyondViewport: false,
  });
  return {
    bytes: Buffer.from(screenshot.data, "base64"),
    exceptions,
    requests,
    state,
    stats,
  };
}

async function comparePng(actualBytes, expectedBytes) {
  if (sha256(actualBytes) === sha256(expectedBytes)) {
    return { exact: true, badPixelRatio: 0, meanAbsoluteError: 0 };
  }
  const [actual, expected] = await Promise.all([
    sharp(actualBytes).ensureAlpha().raw().toBuffer({ resolveWithObject: true }),
    sharp(expectedBytes).ensureAlpha().raw().toBuffer({ resolveWithObject: true }),
  ]);
  assert.deepEqual(actual.info, expected.info);
  let badPixels = 0;
  let totalDelta = 0;
  for (let offset = 0; offset < actual.data.length; offset += 4) {
    let pixelDelta = 0;
    for (let channel = 0; channel < 4; channel += 1) {
      const delta = Math.abs(actual.data[offset + channel] - expected.data[offset + channel]);
      totalDelta += delta;
      pixelDelta = Math.max(pixelDelta, delta);
    }
    if (pixelDelta > 24) badPixels += 1;
  }
  const pixels = actual.info.width * actual.info.height;
  return {
    exact: false,
    badPixelRatio: badPixels / pixels,
    meanAbsoluteError: totalDelta / (actual.data.length * 255),
  };
}

async function isNonBlank(bytes) {
  const statistics = await sharp(bytes).stats();
  return statistics.channels.some((channel) => channel.max > channel.min);
}

async function writeProductionGoldens() {
  const server = createGoldenServer();
  await new Promise((resolve, reject) => {
    server.once("error", reject);
    server.listen(43127, "127.0.0.1", resolve);
  });
  const destinationRoot = path.join(
    manifestRoot,
    "editorial.browser-golden",
  );
  const browser = await launchChrome();
  try {
    await mkdir(destinationRoot, { recursive: true });
    for (const [index] of TEMPLATE.layouts.entries()) {
      const name = `${String(index + 1).padStart(2, "0")}-${TEMPLATE.layouts[index].id}.png`;
      const outputPath = path.join(destinationRoot, name);
      const capture = await capturePage(
        browser,
        `http://127.0.0.1:43127/layout/${index}`,
      );
      assert.equal(capture.state.error, false, JSON.stringify(capture.state));
      await writeFile(outputPath, capture.bytes);
    }
    process.stdout.write(`${JSON.stringify({
      renderer: "production-chromium/readiness-contract",
      layouts: TEMPLATE.layouts.length,
    })}\n`);
  } finally {
    await browser.close();
    await new Promise((resolve) => server.close(resolve));
  }
}

const manifestRoot = path.join(REPOSITORY_ROOT, "templates", "manifests");

if (process.argv.includes("--write-golden")) {
  await writeProductionGoldens();
} else if (process.argv.includes("--serve-golden")) {
  const port = Number(process.env.EDITORIAL_GOLDEN_PORT ?? "43127");
  const server = createGoldenServer();
  server.listen(port, "127.0.0.1", () => {
    process.stdout.write(`EDITORIAL_GOLDEN_URL=http://127.0.0.1:${port}/\n`);
  });
} else {
  test("editorial renderer captures and perceptually verifies all 24 committed browser goldens", async () => {
    assert.equal(TEMPLATE.layouts.length, 24);
    assert.equal(new Set(TEMPLATE.layouts.map((layout) => layout.id)).size, 24);
    assert.deepEqual(
      Object.values(TEMPLATE.fonts).filter((value) => /^(?:https?:)?\/\//i.test(value)),
      [],
    );
    const server = createGoldenServer();
    await new Promise((resolve, reject) => {
      server.once("error", reject);
      server.listen(43127, "127.0.0.1", resolve);
    });
    const browser = await launchChrome();
    const actual = {
      renderedLayoutCount: 0,
      nonBlankLayoutCount: 0,
      missingImages: 0,
      imageCount: 0,
      chartCanvasCount: 0,
      externalRequests: new Set(),
      localFontsLoaded: true,
      consoleErrors: [],
    };
    const observedScreenshots = [];
    try {
      for (const [index, layout] of TEMPLATE.layouts.entries()) {
        const capture = await capturePage(
          browser,
          `http://127.0.0.1:43127/layout/${index}`,
        );
        assert.equal(capture.state.error, false, JSON.stringify(capture.state));
        assert.equal(capture.state.ready, true);
        assert.equal(capture.state.renderState, "ready");
        actual.renderedLayoutCount += 1;
        actual.nonBlankLayoutCount += Number(await isNonBlank(capture.bytes));
        actual.missingImages += capture.stats.missingImages;
        actual.imageCount += capture.stats.imageCount;
        actual.chartCanvasCount += capture.stats.chartCanvasCount;
        assert.equal(
          capture.state.chartState,
          capture.stats.chartCanvasCount > 0 ? "ready" : null,
        );
        actual.localFontsLoaded &&=
          capture.stats.fontSetStatus === "loaded" &&
          capture.stats.fonts.every((font) => font.status === "loaded");
        actual.consoleErrors.push(...capture.exceptions);
        for (const request of capture.requests) {
          if (!request.startsWith("http://127.0.0.1:43127/") && !request.startsWith("data:")) {
            actual.externalRequests.add(request);
          }
        }

        const receiptScreenshot = GOLDEN_RECEIPT.screenshots[index];
        assert.equal(receiptScreenshot.layoutId, layout.id);
        const expected = await readFile(
          path.join(REPOSITORY_ROOT, receiptScreenshot.path),
        );
        assert.equal(sha256(expected), receiptScreenshot.sha256);
        const comparison = await comparePng(capture.bytes, expected);
        assert.ok(
          comparison.badPixelRatio <= 0.005 && comparison.meanAbsoluteError <= 0.001,
          `${layout.id} browser golden drift: ${JSON.stringify(comparison)}`,
        );
        observedScreenshots.push({
          layoutId: layout.id,
          capturedSha256: sha256(capture.bytes),
          goldenSha256: receiptScreenshot.sha256,
          ...comparison,
        });
      }

      const checks = {
        renderedLayoutCount: actual.renderedLayoutCount,
        nonBlankLayoutCount: actual.nonBlankLayoutCount,
        missingImages: actual.missingImages,
        imageCount: actual.imageCount,
        chartCanvasCount: actual.chartCanvasCount,
        externalRequests: [...actual.externalRequests].sort(),
        localFontsLoaded: actual.localFontsLoaded,
        consoleErrors: actual.consoleErrors,
        paintReadinessContract: "passed",
      };
      assert.deepEqual(checks, GOLDEN_RECEIPT.checks);
      const rendererBytes = await readFile(
        path.join(REPOSITORY_ROOT, "servers", "nextjs", "lib", "template-v2-json-to-html.ts"),
      );
      const entrypointBytes = await readFile(path.join(TEMPLATE_ROOT, "template.json"));
      const browserVersion = await browser.cdp.send("Browser.getVersion");
      const evidence = {
        schemaVersion: "presenton.template-browser-capture-evidence/v1",
        templateId: "editorial",
        rendererModuleSha256: sha256(rendererBytes),
        entrypointSha256: sha256(entrypointBytes),
        captureAdapter: "chromium-cdp/Page.captureScreenshot",
        browser: {
          product: browserVersion.product,
          revision: browserVersion.revision,
          protocolVersion: browserVersion.protocolVersion,
        },
        capturedAt: new Date().toISOString(),
        viewport: { width: 1280, height: 720, deviceScaleFactor: 1 },
        thresholds: { badPixelRatio: 0.005, meanAbsoluteError: 0.001 },
        screenshots: observedScreenshots,
        checks,
        status: "passed-browser-capture",
      };
      if (process.env.EDITORIAL_WRITE_CAPTURE_EVIDENCE === "1") {
        await writeFile(CAPTURE_EVIDENCE_PATH, `${JSON.stringify(evidence, null, 2)}\n`);
      } else {
        const committed = JSON.parse(await readFile(CAPTURE_EVIDENCE_PATH, "utf8"));
        assert.equal(committed.schemaVersion, evidence.schemaVersion);
        assert.equal(committed.templateId, evidence.templateId);
        assert.equal(committed.rendererModuleSha256, evidence.rendererModuleSha256);
        assert.equal(committed.entrypointSha256, evidence.entrypointSha256);
        assert.deepEqual(committed.viewport, evidence.viewport);
        assert.deepEqual(committed.thresholds, evidence.thresholds);
        assert.deepEqual(committed.checks, evidence.checks);
        assert.equal(committed.status, "passed-browser-capture");
        assert.deepEqual(
          committed.screenshots.map(({ layoutId, goldenSha256 }) => ({ layoutId, goldenSha256 })),
          evidence.screenshots.map(({ layoutId, goldenSha256 }) => ({ layoutId, goldenSha256 })),
        );
      }
    } finally {
      await browser.close();
      await new Promise((resolve) => server.close(resolve));
    }
  });

  test("raw optional claims and placeholder-looking names cannot bypass required resources", async () => {
    const server = createGoldenServer();
    await new Promise((resolve, reject) => {
      server.once("error", reject);
      server.listen(43127, "127.0.0.1", resolve);
    });
    const browser = await launchChrome();
    try {
      for (const fixture of [
        "required-image",
        "optional-image",
        "placeholder-image",
        "required-font",
        "required-font-stylesheet",
        "optional-font-stylesheet",
      ]) {
        const capture = await capturePage(browser, `http://127.0.0.1:43127/fixture/${fixture}`);
        assert.equal(capture.state.ready, false);
        assert.equal(capture.state.error, true);
        assert.equal(capture.state.renderState, "error");
        assert.equal(capture.state.code, "required-resource-failed");
        assert.ok(capture.state.failures.length > 0);
      }
    } finally {
      await browser.close();
      await new Promise((resolve) => server.close(resolve));
    }
  });

  test("chart error and timeout terminate the common HTML render readiness gate", async () => {
    const server = createGoldenServer();
    await new Promise((resolve, reject) => {
      server.once("error", reject);
      server.listen(43127, "127.0.0.1", resolve);
    });
    const browser = await launchChrome();
    try {
      for (const [fixture, code] of [
        ["chart-error", "chart-render-failed"],
        ["chart-timeout", "chart-render-timeout"],
      ]) {
        const capture = await capturePage(
          browser,
          `http://127.0.0.1:43127/fixture/${fixture}`,
        );
        assert.equal(capture.state.ready, false);
        assert.equal(capture.state.error, true);
        assert.equal(capture.state.renderState, "error");
        assert.equal(capture.state.code, code);
        assert.equal(
          capture.state.chartState,
          fixture === "chart-error" ? "error" : "pending",
        );
        assert.deepEqual(capture.state.failures, [{ kind: "chart", code }]);
      }
    } finally {
      await browser.close();
      await new Promise((resolve) => server.close(resolve));
    }
  });
}
