import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { createHash } from "node:crypto";
import fs from "node:fs/promises";
import http from "node:http";
import os from "node:os";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

import { readPptxArchive } from "../authored-hybrid/pptx-archive.ts";
import { resolveAuthoredHybridChromeExecutable } from "../authored-hybrid/chrome-runtime-discovery.ts";
import { executeExportAtProductionBoundary } from "../presentation-export-boundary.ts";
import { PRESENTATION_SOURCE_SHA256 } from "../presentation-export-strategy.ts";
import { PRESENTATION_DRIVER_REGISTRY } from "../presentation-profile.ts";
import { renderTemplateV2GeneralPresentationHtml } from "../template-v2-general-renderer.mjs";
import {
  compareSlidePngs,
  writeFidelityComparisonErrorArtifacts,
  writeFidelityFailureArtifacts,
} from "./image-compare.mjs";
import { renderPptxToPngPages, resolvePptxRenderTools } from "./pptx-render.mjs";
import { renderTemplateV2SourceHtml } from "./source-render.mjs";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = path.resolve(HERE, "..", "..", "..", "..");
const EXPORT_ROOT =
  process.env.EXPORT_PACKAGE_ROOT?.trim() ||
  path.join(REPO_ROOT, "presentation-export");
const NEXT_ROOT = path.join(REPO_ROOT, "servers", "nextjs");
const FIXTURE_ROOT = path.join(HERE, "fixtures", "template-v2-general");
const TEMPLATE_PACKAGE_IDS = Object.freeze([
  "dynamic",
  "editorial",
  "executive",
  "general",
  "modern",
  "momentum",
  "standard",
  "swift",
]);

function attachTemplateV2Runtime(presentation) {
  const driver = PRESENTATION_DRIVER_REGISTRY["template-v2"];
  presentation.profile = {
    schema_version: 1,
    driver_id: "template-v2",
    content_model: driver.content_model,
    generation_strategy: driver.generation_strategy,
    style_source: "imported",
    generation_mode: "standard",
  };
  presentation.capabilities = {
    schema_version: 1,
    driver_id: "template-v2",
    renderer_adapter_id: driver.renderer_adapter_id,
    editor_adapter_id: driver.editor_adapter_id,
    editor: driver.editor,
    agent_tool_capability: driver.agent_tool_capability,
    export_adapter_id: driver.export_adapter_id,
    export_editable_pptx: driver.export_editable_pptx,
  };
  const chartIds = new Set();
  const chartSnapshots = [];
  const visit = (value, slideId, slideIndex) => {
    if (Array.isArray(value)) {
      value.forEach((child) => visit(child, slideId, slideIndex));
      return;
    }
    if (!value || typeof value !== "object") return;
    if (value.type === "chart" || value.kind === "chart") {
      const chartId =
        [value.id, value.element_id, value.object_id, value.chart_id].find(
          (candidate) => typeof candidate === "string" && candidate
        ) ?? `slide-${slideIndex + 1}-chart-${chartIds.size + 1}`;
      if (!chartIds.has(chartId)) {
        chartIds.add(chartId);
        chartSnapshots.push({
          chart_id: chartId,
          slide_id: slideId,
          mime_type: "image/png",
          sha256: "c".repeat(64),
          asset_id: `template-v2-fidelity-${chartIds.size}`,
        });
      }
    }
    Object.values(value).forEach((child) => visit(child, slideId, slideIndex));
  };
  presentation.slides.forEach((slide, slideIndex) =>
    visit(slide, slide.id ?? `slide-${slideIndex + 1}`, slideIndex)
  );
  presentation.render_dependencies = { chart_snapshots: chartSnapshots };
  return presentation;
}

function packageAssetMediaType(filePath) {
  if (/\.png$/i.test(filePath)) return "image/png";
  if (/\.jpe?g$/i.test(filePath)) return "image/jpeg";
  if (/\.svg$/i.test(filePath)) return "image/svg+xml";
  throw new Error(`unsupported TemplatePackage smoke asset: ${filePath}`);
}

async function inlinePackageAssets(value, templateId) {
  if (typeof value === "string" && value.startsWith("static/")) {
    if (/\.(?:ttf|otf)$/i.test(value)) return value;
    const filePath = path.join(REPO_ROOT, "templates", templateId, ...value.split("/"));
    const bytes = await fs.readFile(filePath);
    return `data:${packageAssetMediaType(filePath)};base64,${bytes.toString("base64")}`;
  }
  if (Array.isArray(value)) {
    return Promise.all(value.map((child) => inlinePackageAssets(child, templateId)));
  }
  if (value && typeof value === "object") {
    return Object.fromEntries(
      await Promise.all(
        Object.entries(value).map(async ([key, child]) => [
          key,
          await inlinePackageAssets(child, templateId),
        ])
      )
    );
  }
  return value;
}

async function loadTemplatePackageSmoke() {
  const slides = [];
  const expectedText = [];
  for (const templateId of TEMPLATE_PACKAGE_IDS) {
    const packageTemplate = JSON.parse(
      await fs.readFile(
        path.join(REPO_ROOT, "templates", templateId, "template.json"),
        "utf8"
      )
    );
    let selected;
    for (const layout of packageTemplate.layouts) {
      const candidate = {
        version: "v2-standard",
        mode: "template",
        slides: [{ id: `${templateId}-${layout.id}`, ui: layout }],
      };
      try {
        renderTemplateV2GeneralPresentationHtml(candidate);
        selected = layout;
        break;
      } catch {
        // The native driver is deliberately capability-gated. Continue until
        // the package's production-general-compatible layout is found.
      }
    }
    assert.ok(selected, `${templateId} has no production-general-compatible layout`);
    const sentinel = `Template package smoke ${templateId}`;
    const ui = await inlinePackageAssets(structuredClone(selected), templateId);
    ui.components.push({
      id: `package-smoke-${templateId}`,
      description: "Structural PPTX smoke identity",
      position: { x: 0, y: 0 },
      elements: [
        {
          type: "text",
          position: { x: 4, y: 700 },
          size: { width: 420, height: 16 },
          runs: [{ text: sentinel }],
          font: { size: 8, family: "Arial", color: "#000000" },
          decorative: false,
          name: `package_smoke_${templateId}`,
          min_length: 1,
          max_length: 80,
        },
      ],
    });
    slides.push({ id: `${templateId}-${selected.id}`, ui });
    expectedText.push(sentinel);
  }
  return {
    fixture: {
      id: "template-packages",
      expectedText,
      structuralOnly: true,
      templateIdentity: { version: "v2-standard", mode: "template" },
    },
    presentation: attachTemplateV2Runtime({
      id: "template-packages",
      revision: 1,
      lifecycle_status: "published",
      version: "v2-standard",
      mode: "template",
      template_id: "verified-template-packages",
      slides,
    }),
  };
}

async function loadCorpus() {
  const manifest = JSON.parse(await fs.readFile(path.join(FIXTURE_ROOT, "manifest.json"), "utf8"));
  const presentations = new Map();
  for (const fixture of manifest.cases) {
    const presentation = JSON.parse(await fs.readFile(path.join(FIXTURE_ROOT, fixture.directory, "template.v2.json"), "utf8"));
    presentation.id = fixture.id;
    presentation.revision = 1;
    presentation.lifecycle_status = "published";
    presentations.set(fixture.id, attachTemplateV2Runtime(presentation));
  }
  const packageSmoke = await loadTemplatePackageSmoke();
  manifest.cases.push(packageSmoke.fixture);
  presentations.set(packageSmoke.fixture.id, packageSmoke.presentation);
  return { manifest, presentations };
}

async function listen(server) {
  await new Promise((resolve, reject) => {
    server.once("error", reject);
    server.listen(0, "127.0.0.1", resolve);
  });
  const address = server.address();
  assert.ok(address && typeof address === "object");
  return address.port;
}

async function close(server) {
  if (!server.listening) return;
  await new Promise((resolve) => server.close(resolve));
}

async function availablePort() {
  const reservation = http.createServer();
  const port = await listen(reservation);
  await close(reservation);
  return port;
}

async function waitForNext(baseUrl, fixtureId, logs) {
  const deadline = Date.now() + 90_000;
  let lastError;
  while (Date.now() < deadline) {
    try {
      const response = await fetch(`${baseUrl}/pdf-maker?id=${encodeURIComponent(fixtureId)}`, {
        headers: { Cookie: "presenton_session=fidelity" },
      });
      if (response.ok) return;
      lastError = new Error(`Next readiness returned ${response.status}`);
    } catch (error) {
      lastError = error;
    }
    await new Promise((resolve) => setTimeout(resolve, 250));
  }
  throw new Error(`Next dev server did not become ready: ${lastError}\n${logs.join("")}`);
}

async function stopProcess(child) {
  if (child.exitCode !== null || child.signalCode !== null) return;
  const closed = new Promise((resolve) => child.once("close", resolve));
  child.kill("SIGTERM");
  const stopped = await Promise.race([
    closed.then(() => true),
    new Promise((resolve) => setTimeout(() => resolve(false), 10_000)),
  ]);
  if (stopped || !child.pid) return;
  if (process.platform === "win32") {
    const killer = spawn("taskkill", ["/pid", String(child.pid), "/T", "/F"], {
      windowsHide: true,
      stdio: "ignore",
    });
    await new Promise((resolve) => killer.once("close", resolve));
  } else {
    child.kill("SIGKILL");
  }
}

async function startNextDev({ port, fastApiUrl, appData, tempRoot, nextDistDir, chrome, fixtureId }) {
  const nextBin = path.join(NEXT_ROOT, "node_modules", "next", "dist", "bin", "next");
  const nextArgs = [
    nextBin,
    "dev",
    "--hostname",
    "127.0.0.1",
    "--port",
    String(port),
  ];
  if (process.env.PRESENTON_TEST_USE_WEBPACK === "1") {
    nextArgs.splice(2, 0, "--webpack");
  }
  const converter = path.join(
    EXPORT_ROOT,
    "py",
    `convert-${process.platform}-${process.arch}${process.platform === "win32" ? ".exe" : ""}`
  );
  await fs.access(converter);
  const logs = [];
  const child = spawn(
    process.execPath,
    nextArgs,
    {
      cwd: NEXT_ROOT,
      env: {
        ...process.env,
        DISABLE_AUTH: "true",
        ENABLE_TEMPLATE_V2: "false",
        NEXT_PUBLIC_TEMPLATE_V2_STUDIO_ENABLED: "false",
        FAST_API_INTERNAL_URL: fastApiUrl,
        NEXT_PUBLIC_FAST_API: fastApiUrl,
        NEXT_PUBLIC_URL: `http://127.0.0.1:${port}`,
        APP_DATA_DIRECTORY: appData,
        TEMP_DIRECTORY: tempRoot,
        EXPORT_PACKAGE_ROOT: EXPORT_ROOT,
        PRESENTON_APP_ROOT: REPO_ROOT,
        PRESENTON_TEST_NEXT_DIST_DIR: nextDistDir,
        BUILT_PYTHON_MODULE_PATH: converter,
        PUPPETEER_EXECUTABLE_PATH: chrome,
      },
      windowsHide: true,
      stdio: ["ignore", "pipe", "pipe"],
    }
  );
  const append = (chunk) => {
    logs.push(chunk.toString());
    if (logs.length > 200) logs.shift();
  };
  child.stdout.on("data", append);
  child.stderr.on("data", append);
  const earlyExit = new Promise((_, reject) => {
    child.once("error", reject);
    child.once("close", (code) => {
      reject(new Error(`Next dev server exited ${code}\n${logs.join("")}`));
    });
  });
  try {
    await Promise.race([
      waitForNext(`http://127.0.0.1:${port}`, fixtureId, logs),
      earlyExit,
    ]);
  } catch (error) {
    if (child.pid) await stopProcess(child);
    throw error;
  }
  return { child, logs };
}

test("Template V2 persisted JSON always crosses the production strategy boundary through the general renderer", async () => {
  const { manifest, presentations } = await loadCorpus();
  assert.equal(manifest.contract, "template-v2-general-export-fidelity-v1");
  for (const fixture of manifest.cases) {
    let generalCalls = 0;
    let hybridCalls = 0;
    const result = await executeExportAtProductionBoundary(
      { format: "pptx", presentationId: fixture.id, cookieHeader: "fidelity-cookie" },
      {
        async fetchPresentation(presentationId, cookieHeader) {
          assert.equal(presentationId, fixture.id);
          assert.equal(cookieHeader, "fidelity-cookie");
          const presentation = presentations.get(presentationId);
          if (!presentation[PRESENTATION_SOURCE_SHA256]) {
            Object.defineProperty(presentation, PRESENTATION_SOURCE_SHA256, {
              value: createHash("sha256")
                .update(JSON.stringify(presentation))
                .digest("hex"),
            });
          }
          return presentation;
        },
        registry: {
          async general(params) {
            generalCalls += 1;
            const html = renderTemplateV2GeneralPresentationHtml(presentations.get(params.presentationId));
            assert.match(html, /id="presentation-slides-wrapper"/);
            assert.match(html, /data-layout="template-v2-general"/);
            for (const expected of fixture.expectedText) assert.ok(html.includes(expected));
            return { path: `memory://${params.presentationId}.pptx` };
          },
          async hybrid() {
            hybridCalls += 1;
            throw new Error("Template V2 must never enter authored-hybrid");
          },
        },
      }
    );
    assert.equal(result.path, `memory://${fixture.id}.pptx`);
    assert.equal(generalCalls, 1);
    assert.equal(hybridCalls, 0);
  }
});

// A cold Next compilation plus three real LibreOffice conversions regularly
// crosses four minutes on Windows. Keep the tighter CI budget elsewhere while
// leaving enough time for the test's mandatory child-process cleanup.
const EXPORT_E2E_TIMEOUT_MS = process.platform === "win32" ? 360_000 : 240_000;

test("actual API route renders Template V2 through /pdf-maker and presentation-export v0.4.3", { timeout: EXPORT_E2E_TIMEOUT_MS }, async (t) => {
  const [{ manifest, presentations }, chrome, installedVersion] = await Promise.all([
    loadCorpus(),
    resolveAuthoredHybridChromeExecutable(),
    fs.readFile(path.join(EXPORT_ROOT, ".installed-version"), "utf8").then((value) => value.trim()),
  ]);
  assert.equal(installedVersion, "v0.4.3");
  if (!chrome) {
    if (process.env.CI === "true") assert.fail("CI must provide Chrome for Template V2 structural export");
    t.skip("Chrome unavailable; dependency-free production boundary coverage passed separately");
    return;
  }

  const renderedHtml = new Map([...presentations].map(
    ([id, presentation]) => [id, renderTemplateV2GeneralPresentationHtml(presentation)]
  ));
  const fastApi = http.createServer((request, response) => {
    const url = new URL(request.url, "http://127.0.0.1");
    if (url.pathname === "/api/v1/auth/status") {
      if (!request.headers.cookie) {
        response.writeHead(401).end("session required");
        return;
      }
      response.writeHead(200, { "content-type": "application/json; charset=utf-8" })
        .end(JSON.stringify({
          configured: true,
          authenticated: true,
          username: "fidelity",
          user_id: null,
          role: "admin",
        }));
      return;
    }
    const prefix = "/api/v1/ppt/presentation/";
    if (!url.pathname.startsWith(prefix)) {
      response.writeHead(404).end("unknown fake FastAPI route");
      return;
    }
    if (!request.headers.cookie) {
      response.writeHead(401).end("session required");
      return;
    }
    const id = decodeURIComponent(url.pathname.slice(prefix.length));
    const presentation = presentations.get(id);
    if (!presentation) {
      response.writeHead(404).end("unknown Template V2 fidelity fixture");
      return;
    }
    response.writeHead(200, { "content-type": "application/json; charset=utf-8" })
      .end(JSON.stringify(presentation));
  });
  const tempRoot = await fs.mkdtemp(path.join(os.tmpdir(), "template-v2-fidelity-"));
  const nextDistDir = `.next-template-v2-fidelity-${path.basename(tempRoot)}`;
  const appData = path.join(tempRoot, "app_data");
  const exported = new Map();
  const generatedConfigPaths = [
    path.join(NEXT_ROOT, "next-env.d.ts"),
    path.join(NEXT_ROOT, "tsconfig.json"),
  ];
  const generatedConfigSnapshots = await Promise.all(
    generatedConfigPaths.map((filePath) => fs.readFile(filePath))
  );
  let nextServer;
  await fs.mkdir(appData, { recursive: true });
  try {
    const fastApiPort = await listen(fastApi);
    const nextPort = await availablePort();
    nextServer = await startNextDev({
      port: nextPort,
      fastApiUrl: `http://127.0.0.1:${fastApiPort}`,
      appData,
      tempRoot,
      nextDistDir,
      chrome,
      fixtureId: manifest.cases[0].id,
    });
    const nextBaseUrl = `http://127.0.0.1:${nextPort}`;
    for (const fixture of manifest.cases) {
      const routeResponse = await fetch(`${nextBaseUrl}/api/export-presentation`, {
        method: "POST",
        headers: {
          "content-type": "application/json",
          Cookie: "presenton_session=fidelity",
        },
        body: JSON.stringify({
          format: "pptx",
          id: fixture.id,
          title: `template-v2-${fixture.id}`,
          pptxMode: "hybrid",
        }),
      });
      const routeBody = await routeResponse.json();
      assert.equal(
        routeResponse.status,
        200,
        `actual export route failed: ${JSON.stringify(routeBody)}\n${nextServer.logs.join("")}`
      );
      assert.equal(routeBody.success, true);
      assert.match(routeBody.path, /^\/api\/export-presentation\/file\?name=/);
      const downloadResponse = await fetch(`${nextBaseUrl}${routeBody.path}`, {
        headers: { Cookie: "presenton_session=fidelity" },
      });
      assert.equal(downloadResponse.status, 200);
      const pptx = Buffer.from(await downloadResponse.arrayBuffer());
      const pptxPath = path.join(tempRoot, `${fixture.id}.pptx`);
      await fs.writeFile(pptxPath, pptx);
      const archive = readPptxArchive(pptx);
      const xml = [...archive.entries()]
        .filter(([name]) => /^ppt\/slides\/slide\d+\.xml$/.test(name))
        .map(([, value]) => value.toString("utf8"))
        .join("\n");
      for (const expected of fixture.expectedText) {
        assert.ok(xml.includes(expected), `${fixture.id} lost editable text: ${expected}`);
      }
      exported.set(fixture.id, pptxPath);
    }

    await t.test("visual comparison (LibreOffice + Poppler)", async (visual) => {
      const tools = await resolvePptxRenderTools();
      if (!tools.soffice || !tools.pdftocairo) {
        const missing = [!tools.soffice && "LibreOffice/soffice", !tools.pdftocairo && "Poppler/pdftocairo"].filter(Boolean).join(", ");
        if (process.env.REQUIRE_TEMPLATE_V2_VISUAL === "1") {
          assert.fail(`required visual fidelity dependencies unavailable: ${missing}`);
        }
        visual.skip(`visual-only dependencies unavailable: ${missing}`);
        return;
      }
      // Every case is compared before anything fails. Stopping at the first
      // regression hides the others, so a shift that moves the whole corpus takes
      // one CI round trip per fixture to discover and gets retoleranced blind.
      const regressions = [];
      // GitHub's hosted macOS ARM runners have repeatedly needed just over the
      // normal 60-second cold-start ceiling. Keep production and other CI
      // platforms unchanged while retaining a finite infrastructure timeout.
      const sourceCaptureTimeoutMs =
        process.env.CI === "true" && process.platform === "darwin"
          ? 120_000
          : undefined;
      for (const fixture of manifest.cases) {
        if (fixture.structuralOnly) continue;
        const sourcePng = await renderTemplateV2SourceHtml(
          renderedHtml.get(fixture.id),
          {
            chromeExecutable: chrome,
            timeoutMs: sourceCaptureTimeoutMs,
          }
        );
        const [pptxPng] = await renderPptxToPngPages({
          pptxPath: exported.get(fixture.id),
          outputDirectory: path.join(tempRoot, "rendered", fixture.id),
          pageCount: 1,
          tools,
        });
        let comparison;
        try {
          comparison = await compareSlidePngs(sourcePng, pptxPng, {
            tolerances: { ...manifest.defaults, ...fixture.tolerances },
          });
        } catch (error) {
          const artifacts = await writeFidelityComparisonErrorArtifacts({
            outputDirectory: process.env.TEST_ARTIFACT_DIR || path.join(tempRoot, "artifacts"),
            label: fixture.id,
            sourcePng,
            pptxPng,
            error,
          });
          regressions.push(
            `${fixture.id} visual comparison error; artifacts: ${artifacts}; error: ${error instanceof Error ? error.message : String(error)}`
          );
          continue;
        }
        console.log(`fidelity ${fixture.id}: ${comparison.passed ? "pass" : "FAIL"} ${JSON.stringify(comparison.metrics)}`);
        if (!comparison.passed) {
          const artifacts = await writeFidelityFailureArtifacts({
            outputDirectory: process.env.TEST_ARTIFACT_DIR || path.join(tempRoot, "artifacts"),
            label: fixture.id,
            sourcePng,
            pptxPng,
            comparison,
          });
          regressions.push(`${fixture.id} visual fidelity regression; artifacts: ${artifacts}; metrics: ${JSON.stringify(comparison.metrics)}`);
        }
      }
      if (regressions.length) assert.fail(regressions.join("\n"));
    });
  } finally {
    if (nextServer) await stopProcess(nextServer.child);
    await close(fastApi);
    await Promise.all(
      generatedConfigPaths.map((filePath, index) =>
        fs.writeFile(filePath, generatedConfigSnapshots[index])
      )
    );
    await fs.rm(path.join(NEXT_ROOT, nextDistDir), { recursive: true, force: true });
    await fs.rm(tempRoot, { recursive: true, force: true });
  }
});
