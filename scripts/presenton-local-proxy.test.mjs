import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { once } from "node:events";
import http from "node:http";
import net from "node:net";
import { fileURLToPath } from "node:url";
import test from "node:test";

const proxyScript = fileURLToPath(
  new URL("./presenton-local-proxy.mjs", import.meta.url),
);

async function availablePort() {
  const server = net.createServer();
  server.listen(0, "127.0.0.1");
  await once(server, "listening");
  const { port } = server.address();
  await new Promise((resolve) => server.close(resolve));
  return port;
}

test("the browser blank-presentation path is proxied unchanged to FastAPI", async (t) => {
  const requests = [];
  const fastApi = http.createServer(async (request, response) => {
    let body = "";
    for await (const chunk of request) body += chunk;
    requests.push({ method: request.method, url: request.url, body });
    response.writeHead(201, { "content-type": "application/json" });
    response.end(JSON.stringify({ id: "blank-deck", n_slides: 1, slides: [{}] }));
  });
  fastApi.listen(0, "127.0.0.1");
  await once(fastApi, "listening");
  const fastApiPort = fastApi.address().port;
  const proxyPort = await availablePort();
  const proxy = spawn(process.execPath, [proxyScript], {
    env: {
      ...process.env,
      PROXY_PORT: String(proxyPort),
      FASTAPI_PORT: String(fastApiPort),
      NEXT_PORT: String(fastApiPort + 1),
      MCP_PORT: String(fastApiPort + 2),
    },
    stdio: ["ignore", "pipe", "pipe"],
  });
  t.after(() => {
    proxy.kill();
    fastApi.close();
  });

  let startup = "";
  proxy.stdout.on("data", (chunk) => {
    startup += chunk;
  });
  await new Promise((resolve, reject) => {
    const timeout = setTimeout(
      () => reject(new Error(`proxy did not start: ${startup}`)),
      5_000,
    );
    const poll = setInterval(() => {
      if (startup.includes("[presenton-local-proxy]")) {
        clearTimeout(timeout);
        clearInterval(poll);
        resolve();
      }
    }, 10);
    proxy.once("exit", (code) => {
      clearTimeout(timeout);
      clearInterval(poll);
      reject(new Error(`proxy exited before startup with code ${code}`));
    });
  });

  const response = await fetch(
    `http://127.0.0.1:${proxyPort}/api/v1/ppt/presentation/create/blank`,
    { method: "POST" },
  );

  assert.equal(response.status, 201);
  assert.equal((await response.json()).id, "blank-deck");
  assert.deepEqual(requests, [
    {
      method: "POST",
      url: "/api/v1/ppt/presentation/create/blank",
      body: "",
    },
  ]);
});
