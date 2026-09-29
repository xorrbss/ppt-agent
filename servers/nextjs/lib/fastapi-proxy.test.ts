import assert from "node:assert/strict";
import test from "node:test";

import {
  classifyProxyError,
  errorPropagatingStream,
  fetchFastApi,
  JSON_RESPONSE_HEADERS,
  proxyToFastApi,
} from "./fastapi-proxy.ts";

type FetchCall = { url: string; init: RequestInit & { dispatcher?: object } };

const ENV_KEYS = ["FAST_API_INTERNAL_URL", "NEXT_PUBLIC_FAST_API", "AUTH_USERNAME", "AUTH_PASSWORD", "USER_CONFIG_PATH"];

async function withFetch<T>(
  fake: (url: string, init: FetchCall["init"]) => Promise<Response>,
  run: (calls: FetchCall[]) => Promise<T>,
): Promise<T> {
  const originalFetch = globalThis.fetch;
  const originalError = console.error;
  const saved = Object.fromEntries(ENV_KEYS.map((key) => [key, process.env[key]]));
  for (const key of ENV_KEYS) delete process.env[key];
  process.env.FAST_API_INTERNAL_URL = "http://backend.test:8000/";
  const calls: FetchCall[] = [];
  globalThis.fetch = (async (url: string, init: FetchCall["init"]) => {
    calls.push({ url: String(url), init });
    return fake(String(url), init);
  }) as typeof fetch;
  console.error = () => {};
  try {
    return await run(calls);
  } finally {
    globalThis.fetch = originalFetch;
    console.error = originalError;
    for (const key of ENV_KEYS) {
      if (saved[key] === undefined) delete process.env[key];
      else process.env[key] = saved[key];
    }
  }
}

function incoming(headers: Record<string, string>, init: RequestInit = {}): Request {
  return new Request("http://app.test/api/x?keep=1", { headers, ...init });
}

test("only allowlisted request headers reach FastAPI and the search string is forwarded", async () => {
  await withFetch(
    async () => new Response("ok"),
    async (calls) => {
      await proxyToFastApi(
        incoming({
          cookie: "session=abc",
          authorization: "Bearer t",
          "x-forwarded-for": "10.0.0.1",
          "x-evil": "1",
          range: "bytes=0-9",
        }),
        "/api/v1/thing",
        { timeoutMs: 1000 },
      );
      const [{ url, init }] = calls;
      assert.equal(url, "http://backend.test:8000/api/v1/thing?keep=1");
      const headers = new Headers(init.headers);
      assert.equal(headers.get("cookie"), "session=abc");
      assert.equal(headers.get("authorization"), "Bearer t");
      assert.equal(headers.get("range"), "bytes=0-9");
      assert.equal(headers.get("x-forwarded-for"), null);
      assert.equal(headers.get("x-evil"), null);
      assert.equal(init.cache, "no-store");
    },
  );
});

test("route-supplied allowlist replaces the default and can drop server auth and search", async () => {
  await withFetch(
      async () => new Response("ok"),
      async (calls) => {
        process.env.AUTH_USERNAME = "svc";
        process.env.AUTH_PASSWORD = "pw";
        await proxyToFastApi(
          incoming({ cookie: "c=1", authorization: "Bearer t", "idempotency-key": "k" }),
          "/p",
          {
            timeoutMs: 1000,
            forwardRequestHeaders: ["cookie"],
            includeServerAuth: false,
            forwardSearch: false,
            headers: { Accept: "application/json" },
          },
        );
        await proxyToFastApi(incoming({ cookie: "c=1" }), "/p", { timeoutMs: 1000 });
        const first = new Headers(calls[0].init.headers);
        assert.equal(calls[0].url, "http://backend.test:8000/p");
        assert.equal(first.get("cookie"), "c=1");
        assert.equal(first.get("authorization"), null);
        assert.equal(first.get("idempotency-key"), null);
        assert.equal(first.get("accept"), "application/json");
        // Server credentials are attached by default and a client Authorization wins.
        const second = new Headers(calls[1].init.headers);
        assert.equal(second.get("authorization"), `Basic ${Buffer.from("svc:pw").toString("base64")}`);
      },
    );
});

test("a forwarded client Authorization overrides the server credentials", async () => {
  await withFetch(
    async () => new Response("ok"),
    async (calls) => {
      process.env.AUTH_USERNAME = "svc";
      process.env.AUTH_PASSWORD = "pw";
      await proxyToFastApi(incoming({ authorization: "Bearer user" }), "/p", { timeoutMs: 1000 });
      assert.equal(new Headers(calls[0].init.headers).get("authorization"), "Bearer user");
    },
  );
});

test("response headers are allowlisted, defaulted and made no-store", async () => {
  await withFetch(
    async () =>
      new Response(null, {
        status: 201,
        headers: { "x-request-id": "r1", "set-cookie": "s=1", "x-internal": "1", etag: "e" },
      }),
    async () => {
      const response = await proxyToFastApi(incoming({}), "/p", {
        timeoutMs: 1000,
        forwardResponseHeaders: JSON_RESPONSE_HEADERS,
        defaultContentType: "application/json",
      });
      assert.equal(response.status, 201);
      assert.equal(response.headers.get("x-request-id"), "r1");
      assert.equal(response.headers.get("set-cookie"), null);
      assert.equal(response.headers.get("x-internal"), null);
      assert.equal(response.headers.get("etag"), null);
      assert.equal(response.headers.get("content-type"), "application/json");
      assert.equal(response.headers.get("cache-control"), "no-store");
    },
  );
  await withFetch(
    async () => new Response("x", { headers: { "cache-control": "max-age=5" } }),
    async () => {
      const response = await proxyToFastApi(incoming({}), "/p", { timeoutMs: 1000, cacheControl: null });
      assert.equal(response.headers.get("cache-control"), "max-age=5");
    },
  );
});

test("request bodies stream by default and can be buffered or dropped explicitly", async () => {
  await withFetch(
    async () => new Response("ok"),
    async (calls) => {
      await proxyToFastApi(
        incoming({ "content-type": "application/json" }, { method: "POST", body: '{"a":1}' }),
        "/streamed",
        { timeoutMs: 1000 },
      );
      assert.ok(calls[0].init.body instanceof ReadableStream);
      assert.equal((calls[0].init as { duplex?: string }).duplex, "half");

      await proxyToFastApi(
        incoming({}, { method: "POST", body: "raw" }),
        "/buffered",
        { timeoutMs: 1000, body: "buffered" },
      );
      assert.equal(calls[1].init.body, "buffered");

      await proxyToFastApi(incoming({}), "/get", { timeoutMs: 1000 });
      assert.equal(calls[2].init.body, undefined);
      assert.equal(calls[2].init.method, "GET");
    },
  );
});

test("timeouts map to 504 by default and to the route's own envelope on request", async () => {
  await withFetch(
    (_url, init) =>
      new Promise((_resolve, reject) => {
        init.signal?.addEventListener("abort", () => reject(init.signal?.reason));
      }),
    async () => {
      const response = await proxyToFastApi(incoming({}), "/slow", { timeoutMs: 20 });
      assert.equal(response.status, 504);
      assert.deepEqual(await response.json(), { detail: { reasonCode: "upstream_timeout" } });

      const custom = await proxyToFastApi(incoming({}), "/slow", {
        timeoutMs: 20,
        onFailure: (kind) => Response.json({ kind }, { status: 599 }),
      });
      assert.equal(custom.status, 599);
      assert.deepEqual(await custom.json(), { kind: "timeout" });
    },
  );
});

test("connection failures map to 502 and undici timeout codes count as timeouts", async () => {
  await withFetch(
    async () => {
      throw new TypeError("fetch failed", { cause: { code: "ECONNREFUSED" } });
    },
    async () => {
      const response = await proxyToFastApi(incoming({}), "/down", { timeoutMs: 1000 });
      assert.equal(response.status, 502);
      assert.deepEqual(await response.json(), { detail: { reasonCode: "upstream_unreachable" } });
    },
  );
  const undici = new TypeError("fetch failed", { cause: { code: "UND_ERR_HEADERS_TIMEOUT" } });
  assert.equal(classifyProxyError(undici), "timeout");
  assert.equal(classifyProxyError(new Error("boom")), "unavailable");
  assert.equal(classifyProxyError("nope"), "unavailable");
  const clientAbort = new AbortController();
  clientAbort.abort();
  assert.equal(classifyProxyError(new DOMException("aborted", "AbortError"), clientAbort.signal), "unavailable");
});

test("timeoutMs null leaves the call unbounded and a dispatcher is passed through", async () => {
  await withFetch(
    async () => new Response("ok"),
    async (calls) => {
      const dispatcher = { tag: "agent" };
      await fetchFastApi(incoming({}), "/stream", { timeoutMs: null, dispatcher });
      assert.equal(calls[0].init.signal, undefined);
      assert.equal(calls[0].init.dispatcher, dispatcher);
    },
  );
});

test("upstream bodies stream through without buffering", async () => {
  const encoder = new TextEncoder();
  let release!: () => void;
  const gate = new Promise<void>((resolve) => {
    release = resolve;
  });
  await withFetch(
    async () =>
      new Response(
        new ReadableStream<Uint8Array>({
          async start(controller) {
            controller.enqueue(encoder.encode("first;"));
            await gate;
            controller.enqueue(encoder.encode("second"));
            controller.close();
          },
        }),
      ),
    async () => {
      const response = await proxyToFastApi(incoming({}), "/sse", { timeoutMs: 1000 });
      const reader = response.body!.getReader();
      const decoder = new TextDecoder();
      // The first chunk arrives while the upstream is still open.
      const first = await reader.read();
      assert.equal(decoder.decode(first.value), "first;");
      release();
      const second = await reader.read();
      assert.equal(decoder.decode(second.value), "second");
      assert.equal((await reader.read()).done, true);
    },
  );
});

test("errorPropagatingStream errors the outgoing stream when the upstream fails mid-flight", async () => {
  const originalError = console.error;
  console.error = () => {};
  try {
    const upstream = new ReadableStream<Uint8Array>({
      start(controller) {
        controller.enqueue(new TextEncoder().encode("partial"));
      },
      pull() {
        throw new Error("socket reset");
      },
    });
    const reader = errorPropagatingStream(upstream).getReader();
    assert.equal(new TextDecoder().decode((await reader.read()).value), "partial");
    await assert.rejects(reader.read(), /socket reset/);
  } finally {
    console.error = originalError;
  }
});
