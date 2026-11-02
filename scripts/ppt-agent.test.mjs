import assert from "node:assert/strict";
import { once } from "node:events";
import http from "node:http";
import { mkdtemp, readFile } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { spawn } from "node:child_process";
import test from "node:test";

const cli = fileURLToPath(new URL("./ppt-agent.mjs", import.meta.url));

const PRESENTATION_ID = "11111111-1111-4111-8111-111111111111";
const OPERATION_ID = "44444444-4444-4444-8444-444444444444";
const ARTIFACT_ID = "66666666-6666-4666-8666-666666666666";
const TASK_ID = "77777777-7777-4777-8777-777777777777";
const SYNC_URL = "/api/v1/ppt/presentation/generate";
const ASYNC_URL = "/api/v1/ppt/presentation/generate/async";

function runCli(args) {
  return new Promise((resolve, reject) => {
    const child = spawn(process.execPath, [cli, ...args], {
      stdio: ["ignore", "pipe", "pipe"],
    });
    let stdout = "";
    let stderr = "";
    child.stdout.on("data", (chunk) => (stdout += chunk));
    child.stderr.on("data", (chunk) => (stderr += chunk));
    child.on("error", reject);
    child.on("close", (code) => resolve({ code, stdout, stderr }));
  });
}

function generateResponse(overrides = {}) {
  return {
    presentation_id: PRESENTATION_ID,
    path: "/app_data/workspace/deck.pptx",
    edit_path: `/presentation?id=${PRESENTATION_ID}`,
    export_operation_id: OPERATION_ID,
    export_error: null,
    ...overrides,
  };
}

/**
 * The svg strategy (the server default) needs a durable job, so the CLI admits
 * it through the async endpoint and polls status. Keys are matched by prefix,
 * so the async route must be listed before any bare SYNC_URL handler.
 */
function asyncGeneration(response = generateResponse()) {
  return {
    [ASYNC_URL]: (res) => json(res, { id: TASK_ID, status: "pending" }, 202),
    "/api/v1/ppt/presentation/status/": (res) =>
      json(res, { id: TASK_ID, status: "completed", data: response }),
  };
}

function operationTimeline(status, result) {
  return {
    schema_version: 1,
    items: [
      { schema_version: 1, anchor_type: "message", id: "m1", position: 1, role: "user" },
      {
        schema_version: 1,
        anchor_type: "operation",
        id: "card",
        operation_id: OPERATION_ID,
        position: 2,
        operation_type: "export.pptx",
        status,
        result: result ?? null,
      },
    ],
  };
}

/**
 * A stand-in backend. `plan` maps a path prefix to a handler; every request is
 * recorded so a test can assert the exact contract the CLI depends on.
 */
async function startMockServer(plan) {
  const requests = [];
  const server = http.createServer(async (req, res) => {
    let raw = "";
    for await (const chunk of req) raw += chunk;
    const url = req.url ?? "";
    requests.push({ method: req.method, url, body: raw ? JSON.parse(raw) : null });
    const handler = Object.entries(plan).find(([prefix]) => url.startsWith(prefix));
    if (!handler) {
      res.writeHead(404, { "content-type": "application/json" });
      res.end(JSON.stringify({ detail: `unexpected request: ${url}` }));
      return;
    }
    handler[1](res, requests);
  });
  server.listen(0, "127.0.0.1");
  await once(server, "listening");
  const { port } = server.address();
  return { base: `http://127.0.0.1:${port}`, requests, server };
}

function json(res, payload, status = 200) {
  res.writeHead(status, { "content-type": "application/json" });
  res.end(JSON.stringify(payload));
}

test("keeps polling when status completes before the chained export is recorded", async (t) => {
  let polls = 0;
  const mock = await startMockServer({
    [ASYNC_URL]: (res) => json(res, { id: TASK_ID, status: "pending" }, 202),
    "/api/v1/ppt/presentation/status/": (res) => {
      polls += 1;
      const data = polls === 1
        ? generateResponse({ export_operation_id: null, export_error: null })
        : generateResponse();
      json(res, { id: TASK_ID, status: "completed", data });
    },
  });
  t.after(() => mock.server.close());

  const result = await runCli([
    "--content", "svg deck",
    "--base", mock.base,
  ]);

  assert.equal(result.code, 0, result.stderr);
  assert.equal(polls, 2);
  assert.match(result.stdout, new RegExp(`export op: +${OPERATION_ID}`));
});

test("forwards --style to the local generation API", async (t) => {
  const mock = await startMockServer(asyncGeneration());
  t.after(() => mock.server.close());

  const result = await runCli([
    "--content", "authored deck",
    "--mode", "authored",
    "--style", "strategic-navy",
    "--base", mock.base,
  ]);

  assert.equal(result.code, 0, result.stderr);
  assert.equal(mock.requests.length, 2);
  assert.equal(mock.requests[1].url, `/api/v1/ppt/presentation/status/${TASK_ID}`);
  assert.deepEqual(mock.requests[0], {
    method: "POST",
    url: ASYNC_URL,
    body: {
      content: "authored deck",
      n_slides: 8,
      language: "Korean (한국어)",
      template: "authored",
      export_as: "pptx",
      authored_style: "strategic-navy",
    },
  });
  assert.match(result.stdout, new RegExp(`export op: +${OPERATION_ID}`));
});

test("omits strategy from the request body by default (server default is svg)", async (t) => {
  const mock = await startMockServer(asyncGeneration());
  t.after(() => mock.server.close());

  const result = await runCli(["--content", "default strategy deck", "--base", mock.base]);

  assert.equal(result.code, 0, result.stderr);
  // svg cannot run on the inline endpoint, so the default goes through the job.
  assert.equal(mock.requests[0].url, ASYNC_URL);
  assert.ok(!("strategy" in mock.requests[0].body));
  assert.ok(!("common_header_mode" in mock.requests[0].body));
});

test("--common-header forwards the svg header mode", async (t) => {
  const mock = await startMockServer(asyncGeneration());
  t.after(() => mock.server.close());

  const result = await runCli([
    "--content", "free header deck", "--common-header", "free", "--base", mock.base,
  ]);

  assert.equal(result.code, 0, result.stderr);
  assert.equal(mock.requests[0].body.common_header_mode, "free");
});

test("--common-header rejects an unknown value and authored strategy", async () => {
  let result = await runCli(["--content", "x", "--common-header", "fixed"]);
  assert.equal(result.code, 1);
  assert.match(result.stderr, /--common-header must be one of uniform, free/);
  result = await runCli(["--content", "x", "--common-header", "free", "--strategy", "authored"]);
  assert.equal(result.code, 1);
  assert.match(result.stderr, /--common-header applies only to the svg strategy/);
});

test("--strategy authored sends an explicit strategy override", async (t) => {
  const mock = await startMockServer({
    "/api/v1/ppt/presentation/generate": (res) => json(res, generateResponse()),
  });
  t.after(() => mock.server.close());

  const result = await runCli([
    "--content", "authored deck",
    "--strategy", "authored",
    "--base", mock.base,
  ]);

  assert.equal(result.code, 0, result.stderr);
  assert.equal(mock.requests[0].url, SYNC_URL);
  assert.equal(mock.requests[0].body.strategy, "authored");
});

test("--strategy rejects an unknown value", async (t) => {
  const result = await runCli(["--content", "x", "--strategy", "deck-ir"]);
  assert.equal(result.code, 1);
  assert.match(result.stderr, /--strategy must be one of svg, authored/);
});

test("--out follows the chained export operation to its artifact", async (t) => {
  let polls = 0;
  const mock = await startMockServer({
    ...asyncGeneration(),
    [`/api/v1/ppt/workspace/presentations/${PRESENTATION_ID}/timeline`]: (res) => {
      polls += 1;
      json(
        res,
        polls === 1
          ? operationTimeline("running", null)
          : operationTimeline("succeeded", { artifact_id: ARTIFACT_ID, format: "pptx" })
      );
    },
    [`/api/v1/ppt/workspace/artifacts/${ARTIFACT_ID}/download`]: (res) => {
      res.writeHead(200, {
        "content-type":
          "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        "content-disposition": 'attachment; filename="presentation-deck-r5.pptx"',
      });
      res.end(Buffer.from("PPTX-BYTES"));
    },
  });
  t.after(() => mock.server.close());
  const outDir = await mkdtemp(path.join(os.tmpdir(), "ppt-agent-export-"));

  const result = await runCli([
    "--content", "chained export",
    "--base", mock.base,
    "--out", outDir,
    "--timeout", "30",
  ]);

  assert.equal(result.code, 0, result.stderr);
  const urls = mock.requests.map((request) => request.url);
  assert.equal(urls[0], ASYNC_URL);
  // No /app_data/exports/... read survives: the artifact is reached through the
  // operation, not through a server-side path.
  assert.ok(!urls.some((url) => url.includes("/app_data/")));
  assert.ok(urls.includes(`/api/v1/ppt/workspace/presentations/${PRESENTATION_ID}/timeline`));
  assert.equal(urls.at(-1), `/api/v1/ppt/workspace/artifacts/${ARTIFACT_ID}/download`);

  const saved = path.join(outDir, "presentation-deck-r5.pptx");
  assert.equal(await readFile(saved, "utf8"), "PPTX-BYTES");
  assert.match(result.stdout, /downloaded to:/);
});

test("--out reports a failed export operation instead of a silent success", async (t) => {
  const mock = await startMockServer({
    ...asyncGeneration(),
    [`/api/v1/ppt/workspace/presentations/${PRESENTATION_ID}/timeline`]: (res) =>
      json(res, operationTimeline("failed", { failure_code: "public_export_deadline_exceeded" })),
  });
  t.after(() => mock.server.close());
  const outDir = await mkdtemp(path.join(os.tmpdir(), "ppt-agent-export-"));

  const result = await runCli([
    "--content", "failing export",
    "--base", mock.base,
    "--out", outDir,
    "--timeout", "30",
  ]);

  assert.equal(result.code, 1);
  assert.match(result.stderr, /export operation failed/);
  assert.match(result.stderr, /public_export_deadline_exceeded/);
});

test("--out reports a generation whose export was never chained", async (t) => {
  const mock = await startMockServer(
    asyncGeneration(
      generateResponse({
        export_operation_id: null,
        export_error: "public_admission_export_quota",
      })
    )
  );
  t.after(() => mock.server.close());
  const outDir = await mkdtemp(path.join(os.tmpdir(), "ppt-agent-export-"));

  const result = await runCli([
    "--content", "unchained export",
    "--base", mock.base,
    "--out", outDir,
    "--timeout", "30",
  ]);

  assert.equal(result.code, 1);
  assert.match(result.stderr, /did not start the requested export/);
  assert.match(result.stderr, /public_admission_export_quota/);
});

test("rejects a missing --style value before making a request", async () => {
  const result = await runCli(["--content", "authored deck", "--style"]);

  assert.equal(result.code, 1);
  assert.match(result.stderr, /--style requires a style preset id/);
});

test("documents authored style usage in --help", async () => {
  const result = await runCli(["--help"]);

  assert.equal(result.code, 0, result.stderr);
  assert.match(result.stdout, /--mode authored/);
  assert.match(result.stdout, /--style <id>/);
});
