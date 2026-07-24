import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import fs from "node:fs/promises";
import http from "node:http";
import os from "node:os";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

import { readPptxArchive } from "./authored-hybrid/pptx-archive.ts";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = path.resolve(HERE, "..", "..", "..");
const EXPORT_ROOT = path.join(REPO_ROOT, "presentation-export");

function chromeExecutable() {
  const candidates = [
    process.env.PUPPETEER_EXECUTABLE_PATH,
    process.env.CHROME_PATH,
    path.join(
      process.env.ProgramFiles || "C:\\Program Files",
      "Google",
      "Chrome",
      "Application",
      "chrome.exe"
    ),
    path.join(
      process.env["ProgramFiles(x86)"] || "C:\\Program Files (x86)",
      "Microsoft",
      "Edge",
      "Application",
      "msedge.exe"
    ),
  ].filter(Boolean);
  return candidates;
}

async function firstExisting(paths) {
  for (const candidate of paths) {
    try {
      await fs.access(candidate);
      return candidate;
    } catch {
      // Continue probing.
    }
  }
  return undefined;
}

function runExporter(entrypoint, taskPath, env) {
  return new Promise((resolve, reject) => {
    const child = spawn(process.execPath, [entrypoint, taskPath], {
      cwd: REPO_ROOT,
      env: { ...process.env, ...env },
      stdio: ["ignore", "pipe", "pipe"],
    });
    const stdout = [];
    const stderr = [];
    child.stdout.on("data", (chunk) => stdout.push(chunk));
    child.stderr.on("data", (chunk) => stderr.push(chunk));
    child.once("error", reject);
    child.once("close", (code) => {
      if (code === 0) {
        resolve();
        return;
      }
      reject(
        new Error(
          `presentation-export exited ${code}: ${Buffer.concat(stderr)} ${Buffer.concat(stdout)}`
        )
      );
    });
  });
}

test("v0.4.2 general exporter converts a representative Template V2 render", async (t) => {
  const chrome = await firstExisting(chromeExecutable());
  if (!chrome) {
    if (process.env.CI === "true") assert.fail("CI must provide Chrome/Chromium");
    t.skip("Chrome/Chromium is unavailable");
    return;
  }

  const tempRoot = await fs.mkdtemp(
    path.join(os.tmpdir(), "template-v2-general-export-")
  );
  const appData = path.join(tempRoot, "app_data");
  await fs.mkdir(appData, { recursive: true });

  const html = `<!doctype html>
    <html><head><meta charset="utf-8"><style>
      html,body{margin:0} #presentation-slides-wrapper,.main-slide,.slide-export-inner,
      .slide-scale-frame,.slide-canvas{
        width:1280px;height:720px;margin:0;overflow:hidden
      }
      .slide-canvas{position:relative}
    </style></head><body>
      <div id="presentation-slides-wrapper">
        <div class="slides-export-stack">
          <div id="slide-1" class="main-slide" data-speaker-note="">
            <div class="slide-export-inner" data-layout="template-v2">
              <div class="slide-scale-frame">
                <div class="slide-canvas">
                  <div style="position:absolute;left:96px;top:72px;width:1088px;height:150px;
                    font:700 42px Arial;color:#123b72">Template V2 General Export</div>
                  <div style="position:absolute;left:96px;top:260px;width:560px;height:220px;
                    border:4px solid #2f80ed;background:#eaf3ff;font:24px Arial">
                    Editable native text and rectangle
                  </div>
                </div>
              </div>
            </div>
          </div>
        </div>
      </div>
    </body></html>`;
  const server = http.createServer((_request, response) => {
    response.writeHead(200, { "content-type": "text/html; charset=utf-8" });
    response.end(html);
  });

  try {
    await new Promise((resolve, reject) => {
      server.once("error", reject);
      server.listen(0, "127.0.0.1", resolve);
    });
    const address = server.address();
    assert.ok(address && typeof address === "object");

    const taskPath = path.join(tempRoot, "export_task.json");
    await fs.writeFile(
      taskPath,
      JSON.stringify({
        type: "export",
        url: `http://127.0.0.1:${address.port}/pdf-maker?id=template-v2-smoke`,
        format: "pptx",
        title: "template-v2-general-smoke",
      })
    );

    const entrypoint = path.join(EXPORT_ROOT, "index.cjs");
    const converter = path.join(
      EXPORT_ROOT,
      "py",
      `convert-${process.platform}-${process.arch}${
        process.platform === "win32" ? ".exe" : ""
      }`
    );
    await runExporter(entrypoint, taskPath, {
      APP_DATA_DIRECTORY: appData,
      TEMP_DIRECTORY: tempRoot,
      BUILT_PYTHON_MODULE_PATH: converter,
      PUPPETEER_EXECUTABLE_PATH: chrome,
    });

    const response = JSON.parse(
      await fs.readFile(taskPath.replace(/\.json$/, ".response.json"), "utf8")
    );
    const outputPath =
      typeof response.path === "string"
        ? response.path
        : typeof response.url === "string" && response.url.startsWith("file:")
          ? fileURLToPath(response.url)
          : typeof response.url === "string" &&
              response.url.startsWith("/app_data/")
            ? path.join(appData, response.url.slice("/app_data/".length))
            : undefined;
    assert.equal(typeof outputPath, "string");
    const pptx = await fs.readFile(outputPath);
    assert.equal(pptx.subarray(0, 2).toString("ascii"), "PK");
    const archive = readPptxArchive(pptx);
    const slideEntry = [...archive.keys()].find((name) =>
      /^ppt\/slides\/slide\d+\.xml$/.test(name)
    );
    assert.ok(
      slideEntry,
      `generated archive has no slide XML: ${[...archive.keys()].slice(0, 20)}`
    );
    const slideXml = archive.get(slideEntry).toString("utf8");
    assert.match(slideXml, /Template V2 General Export/);
    assert.match(slideXml, /Editable native text and rectangle/);
  } finally {
    await new Promise((resolve) => server.close(resolve));
    await fs.rm(tempRoot, { recursive: true, force: true });
  }
});
