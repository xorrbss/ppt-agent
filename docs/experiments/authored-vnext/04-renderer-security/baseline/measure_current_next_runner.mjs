#!/usr/bin/env node
/** Local-only low-level probe for the current Next authored-hybrid Chrome runner. */

import fs from "node:fs/promises";
import http from "node:http";
import os from "node:os";
import path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = path.resolve(HERE, "../../../../..");
const NEXT_ROOT = path.join(REPO_ROOT, "servers", "nextjs");
const { default: sharp } = await import(
  pathToFileURL(path.join(NEXT_ROOT, "node_modules", "sharp", "dist", "index.mjs"))
);
const {
  resolveAuthoredHybridChromeExecutable,
  runAuthoredHybridChrome,
} = await import(
  pathToFileURL(path.join(NEXT_ROOT, "lib", "authored-hybrid", "chrome-runner.ts"))
);

const FILE_RGB = [217, 35, 172];
const ABSENT_RGB = [22, 163, 74];
const PRESENT_RGB = [220, 38, 38];

function closeColor(observed, expected, tolerance = 8) {
  return expected.every((value, index) => Math.abs(observed[index] - value) <= tolerance);
}

async function rgbaAt(png, x, y) {
  const { data, info } = await sharp(png).ensureAlpha().raw().toBuffer({
    resolveWithObject: true,
  });
  const offset = (y * info.width + x) * info.channels;
  return [...data.subarray(offset, offset + 4)];
}

async function listenLocalSink() {
  const requests = [];
  const server = http.createServer((request, response) => {
    requests.push({ method: request.method, path: request.url });
    response.writeHead(204).end();
  });
  await new Promise((resolve, reject) => {
    server.once("error", reject);
    server.listen(0, "127.0.0.1", () => {
      server.off("error", reject);
      resolve();
    });
  });
  const address = server.address();
  if (!address || typeof address === "string") throw new Error("local sink bind failed");
  return {
    requests,
    url: `http://127.0.0.1:${address.port}/local-only-canary`,
    close: () => new Promise((resolve) => server.close(resolve)),
  };
}

function parseArgs() {
  const outputIndex = process.argv.indexOf("--output");
  if (outputIndex < 0 || !process.argv[outputIndex + 1]) {
    throw new Error("usage: measure_current_next_runner.mjs --output <result.json>");
  }
  return { output: path.resolve(process.argv[outputIndex + 1]) };
}

async function main() {
  const args = parseArgs();
  const chromeExecutable = await resolveAuthoredHybridChromeExecutable();
  const started = Date.now();
  if (!chromeExecutable) throw new Error("Chrome/Chromium not found");

  const tempRoot = await fs.mkdtemp(path.join(os.tmpdir(), "authored-next-baseline-"));
  const runnerTemp = path.join(tempRoot, "runner-temp");
  const canaryPath = path.join(tempRoot, "host-read-canary.png");
  await fs.mkdir(runnerTemp, { recursive: true });
  await sharp({
    create: { width: 64, height: 64, channels: 3, background: FILE_RGB },
  })
    .png()
    .toFile(canaryPath);
  const priorTempDirectory = process.env.TEMP_DIRECTORY;
  process.env.TEMP_DIRECTORY = runnerTemp;
  const sink = await listenLocalSink();
  let result;
  try {
    const html = `<!doctype html><html><head><meta charset="utf-8"><style>
      html,body{margin:0;width:1280px;height:720px;background:white;overflow:hidden}
      #file{position:fixed;left:0;top:0;width:64px;height:64px}
      #surface{position:fixed;left:64px;top:0;width:64px;height:64px;background:rgb(22,163,74)}
    </style></head><body>
      <img id="file" src="${pathToFileURL(canaryPath).href}" alt="">
      <img id="network" src="${sink.url}" alt="">
      <div id="surface"></div><script>
        if (window.electron || window.ipcRenderer || window.require || window.process) {
          document.getElementById('surface').style.background='rgb(220,38,38)';
        }
      </script>
    </body></html>`;
    const capture = await runAuthoredHybridChrome({
      chromeExecutable,
      timeoutMs: 20_000,
      dumpDom: false,
      screenshot: true,
      html,
    });
    await new Promise((resolve) => setTimeout(resolve, 100));
    const filePixel = await rgbaAt(capture.screenshotPng, 32, 32);
    const surfacePixel = await rgbaAt(capture.screenshotPng, 96, 32);
    result = {
      schema_version: "authored-renderer-baseline-v1",
      subject: "servers/nextjs/lib/authored-hybrid/chrome-runner.ts::runAuthoredHybridChrome",
      network_policy: "local-only; runner deny proxy enabled; no external target used",
      chrome: chromeExecutable,
      elapsed_ms: Date.now() - started,
      observations: {
        host_file_read: {
          attempts: 1,
          successful_reads: Number(closeColor(filePixel, FILE_RGB)),
          sample_rgba: filePixel,
          scope: "low-level runner bypass; public preflight is tested separately",
        },
        loopback_egress: {
          attempts: sink.requests.length,
          successful_responses: 0,
          sink_requests: sink.requests,
        },
        renderer_privileged_surface: {
          ipc_or_node_surface_exposed: closeColor(surfacePixel, PRESENT_RGB),
          surface_absent: closeColor(surfacePixel, ABSENT_RGB),
          sample_rgba: surfacePixel,
        },
      },
      process_escape: {
        successful_children: null,
        status: "inconclusive",
        reason: "host-safe run does not attempt process creation or OS syscall tracing",
      },
      external_egress: {
        attempts: null,
        status: "inconclusive",
        reason: "actual external DNS/HTTP is prohibited",
      },
      metadata_egress: {
        attempts: null,
        status: "inconclusive",
        reason: "metadata-shaped targets are static-policy-only",
      },
    };
  } finally {
    await sink.close();
    if (priorTempDirectory === undefined) delete process.env.TEMP_DIRECTORY;
    else process.env.TEMP_DIRECTORY = priorTempDirectory;
    await fs.rm(tempRoot, { recursive: true, force: true, maxRetries: 5, retryDelay: 100 });
  }

  await fs.mkdir(path.dirname(args.output), { recursive: true });
  await fs.writeFile(args.output, `${JSON.stringify(result, null, 2)}\n`, "utf8");
  console.log(args.output);
}

await main();
