import assert from "node:assert/strict";
import test from "node:test";

import {
  clearGoogleFontCollectorCacheForTests,
  collectGoogleFontsForAuthoredHtml,
} from "./google-font-collector.ts";
import { preflightAuthoredHtmlForHybrid } from "./security.ts";

const cssUrl = "https://fonts.googleapis.com/css2?family=Inter:wght@400";
const fontUrl = "https://fonts.gstatic.com/s/inter/v1/test.woff2";
const woff2 = Buffer.concat([Buffer.from("wOF2"), Buffer.alloc(32, 7)]);
const css = `@font-face{font-family:'Inter';font-style:normal;font-weight:400;src:url(${fontUrl}) format('woff2')}`;

function successfulFetch(counter) {
  return async (input) => {
    const url = String(input);
    counter.set(url, (counter.get(url) ?? 0) + 1);
    if (url === cssUrl) {
      return new Response(css, {
        headers: { "content-type": "text/css; charset=utf-8" },
      });
    }
    if (url === fontUrl) {
      return new Response(woff2, {
        headers: { "content-type": "font/woff2" },
      });
    }
    return new Response("not found", { status: 404 });
  };
}

test.beforeEach(() => clearGoogleFontCollectorCacheForTests());

test("Google Fonts link is collected server-side into magic-validated data", async () => {
  const calls = new Map();
  const result = await collectGoogleFontsForAuthoredHtml(
    `<html><head><link rel="stylesheet" href="${cssUrl.replace("&", "&amp;")}"></head><body style="font-family:Inter">x</body></html>`,
    { fetchImpl: successfulFetch(calls) }
  );

  assert.equal(result.collectedStylesheets, 1);
  assert.equal(result.collectedFontFiles, 1);
  assert.equal(result.failures.length, 0);
  assert.equal(calls.get(cssUrl), 1);
  assert.equal(calls.get(fontUrl), 1);
  assert.match(result.html, /data-presenton-google-fonts="collected"/);
  assert.match(result.html, /data:font\/woff2;base64,/);
  assert.doesNotMatch(result.html, /fonts\.(?:googleapis|gstatic)\.com/);
  assert.deepEqual(preflightAuthoredHtmlForHybrid(result.html), { ok: true });
});

test("existing CSS @import is collected and repeat use hits the cache", async () => {
  const calls = new Map();
  const fetchImpl = successfulFetch(calls);
  const html = `<html><head><style>@import url("${cssUrl}");</style></head><body>x</body></html>`;
  const first = await collectGoogleFontsForAuthoredHtml(html, { fetchImpl });
  const second = await collectGoogleFontsForAuthoredHtml(html, { fetchImpl });

  assert.equal(first.collectedStylesheets, 1);
  assert.equal(first.cacheHits, 0);
  assert.equal(second.collectedStylesheets, 1);
  assert.equal(second.cacheHits, 1);
  assert.equal(calls.get(cssUrl), 1);
  assert.equal(calls.get(fontUrl), 1);
  assert.doesNotMatch(second.html, /@import/i);
  assert.deepEqual(preflightAuthoredHtmlForHybrid(second.html), { ok: true });
});

test("network failure removes only the Google reference and retains local fallback", async () => {
  const result = await collectGoogleFontsForAuthoredHtml(
    `<link rel="stylesheet" href="${cssUrl}"><p style="font-family:Inter,Aptos">x</p>`,
    {
      fetchImpl: async () => {
        throw new TypeError("network unavailable");
      },
    }
  );

  assert.equal(result.collectedStylesheets, 0);
  assert.equal(result.failures.length, 1);
  assert.equal(result.failures[0].source, "link");
  assert.doesNotMatch(result.html, /fonts\.googleapis\.com/);
  assert.match(result.html, /font-family:Inter,Aptos/);
  assert.deepEqual(preflightAuthoredHtmlForHybrid(result.html), { ok: true });
});

test("redirect hosts, CSS MIME, and font magic fail closed", async (t) => {
  await t.test("same-host HTTPS redirect is revalidated and followed", async () => {
    const redirectedCssUrl =
      "https://fonts.googleapis.com/css2?family=Inter:wght@400&display=swap";
    const result = await collectGoogleFontsForAuthoredHtml(
      `<link rel="stylesheet" href="${cssUrl}">`,
      {
        fetchImpl: async (input) => {
          const url = String(input);
          if (url === cssUrl) {
            return new Response(null, {
              status: 302,
              headers: { location: redirectedCssUrl },
            });
          }
          if (url === redirectedCssUrl) {
            return new Response(css, {
              headers: { "content-type": "text/css" },
            });
          }
          if (url === fontUrl) {
            return new Response(woff2, {
              headers: { "content-type": "font/woff2" },
            });
          }
          return new Response(null, { status: 404 });
        },
      }
    );
    assert.equal(result.collectedStylesheets, 1);
    assert.equal(result.failures.length, 0);
  });

  await t.test("cross-host redirect", async () => {
    const result = await collectGoogleFontsForAuthoredHtml(
      `<link rel="stylesheet" href="${cssUrl}">`,
      {
        fetchImpl: async () =>
          new Response(null, {
            status: 302,
            headers: { location: "https://example.com/fonts.css" },
          }),
      }
    );
    assert.equal(result.failures[0].reason, "disallowed-css-url");
  });

  await t.test("wrong stylesheet MIME", async () => {
    const result = await collectGoogleFontsForAuthoredHtml(
      `<link rel="stylesheet" href="${cssUrl}">`,
      {
        fetchImpl: async () =>
          new Response(css, { headers: { "content-type": "text/html" } }),
      }
    );
    assert.equal(result.failures[0].reason, "invalid-css-mime");
  });

  await t.test("wrong font magic", async () => {
    const result = await collectGoogleFontsForAuthoredHtml(
      `<link rel="stylesheet" href="${cssUrl}">`,
      {
        fetchImpl: async (input) =>
          String(input) === cssUrl
            ? new Response(css, {
                headers: { "content-type": "text/css" },
              })
            : new Response(Buffer.from("not-a-font"), {
                headers: { "content-type": "font/woff2" },
              }),
      }
    );
    assert.equal(result.failures[0].reason, "invalid-font-magic");
  });
});

test("active HTML and stylesheet fan-out are rejected before network access", async (t) => {
  await t.test("active HTML", async () => {
    let calls = 0;
    const html = `<link rel="stylesheet" href="${cssUrl}"><script>alert(1)</script>`;
    const result = await collectGoogleFontsForAuthoredHtml(html, {
      fetchImpl: async () => {
        calls += 1;
        throw new Error("must not fetch");
      },
    });
    assert.equal(calls, 0);
    assert.equal(result.html, html);
  });

  await t.test("more than eight approved stylesheet references", async () => {
    let calls = 0;
    const html = Array.from(
      { length: 9 },
      (_, index) =>
        `<link rel="stylesheet" href="${cssUrl}&subset=${index}">`
    ).join("");
    const result = await collectGoogleFontsForAuthoredHtml(html, {
      fetchImpl: async () => {
        calls += 1;
        throw new Error("must not fetch");
      },
    });
    assert.equal(calls, 0);
    assert.equal(result.html, html);
  });
});

test("font timeout and declared size limit fail closed", async (t) => {
  await t.test("timeout", async () => {
    const result = await collectGoogleFontsForAuthoredHtml(
      `<link rel="stylesheet" href="${cssUrl}">`,
      {
        timeoutMs: 2,
        fetchImpl: async (_input, init) =>
          await new Promise((_resolve, reject) => {
            init.signal.addEventListener(
              "abort",
              () =>
                reject(
                  new DOMException("The operation was aborted", "AbortError")
                ),
              { once: true }
            );
          }),
      }
    );
    assert.equal(result.failures[0].reason, "network-timeout");
  });

  await t.test("declared font size over 8 MiB", async () => {
    const result = await collectGoogleFontsForAuthoredHtml(
      `<link rel="stylesheet" href="${cssUrl}">`,
      {
        fetchImpl: async (input) =>
          String(input) === cssUrl
            ? new Response(css, {
                headers: { "content-type": "text/css" },
              })
            : new Response(woff2, {
                headers: {
                  "content-type": "font/woff2",
                  "content-length": String(8 * 1024 * 1024 + 1),
                },
              }),
      }
    );
    assert.equal(result.failures[0].reason, "upstream-size-limit");
  });
});
