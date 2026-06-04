// Local dev reverse proxy for running Presenton natively on Windows (no Docker/nginx).
// Mirrors servers/.. nginx.conf routing so the browser sees a single origin (:5000),
// which the frontend assumes (utils/api.ts getApiUrl returns same-origin /api/v1/* paths).
//   /api/v1/*, /docs, /openapi.json, /static/*, /app_data/* -> FastAPI (8000)
//   /mcp*                                                    -> MCP server (8001)
//   everything else (/, /_next/*, Next.js /api/<handler>)    -> Next.js (3000)
// Dependency-free (Node built-ins only). Lives OUTSIDE the repo to keep the clone pristine.
import http from "node:http";
import net from "node:net";

const LISTEN_PORT = Number(process.env.PROXY_PORT || 5000);
const NEXT = { host: "127.0.0.1", port: Number(process.env.NEXT_PORT || 3000) };
const FASTAPI = { host: "127.0.0.1", port: Number(process.env.FASTAPI_PORT || 8000) };
const MCP = { host: "127.0.0.1", port: Number(process.env.MCP_PORT || 8001) };

function pickUpstream(url) {
  if (
    url.startsWith("/api/v1/") ||
    url === "/docs" ||
    url.startsWith("/docs") ||
    url === "/openapi.json" ||
    url.startsWith("/static/") ||
    url.startsWith("/app_data/")
  ) {
    return FASTAPI;
  }
  if (url === "/mcp" || url.startsWith("/mcp/") || url.startsWith("/mcp")) {
    return MCP;
  }
  return NEXT;
}

const server = http.createServer((req, res) => {
  const target = pickUpstream(req.url);
  const headers = { ...req.headers };
  headers["x-forwarded-proto"] = "http";
  headers["x-forwarded-host"] = req.headers.host || `localhost:${LISTEN_PORT}`;
  headers["x-forwarded-for"] = req.socket.remoteAddress || "";

  const upstream = http.request(
    { host: target.host, port: target.port, method: req.method, path: req.url, headers },
    (ur) => {
      res.writeHead(ur.statusCode || 502, ur.headers);
      ur.pipe(res);
    }
  );
  upstream.on("error", (err) => {
    if (!res.headersSent) res.writeHead(502, { "content-type": "text/plain" });
    res.end(`[local-proxy] upstream ${target.host}:${target.port} error: ${err.message}`);
  });
  req.pipe(upstream);
});

// WebSocket / HTTP upgrade passthrough (Next.js).
server.on("upgrade", (req, socket, head) => {
  const target = pickUpstream(req.url);
  const up = net.connect(target.port, target.host, () => {
    const lines = [`${req.method} ${req.url} HTTP/1.1`];
    for (let i = 0; i < req.rawHeaders.length; i += 2) {
      lines.push(`${req.rawHeaders[i]}: ${req.rawHeaders[i + 1]}`);
    }
    up.write(lines.join("\r\n") + "\r\n\r\n");
    if (head && head.length) up.write(head);
    socket.pipe(up);
    up.pipe(socket);
  });
  up.on("error", () => socket.destroy());
  socket.on("error", () => up.destroy());
});

server.listen(LISTEN_PORT, "127.0.0.1", () => {
  console.log(
    `[presenton-local-proxy] http://127.0.0.1:${LISTEN_PORT}  ->  next:${NEXT.port}  fastapi:${FASTAPI.port}  mcp:${MCP.port}`
  );
});
