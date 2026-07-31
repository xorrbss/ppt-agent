import assert from "node:assert/strict";
import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import test from "node:test";

import {
  GoogleFontsCollector,
  validateGoogleFontsCssUrl,
  validateGoogleFontsWoff2Url,
} from "./google-fonts-collector.ts";
import { preflightAuthoredHtmlForHybrid } from "./security.ts";

const CSS_URL =
  "https://fonts.googleapis.com/css2?family=Noto+Sans+KR:wght@400;700&display=swap";
const FONT_URL =
  "https://fonts.gstatic.com/s/notosanskr/v1/verified-subset.woff2";
const VALID_WOFF2 = Buffer.concat([
  Buffer.from("wOF2", "ascii"),
  Buffer.alloc(44),
]);
const VALID_CSS = `/* korean */
@font-face {
  font-family: 'Noto Sans KR';
  font-style: normal;
  font-weight: 400 700;
  font-display: swap;
  src: url(${FONT_URL}) format('woff2');
  unicode-range: U+AC00-D7A3, U+0020-007E;
}`;

async function makeCacheDirectory(t) {
  const directory = await fs.mkdtemp(
    path.join(os.tmpdir(), "presenton-google-font-test-")
  );
  t.after(async () => {
    await fs.rm(directory, { recursive: true, force: true });
  });
  return directory;
}

function response(body, contentType, init = {}) {
  return new Response(body, {
    status: init.status ?? 200,
    headers: {
      "content-type": contentType,
      ...(init.headers ?? {}),
    },
  });
}

function createSuccessfulFetch(requests) {
  return async (input, init) => {
    const url = String(input);
    requests.push({ url, init });
    if (url === CSS_URL) return response(VALID_CSS, "text/css; charset=utf-8");
    if (url === FONT_URL) return response(VALID_WOFF2, "font/woff2");
    throw new Error(`unexpected URL: ${url}`);
  };
}

test("URL policy accepts only exact HTTPS Google Fonts CSS and WOFF2 endpoints", () => {
  assert.equal(validateGoogleFontsCssUrl(CSS_URL).hostname, "fonts.googleapis.com");
  assert.equal(validateGoogleFontsWoff2Url(FONT_URL).hostname, "fonts.gstatic.com");

  for (const value of [
    "http://fonts.googleapis.com/css2?family=Inter",
    "https://fonts.googleapis.com:444/css2?family=Inter",
    "https://user:secret@fonts.googleapis.com/css2?family=Inter",
    "https://fonts.googleapis.com.evil.example/css2?family=Inter",
    "https://fonts.googleapis.com/css3?family=Inter",
    "https://fonts.googleapis.com/css2?family=Inter&text=slide%20body",
    "https://fonts.googleapis.com/css2?family=%ED%95%9C%EA%B8%80",
    "https://127.0.0.1/css2?family=Inter",
    "https://[::1]/css2?family=Inter",
  ]) {
    assert.throws(() => validateGoogleFontsCssUrl(value), value);
  }

  for (const value of [
    "http://fonts.gstatic.com/s/inter/v1/font.woff2",
    "https://fonts.gstatic.com:8443/s/inter/v1/font.woff2",
    "https://fonts.gstatic.com.evil.example/s/inter/v1/font.woff2",
    "https://fonts.googleapis.com/s/inter/v1/font.woff2",
    "https://fonts.gstatic.com/font.woff2",
    "https://fonts.gstatic.com/s/inter/v1/font.ttf",
    "https://fonts.gstatic.com/s/inter/v1/font.woff2?token=x",
    "https://127.0.0.1/s/inter/v1/font.woff2",
  ]) {
    assert.throws(() => validateGoogleFontsWoff2Url(value), value);
  }
});

test("collector strips only validated references and injects verified data fonts after preflight", async (t) => {
  const cacheDirectory = await makeCacheDirectory(t);
  const requests = [];
  const collector = new GoogleFontsCollector({
    cacheDirectory,
    fetchImpl: createSuccessfulFetch(requests),
  });
  const html = `<!doctype html><html><head>
    <link rel="preconnect" href="https://fonts.googleapis.com">
    <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
    <link rel="stylesheet" href="${CSS_URL.replaceAll("&", "&amp;")}">
    <style>@import url('${CSS_URL}');
      h1 { font-family: 'Noto Sans KR', sans-serif; }
    </style>
  </head><body><h1>편집 가능한 제목</h1></body></html>`;

  const prepared = await collector.prepareHtml(html);
  assert.equal(prepared.status, "success");
  assert.equal(prepared.usedFallback, false);
  assert.equal(prepared.requestedStylesheets, 1);
  assert.equal(prepared.requestedFontFaces, 0);
  assert.equal(prepared.loadedStylesheets, 1);
  assert.equal(prepared.fontFiles, 1);
  assert.deepEqual(prepared.trustedFamilies, ["Noto Sans KR"]);
  assert.deepEqual(prepared.failures, []);
  assert.deepEqual(preflightAuthoredHtmlForHybrid(prepared.htmlForPreflight), {
    ok: true,
  });
  assert.doesNotMatch(prepared.htmlForPreflight, /fonts\.(?:googleapis|gstatic)\.com/i);
  assert.doesNotMatch(prepared.htmlForPreflight, /@font-face/i);
  assert.match(
    prepared.htmlForChrome,
    /data-presenton-trusted-google-fonts="v1"/
  );
  assert.match(prepared.htmlForChrome, /data:font\/woff2;base64,d09GMg/);
  assert.doesNotMatch(prepared.htmlForChrome, /https:\/\/fonts\.gstatic\.com/i);

  assert.equal(requests.length, 2);
  for (const request of requests) {
    const headers = new Headers(request.init.headers);
    assert.equal(headers.has("cookie"), false);
    assert.equal(headers.has("authorization"), false);
    assert.equal(headers.has("proxy-authorization"), false);
    assert.equal(request.init.redirect, "manual");
    assert.equal(request.init.credentials, "omit");
  }
});

test("untrusted data fonts remain in htmlForPreflight and cannot cross the trust boundary", async (t) => {
  const cacheDirectory = await makeCacheDirectory(t);
  const collector = new GoogleFontsCollector({
    cacheDirectory,
    fetchImpl: async () => {
      throw new Error("network must not be called");
    },
  });
  const malicious = `<!doctype html><html><head><style>
    @font-face{font-family:'Owned';src:url(data:font/woff2;base64,d09GMgAAAAA=)}
  </style></head><body>owned</body></html>`;
  const prepared = await collector.prepareHtml(malicious);
  assert.equal(prepared.status, "not-requested");
  assert.deepEqual(prepared.trustedFamilies, []);
  assert.equal(prepared.htmlForChrome, malicious);
  assert.equal(prepared.htmlForPreflight, malicious);
  assert.equal(preflightAuthoredHtmlForHybrid(prepared.htmlForPreflight).ok, false);
});

test("strict inline remote @font-face is collected while ambiguous blocks remain blocked", async (t) => {
  const cacheDirectory = await makeCacheDirectory(t);
  const requests = [];
  const collector = new GoogleFontsCollector({
    cacheDirectory,
    fetchImpl: async (input) => {
      requests.push(String(input));
      if (String(input) === FONT_URL) {
        return response(VALID_WOFF2, "font/woff2");
      }
      throw new Error(`unexpected URL: ${input}`);
    },
  });
  const direct = `<!doctype html><html><head><style>
    @font-face {
      font-family: 'Noto Sans KR';
      font-style: normal;
      font-weight: 400;
      font-display: swap;
      src: url('${FONT_URL}') format('woff2');
    }
    p { font-family: 'Noto Sans KR', sans-serif; }
  </style></head><body><p>직접 참조</p></body></html>`;
  const prepared = await collector.prepareHtml(direct);
  assert.equal(prepared.status, "success");
  assert.equal(prepared.requestedStylesheets, 0);
  assert.equal(prepared.requestedFontFaces, 1);
  assert.equal(prepared.fontFiles, 1);
  assert.deepEqual(prepared.trustedFamilies, ["Noto Sans KR"]);
  assert.deepEqual(requests, [FONT_URL]);
  assert.deepEqual(preflightAuthoredHtmlForHybrid(prepared.htmlForPreflight), {
    ok: true,
  });
  assert.doesNotMatch(prepared.htmlForPreflight, /@font-face/i);
  assert.match(
    prepared.htmlForPreflight,
    /font-family:\s*"Noto Sans KR", "Malgun Gothic", sans-serif/
  );
  assert.match(prepared.htmlForChrome, /data:font\/woff2;base64/);
  assert.doesNotMatch(prepared.htmlForChrome, /https:\/\/fonts\.gstatic\.com/);

  const ambiguous = `<!doctype html><style>
    @font-face {
      font-family: 'Noto Sans KR';
      src: local('Noto Sans KR'), url('${FONT_URL}') format('woff2');
    }
  </style><p>must fail closed</p>`;
  const rejected = await collector.prepareHtml(ambiguous);
  assert.equal(rejected.status, "not-requested");
  assert.equal(rejected.requestedFontFaces, 0);
  assert.match(rejected.htmlForPreflight, /https:\/\/fonts\.gstatic\.com/);
  assert.equal(preflightAuthoredHtmlForHybrid(rejected.htmlForPreflight).ok, false);
});

test("partial collection keeps loaded logical fonts and adds policy-identical local fallback stacks", async (t) => {
  const cacheDirectory = await makeCacheDirectory(t);
  const interCssUrl =
    "https://fonts.googleapis.com/css2?family=Inter&display=swap";
  const robotoCssUrl =
    "https://fonts.googleapis.com/css2?family=Roboto&display=swap";
  const interFontUrl =
    "https://fonts.gstatic.com/s/inter/v1/inter-verified.woff2";
  const interCss = `@font-face {
    font-family:'Inter';font-style:normal;font-weight:400;
    src:url('${interFontUrl}') format('woff2')
  }`;
  const collector = new GoogleFontsCollector({
    cacheDirectory,
    fetchImpl: async (input) => {
      const url = String(input);
      if (url === interCssUrl) return response(interCss, "text/css");
      if (url === interFontUrl) return response(VALID_WOFF2, "font/woff2");
      if (url === robotoCssUrl) return response("unavailable", "text/plain", { status: 503 });
      throw new Error(`unexpected URL: ${url}`);
    },
  });
  const prepared = await collector.prepareHtml(`<!doctype html><html><head>
    <link rel="stylesheet" href="${interCssUrl}">
    <style>@import '${robotoCssUrl}';
      .loaded{font-family:Inter,sans-serif}
      .failed{font-family:'Roboto',sans-serif}
    </style>
  </head><body></body></html>`);
  assert.equal(prepared.status, "partial");
  assert.equal(prepared.usedFallback, true);
  assert.equal(prepared.loadedStylesheets, 1);
  assert.equal(prepared.fontFiles, 1);
  assert.match(
    prepared.htmlForPreflight,
    /font-family:"Inter", "Aptos", sans-serif/
  );
  assert.match(
    prepared.htmlForPreflight,
    /font-family:"Roboto", "Aptos", sans-serif/
  );
  assert.match(prepared.htmlForChrome, /font-family:'Inter'/);
  assert.doesNotMatch(prepared.htmlForChrome, /font-family:'Roboto'.*data:font/s);
  assert.deepEqual(preflightAuthoredHtmlForHybrid(prepared.htmlForPreflight), {
    ok: true,
  });
});

test("large CJK stylesheets fetch only unicode ranges needed by local slide text", async (t) => {
  const cacheDirectory = await makeCacheDirectory(t);
  const matchingUrls = [
    "https://fonts.gstatic.com/s/notosanskr/v1/hangul-400.woff2",
    "https://fonts.gstatic.com/s/notosanskr/v1/hangul-700.woff2",
  ];
  const unusedUrls = [
    "https://fonts.gstatic.com/s/notosanskr/v1/unused-a.woff2",
    "https://fonts.gstatic.com/s/notosanskr/v1/unused-b.woff2",
  ];
  const rules = [
    [unusedUrls[0], "400", "U+10000-10001"],
    [matchingUrls[0], "400", "U+AC??"],
    [unusedUrls[1], "700", "U+10100-10101"],
    [matchingUrls[1], "700", "U+AC00-D7A3"],
  ]
    .map(
      ([url, weight, range]) => `@font-face {
        font-family:'Noto Sans KR';
        font-style:normal;
        font-weight:${weight};
        src:url('${url}') format('woff2');
        unicode-range:${range}
      }`
    )
    .join("\n");
  const largeCss = `/*${"x".repeat(300 * 1024)}*/\n${rules}`;
  const seen = [];
  const collector = new GoogleFontsCollector({
    cacheDirectory,
    maxFontFiles: 2,
    fetchImpl: async (input) => {
      const url = String(input);
      seen.push(url);
      if (url === CSS_URL) return response(largeCss, "text/css");
      if (matchingUrls.includes(url)) {
        return response(VALID_WOFF2, "font/woff2");
      }
      throw new Error(`unused unicode range must not be fetched: ${url}`);
    },
  });

  const prepared = await collector.prepareHtml(
    `<link rel="stylesheet" href="${CSS_URL}"><p>&#xAC01;</p>`
  );
  assert.equal(prepared.status, "success");
  assert.equal(prepared.fontFiles, 2);
  assert.deepEqual(prepared.trustedFamilies, ["Noto Sans KR"]);
  assert.deepEqual(seen, [CSS_URL, ...matchingUrls]);
  assert.equal(seen.some((url) => unusedUrls.includes(url)), false);
  assert.equal(seen.some((url) => /[?&]text=/i.test(url)), false);
});

test("every redirect hop is revalidated and cross-host redirects fail closed", async (t) => {
  const cacheDirectory = await makeCacheDirectory(t);
  const seen = [];
  const collector = new GoogleFontsCollector({
    cacheDirectory,
    fetchImpl: async (input) => {
      seen.push(String(input));
      return response("", "text/plain", {
        status: 302,
        headers: { location: "https://evil.example/steal.css" },
      });
    },
  });
  const prepared = await collector.prepareHtml(
    `<link rel="stylesheet" href="${CSS_URL}"><p>safe</p>`
  );
  assert.equal(prepared.status, "unavailable");
  assert.equal(prepared.usedFallback, true);
  assert.equal(prepared.loadedStylesheets, 0);
  assert.match(prepared.reason, /host-not-allowed/);
  assert.deepEqual(seen, [CSS_URL]);
  assert.deepEqual(preflightAuthoredHtmlForHybrid(prepared.htmlForPreflight), {
    ok: true,
  });
  assert.equal(prepared.htmlForChrome, prepared.htmlForPreflight);
});

test("same-policy redirect is followed with manual validation on each response", async (t) => {
  const cacheDirectory = await makeCacheDirectory(t);
  const redirectedCssUrl =
    "https://fonts.googleapis.com/css2?family=Noto+Sans+KR&display=block";
  const seen = [];
  const collector = new GoogleFontsCollector({
    cacheDirectory,
    fetchImpl: async (input) => {
      const url = String(input);
      seen.push(url);
      if (url === CSS_URL) {
        return response("", "text/plain", {
          status: 307,
          headers: { location: redirectedCssUrl },
        });
      }
      if (url === redirectedCssUrl) return response(VALID_CSS, "text/css");
      if (url === FONT_URL) return response(VALID_WOFF2, "font/woff2");
      throw new Error(`unexpected URL: ${url}`);
    },
  });
  const prepared = await collector.prepareHtml(
    `<link rel="stylesheet" href="${CSS_URL}"><p>A</p>`
  );
  assert.equal(prepared.status, "success");
  assert.deepEqual(seen, [CSS_URL, redirectedCssUrl, FONT_URL]);
});

test("CSS and WOFF2 enforce MIME, bounded bodies, and WOFF2 magic", async (t) => {
  const cases = [
    {
      name: "css MIME",
      fetchImpl: async () => response(VALID_CSS, "text/html"),
      reason: /css-mime-not-allowed/,
    },
    {
      name: "css size",
      options: { maxCssBytes: 16 },
      fetchImpl: async () => response(VALID_CSS, "text/css"),
      reason: /resource-too-large/,
    },
    {
      name: "woff2 MIME",
      fetchImpl: async (input) =>
        String(input) === CSS_URL
          ? response(VALID_CSS, "text/css")
          : response(VALID_WOFF2, "application/javascript"),
      reason: /woff2-mime-not-allowed/,
    },
    {
      name: "woff2 size",
      options: { maxFontBytes: 16 },
      fetchImpl: async (input) =>
        String(input) === CSS_URL
          ? response(VALID_CSS, "text/css")
          : response(VALID_WOFF2, "font/woff2"),
      reason: /resource-too-large/,
    },
    {
      name: "woff2 magic",
      fetchImpl: async (input) =>
        String(input) === CSS_URL
          ? response(VALID_CSS, "text/css")
          : response(Buffer.from("NOT_A_FONT"), "font/woff2"),
      reason: /magic-mismatch/,
    },
  ];

  for (const entry of cases) {
    await t.test(entry.name, async (t) => {
      const cacheDirectory = await makeCacheDirectory(t);
      const collector = new GoogleFontsCollector({
        cacheDirectory,
        fetchImpl: entry.fetchImpl,
        ...(entry.options ?? {}),
      });
      const prepared = await collector.prepareHtml(
        `<link rel="stylesheet" href="${CSS_URL}"><p>A</p>`
      );
      assert.equal(prepared.status, "unavailable");
      assert.match(prepared.reason, entry.reason);
      assert.equal(prepared.htmlForChrome, prepared.htmlForPreflight);
    });
  }
});

test("timeout aborts collection and reports fallback without exposing slide text", async (t) => {
  const cacheDirectory = await makeCacheDirectory(t);
  const seen = [];
  const collector = new GoogleFontsCollector({
    cacheDirectory,
    timeoutMs: 20,
    fetchImpl: async (input, init) => {
      seen.push(String(input));
      return await new Promise((_resolve, reject) => {
        init.signal.addEventListener(
          "abort",
          () => reject(new DOMException("aborted", "AbortError")),
          { once: true }
        );
      });
    },
  });
  const secretSlideText = "CONFIDENTIAL 분기 실적";
  const prepared = await collector.prepareHtml(
    `<link rel="stylesheet" href="${CSS_URL}"><p>${secretSlideText}</p>`
  );
  assert.equal(prepared.status, "unavailable");
  assert.equal(prepared.reason, "google-font-fetch-timeout");
  assert.deepEqual(seen, [CSS_URL]);
  assert.equal(seen.some((url) => url.includes(secretSlideText)), false);
  assert.equal(seen.some((url) => /[?&]text=/i.test(url)), false);
});

test("hash-addressed cache is reused and rejects tampered objects", async (t) => {
  const cacheDirectory = await makeCacheDirectory(t);
  const requests = [];
  const first = new GoogleFontsCollector({
    cacheDirectory,
    fetchImpl: createSuccessfulFetch(requests),
  });
  const html = `<link rel="stylesheet" href="${CSS_URL}"><p>A</p>`;
  assert.equal((await first.prepareHtml(html)).status, "success");
  assert.equal(requests.length, 2);

  const second = new GoogleFontsCollector({
    cacheDirectory,
    fetchImpl: async () => {
      throw new Error("valid cached resources should avoid network");
    },
  });
  assert.equal((await second.prepareHtml(html)).status, "success");

  const objectsDirectory = path.join(cacheDirectory, "objects");
  const objectNames = await fs.readdir(objectsDirectory);
  assert.ok(objectNames.every((name) => /^[a-f0-9]{64}\.(?:css|woff2)$/.test(name)));
  const fontObject = objectNames.find((name) => name.endsWith(".woff2"));
  assert.ok(fontObject);
  await fs.writeFile(path.join(objectsDirectory, fontObject), "tampered");

  const thirdRequests = [];
  const third = new GoogleFontsCollector({
    cacheDirectory,
    fetchImpl: createSuccessfulFetch(thirdRequests),
  });
  assert.equal((await third.prepareHtml(html)).status, "success");
  assert.deepEqual(thirdRequests.map((entry) => entry.url), [FONT_URL]);
});

test("disabled collection stays offline and falls back without any fetch", async () => {
  const cacheDirectory = await fs.mkdtemp(
    path.join(os.tmpdir(), "google-fonts-disabled-")
  );
  let fetches = 0;
  const collector = new GoogleFontsCollector({
    cacheDirectory,
    enabled: false,
    fetchImpl: async () => {
      fetches += 1;
      throw new Error("collection must not reach the network when disabled");
    },
  });

  const html = `<!doctype html><html><head><link rel="stylesheet" href="${CSS_URL}"></head><body><p>한글</p></body></html>`;
  const prepared = await collector.prepareHtml(html);

  assert.equal(fetches, 0);
  assert.equal(prepared.status, "unavailable");
  assert.equal(prepared.reason, "google-font-collection-disabled");
  assert.equal(prepared.usedFallback, true);
  assert.equal(prepared.requestedStylesheets, 1);
  assert.deepEqual(prepared.trustedFamilies, []);
  // The sanitized document is still returned, so the deck renders with the
  // local fallback stacks instead of failing the export.
  assert.equal(prepared.htmlForChrome, prepared.htmlForPreflight);
  assert.ok(!prepared.htmlForChrome.includes("fonts.googleapis.com"));
  assert.deepEqual(preflightAuthoredHtmlForHybrid(prepared.htmlForPreflight), {
    ok: true,
  });
});
