import assert from "node:assert/strict";
import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

import {
  extractAuthoredSlideDom,
  resolveAuthoredHybridChromeExecutable,
} from "./index.ts";
import { GoogleFontsCollector } from "./google-fonts-collector.ts";
import {
  prepareNativeElements,
  serializePreparedNativeElement,
} from "./native-plan.ts";
import { preflightAuthoredHtmlForHybrid } from "./security.ts";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const FIXTURE_PATH = path.join(
  HERE,
  "fixtures",
  "legacy-google-fonts.html"
);
const TEST_WOFF2_PATH = path.resolve(
  HERE,
  "..",
  "..",
  "node_modules",
  "katex",
  "dist",
  "fonts",
  "KaTeX_SansSerif-Regular.woff2"
);

function textElement(slide, plainText) {
  return slide.elements.find(
    (element) =>
      "text" in element && element.text.plainText.includes(plainText)
  );
}

test("raw legacy Google Fonts HTML still fails the untrusted preflight", async () => {
  const html = await fs.readFile(FIXTURE_PATH, "utf8");

  assert.deepEqual(preflightAuthoredHtmlForHybrid(html), {
    ok: false,
    reason: "active-or-external-html",
  });
});

test("untrusted HTML cannot self-authorize a data font face", () => {
  const html = `<!doctype html><style>
    @font-face {
      font-family: "Injected";
      src: url("data:font/woff2;base64,d09GMgAAAAA=") format("woff2");
    }
    body { font-family: "Injected", sans-serif; }
  </style><p>must stay blocked</p>`;

  assert.deepEqual(preflightAuthoredHtmlForHybrid(html), {
    ok: false,
    reason: "unsafe-css-url",
  });
});

test("trusted collection makes legacy Google Fonts HTML editable in offline Chrome", async (t) => {
  const chromeExecutable = await resolveAuthoredHybridChromeExecutable();
  if (!chromeExecutable) {
    if (process.env.CI === "true") {
      assert.fail("CI must provide Chrome/Chromium for Google Fonts compatibility");
    }
    t.skip("Chrome/Chromium is unavailable");
    return;
  }

  const [html, woff2] = await Promise.all([
    fs.readFile(FIXTURE_PATH, "utf8"),
    fs.readFile(TEST_WOFF2_PATH),
  ]);
  const cacheDirectory = await fs.mkdtemp(
    path.join(os.tmpdir(), "presenton-google-fonts-test-")
  );
  t.after(() => fs.rm(cacheDirectory, { recursive: true, force: true }));

  const fetched = [];
  const fetchImpl = async (input, init) => {
    const url = String(input);
    fetched.push({ url, init });
    if (url.startsWith("https://fonts.googleapis.com/css2?")) {
      const isInter = new URL(url).searchParams
        .getAll("family")
        .some((family) => family.startsWith("Inter"));
      const family = isInter ? "Inter" : "Noto Sans KR";
      const slug = isInter ? "inter" : "notosanskr";
      return new Response(
        `@font-face {
          font-family: '${family}';
          font-style: normal;
          font-weight: ${isInter ? "600" : "700"};
          src: url(https://fonts.gstatic.com/s/${slug}/v1/${slug}.woff2) format('woff2');
        }`,
        { headers: { "content-type": "text/css" } }
      );
    }
    if (url.startsWith("https://fonts.gstatic.com/s/")) {
      return new Response(woff2, {
        headers: {
          "content-type": "font/woff2",
          "content-length": String(woff2.length),
        },
      });
    }
    throw new Error(`unexpected test fetch: ${url}`);
  };

  const prepared = await new GoogleFontsCollector({
    cacheDirectory,
    fetchImpl,
  }).prepareHtml(html);

  assert.equal(prepared.status, "success");
  assert.equal(prepared.usedFallback, false);
  assert.equal(prepared.requestedStylesheets, 2);
  assert.equal(prepared.requestedFontFaces, 1);
  assert.equal(prepared.loadedStylesheets, 2);
  assert.equal(prepared.fontFiles, 3);
  assert.deepEqual(
    new Set(prepared.trustedFamilies),
    new Set(["Noto Sans KR", "Inter", "Roboto"])
  );
  assert.deepEqual(prepared.failures, []);
  assert.deepEqual(preflightAuthoredHtmlForHybrid(prepared.htmlForPreflight), {
    ok: true,
  });
  assert.match(
    prepared.htmlForChrome,
    /data-presenton-trusted-google-fonts="v1"/
  );
  assert.doesNotMatch(
    prepared.htmlForChrome,
    /https:\/\/fonts\.(?:googleapis|gstatic)\.com/i
  );

  assert.equal(fetched.length, 5);
  for (const { init } of fetched) {
    assert.equal(init.redirect, "manual");
    assert.equal(init.credentials, "omit");
    const headers = new Headers(init.headers);
    assert.equal(headers.has("cookie"), false);
    assert.equal(headers.has("authorization"), false);
  }
  const fetchesBeforeChrome = fetched.length;
  const slide = await extractAuthoredSlideDom(prepared.htmlForChrome, {
    chromeExecutable,
    timeoutMs: 30_000,
  });
  assert.equal(
    fetched.length,
    fetchesBeforeChrome,
    "Chrome must not perform any collector fetches"
  );

  const title = textElement(slide, "편집 가능한 Google Fonts 호환성");
  const metric = textElement(slide, "Editable text remains native");
  const direct = textElement(slide, "Direct remote font-face remains editable");
  assert.ok(title, "Korean text must be extracted as an editable text element");
  assert.ok(metric, "Latin text must be extracted as an editable text element");
  assert.ok(direct, "Direct gstatic font-face text must remain editable");
  assert.equal(title.classification.mode, "native");
  assert.equal(metric.classification.mode, "native");
  assert.equal(direct.classification.mode, "native");

  const native = await prepareNativeElements([title, metric, direct], {
    trustedFontFamilies: prepared.trustedFamilies,
  });
  assert.deepEqual(native.map((element) => element.kind), [
    "text",
    "text",
    "text",
  ]);
  const titleXml = serializePreparedNativeElement(native[0], 10);
  const metricXml = serializePreparedNativeElement(native[1], 11);
  const directXml = serializePreparedNativeElement(native[2], 12);
  assert.match(titleXml, /<a:latin typeface="Noto Sans KR"\/>/);
  assert.match(titleXml, /<a:ea typeface="Noto Sans KR"\/>/);
  assert.match(metricXml, /<a:latin typeface="Inter"\/>/);
  assert.match(directXml, /<a:latin typeface="Roboto"\/>/);
});
