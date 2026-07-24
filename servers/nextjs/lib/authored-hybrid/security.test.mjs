import assert from "node:assert/strict";
import http from "node:http";
import test from "node:test";

import {
  resolveAuthoredHybridChromeExecutable,
  runAuthoredHybridChrome,
} from "./chrome-runner.ts";
import {
  preflightAuthoredHtmlForHybrid,
  readBoundedResponseText,
  validateHybridDataImageUrl,
} from "./security.ts";

const PNG =
  "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNgYAAAAAMAASsJTYQAAAAASUVORK5CYII=";

test("static legacy HTML and embedded raster images pass hybrid preflight", () => {
  const html = `<!doctype html><html><head><style>
    body{background-image:url('${PNG}');font-family:'맑은 고딕',sans-serif}
  </style></head><body><img src="${PNG}" alt="안전"><p>서울 발표</p></body></html>`;
  assert.deepEqual(preflightAuthoredHtmlForHybrid(html), { ok: true });
  const decoded = validateHybridDataImageUrl(PNG);
  assert.equal(decoded.ok, true);
  if (decoded.ok) assert.equal(decoded.mime, "png");
});

test("same-document SVG marker URLs pass hybrid preflight", () => {
  const html = `<!doctype html><html><head><style>
    .flow{marker-end:url(#arrowBlue)}
  </style></head><body><svg><defs><marker id="arrowBlue"></marker></defs>
    <path class="flow" d="M0 0 L10 10"/></svg></body></html>`;
  assert.deepEqual(preflightAuthoredHtmlForHybrid(html), { ok: true });
});

test("ordinary business prose with protocol-like words and backslashes passes", () => {
  for (const html of [
    "<!doctype html><html><body><h1>Company Profile: 회사 소개</h1></body></html>",
    "<p>배포 경로는 C:\\Users\\ibiz\\deploy 이며 Dockerfile: 참조</p>",
    "<p>2024 목표는 US$'000 단위, 순이익 &amp; 성장 &lt;10%&gt;</p>",
    "<p>ftp is legacy; the expression (매출 - 비용) 을 계산</p>",
    "<div style=\"color:#123456;font-family:'맑은 고딕'\">서울</div>",
  ]) {
    assert.equal(preflightAuthoredHtmlForHybrid(html).ok, true, html);
  }
});

test("active, network-bearing, local-file, and vector data content fail closed", () => {
  for (const html of [
    "<script>fetch('https://example.com')</script>",
    '<img src="https://example.com/a.png">',
    '<img src="file:///etc/passwd">',
    '<div onclick="alert(1)">x</div>',
    '<body/onload="alert(1)">x</body>',
    '<svg/onload="alert(1)"></svg>',
    '<style>@import "https://example.com/x.css"</style>',
    '<iframe srcdoc="<p>x</p>"></iframe>',
    '<img src="data:image/svg+xml;base64,PHN2Zz48L3N2Zz4=">',
    `<img src="${PNG}" srcset="http://127.0.0.1:31337/srcset 1x">`,
    "<style>.a{background-image:u\\72 l(http://127.0.0.1:31337/escaped)}</style>",
    '<style>.b{background-image:image-set("http://127.0.0.1:31337/set" 1x)}</style>',
  ]) {
    assert.equal(preflightAuthoredHtmlForHybrid(html).ok, false, html);
  }
});

test("Chrome blocks network requests even when markup bypasses preflight", async (t) => {
  const chromeExecutable = await resolveAuthoredHybridChromeExecutable();
  if (!chromeExecutable) {
    if (process.env.CI === "true") {
      assert.fail("CI must provide Chrome/Chromium for hybrid network isolation");
    }
    t.skip("Chrome/Chromium is unavailable");
    return;
  }

  const requests = [];
  const server = http.createServer((request, response) => {
    requests.push(request.url);
    response.writeHead(204).end();
  });
  await new Promise((resolve, reject) => {
    server.once("error", reject);
    server.listen(0, "127.0.0.1", () => {
      server.off("error", reject);
      resolve();
    });
  });
  try {
    const address = server.address();
    assert.ok(address && typeof address !== "string");
    const origin = `http://127.0.0.1:${address.port}`;
    await runAuthoredHybridChrome({
      chromeExecutable,
      timeoutMs: 30_000,
      dumpDom: true,
      screenshot: false,
      html: `<!doctype html><img srcset="${origin}/srcset 1x">
        <style>
          .a{background-image:u\\72 l(${origin}/escaped)}
          .b{background-image:image-set("${origin}/set" 1x)}
        </style><div class="a b">network isolation</div>`,
    });
    await new Promise((resolve) => setTimeout(resolve, 100));
    assert.deepEqual(requests, []);
  } finally {
    await new Promise((resolve) => server.close(() => resolve()));
  }
});

test("data image MIME, base64, and magic bytes must agree", () => {
  assert.equal(validateHybridDataImageUrl("data:image/png;base64,AAAA").ok, false);
  assert.equal(
    validateHybridDataImageUrl(PNG.replace("image/png", "image/jpeg")).ok,
    false
  );
  assert.equal(validateHybridDataImageUrl("data:image/png;base64,abc").ok, false);
});

test("presentation response reads are byte-bounded for declared and streamed sizes", async () => {
  assert.equal(
    await readBoundedResponseText(new Response("서울 hybrid"), 64),
    "서울 hybrid"
  );
  await assert.rejects(
    readBoundedResponseText(
      new Response("x", { headers: { "content-length": "65" } }),
      64
    ),
    /size limit/
  );

  const streamed = new Response(
    new ReadableStream({
      start(controller) {
        controller.enqueue(new Uint8Array(40));
        controller.enqueue(new Uint8Array(40));
        controller.close();
      },
    })
  );
  await assert.rejects(readBoundedResponseText(streamed, 64), /size limit/);
});

test("bounded response reads enforce UTF-8 byte boundaries and reject invalid lengths", async () => {
  assert.equal(
    await readBoundedResponseText(new Response("서울"), 6),
    "서울"
  );
  await assert.rejects(
    readBoundedResponseText(
      new Response("x", { headers: { "content-length": "not-a-number" } }),
      64
    ),
    /size limit/
  );

  let cancelled = false;
  const streamed = new Response(
    new ReadableStream({
      cancel() {
        cancelled = true;
      },
      start(controller) {
        controller.enqueue(new Uint8Array(65));
      },
    })
  );
  await assert.rejects(readBoundedResponseText(streamed, 64), /size limit/);
  assert.equal(cancelled, true);
});
