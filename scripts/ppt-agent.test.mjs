import assert from "node:assert/strict";
import { once } from "node:events";
import http from "node:http";
import { fileURLToPath } from "node:url";
import { spawn } from "node:child_process";
import test from "node:test";

const cli = fileURLToPath(new URL("./ppt-agent.mjs", import.meta.url));

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

async function startMockServer() {
  const requests = [];
  const server = http.createServer(async (req, res) => {
    let raw = "";
    for await (const chunk of req) raw += chunk;
    requests.push({ method: req.method, url: req.url, body: raw ? JSON.parse(raw) : null });
    res.writeHead(200, { "content-type": "application/json" });
    res.end(JSON.stringify({ presentation_id: "mock-deck", path: "/tmp/mock.pptx", edit_path: "/presentation?id=mock-deck" }));
  });
  server.listen(0, "127.0.0.1");
  await once(server, "listening");
  const { port } = server.address();
  return { base: `http://127.0.0.1:${port}`, requests, server };
}

test("forwards --style to the local generation API", async (t) => {
  const mock = await startMockServer();
  t.after(() => mock.server.close());

  const result = await runCli([
    "--content", "authored deck",
    "--mode", "authored",
    "--style", "strategic-navy",
    "--base", mock.base,
  ]);

  assert.equal(result.code, 0, result.stderr);
  assert.equal(mock.requests.length, 1);
  assert.deepEqual(mock.requests[0], {
    method: "POST",
    url: "/api/v1/ppt/presentation/generate",
    body: {
      content: "authored deck",
      n_slides: 8,
      language: "Korean (한국어)",
      template: "authored",
      export_as: "pptx",
      authored_style: "strategic-navy",
    },
  });
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
