import assert from "node:assert/strict";
import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import test from "node:test";

import { prepareBundledFontsHtml } from "./bundled-fonts-collector.ts";
import {
  GoogleFontsCollector,
  validateGoogleFontsCssUrl,
} from "./google-fonts-collector.ts";
import { stripBundledFamiliesFromGoogleFontsCssUrl } from "./google-fonts-local-first.ts";
import { preflightTrustedSelfContainedAuthoredHtml } from "./security.ts";

const NOTO_CSS_URL =
  "https://fonts.googleapis.com/css2?family=Noto+Sans+KR:wght@400;700&display=swap";
const MIXED_CSS_URL =
  "https://fonts.googleapis.com/css2?family=Noto+Sans+KR:wght@400;700&family=Pretendard:wght@400;700&display=swap";
const PRETENDARD_ONLY_CSS_URL =
  "https://fonts.googleapis.com/css2?family=Pretendard:wght@400;700&display=swap";
const NOTO_FONT_URL = "https://fonts.gstatic.com/s/notosanskr/v1/verified-subset.woff2";
const VALID_WOFF2 = Buffer.concat([Buffer.from("wOF2", "ascii"), Buffer.alloc(44)]);
const NOTO_CSS = `@font-face {
  font-family: 'Noto Sans KR';
  font-style: normal;
  font-weight: 400 700;
  font-display: swap;
  src: url(${NOTO_FONT_URL}) format('woff2');
  unicode-range: U+AC00-D7A3, U+0020-007E;
}`;

function deck(linkUrl, stack) {
  const link = linkUrl
    ? `<link rel="stylesheet" href="${linkUrl.replace(/&/g, "&amp;")}">`
    : "";
  return (
    `<!doctype html><html><head><meta charset="utf-8">${link}` +
    `<style>body{font-family:${stack}}h1{font-weight:700}</style></head>` +
    "<body><h1>분기 실적</h1><p>매출 12% 증가</p></body></html>"
  );
}

async function makeCacheDirectory(t) {
  const directory = await fs.mkdtemp(path.join(os.tmpdir(), "presenton-local-first-test-"));
  t.after(async () => {
    await fs.rm(directory, { recursive: true, force: true });
  });
  return directory;
}

function recordingFetch(requests) {
  return async (input) => {
    const url = String(input);
    requests.push(url);
    if (/Pretendard/i.test(url)) throw new Error(`bundled family requested from Google: ${url}`);
    const body = url.endsWith(".woff2") ? VALID_WOFF2 : NOTO_CSS;
    const contentType = url.endsWith(".woff2") ? "font/woff2" : "text/css; charset=utf-8";
    return new Response(body, { status: 200, headers: { "content-type": contentType } });
  };
}

test("bundled families are stripped from css2 and legacy css URLs before any request", () => {
  const mixed = stripBundledFamiliesFromGoogleFontsCssUrl(validateGoogleFontsCssUrl(MIXED_CSS_URL));
  assert.deepEqual(mixed.localFamilies, ["Pretendard"]);
  assert.deepEqual(mixed.url.searchParams.getAll("family"), ["Noto Sans KR:wght@400;700"]);
  assert.equal(mixed.url.searchParams.get("display"), "swap");
  assert.doesNotThrow(() => validateGoogleFontsCssUrl(mixed.url.href));

  const only = stripBundledFamiliesFromGoogleFontsCssUrl(validateGoogleFontsCssUrl(PRETENDARD_ONLY_CSS_URL));
  assert.equal(only.url, null);
  assert.deepEqual(only.localFamilies, ["Pretendard"]);

  const legacy = stripBundledFamiliesFromGoogleFontsCssUrl(
    validateGoogleFontsCssUrl("https://fonts.googleapis.com/css?family=Pretendard:400,700&family=Noto+Sans+KR:400"),
  );
  assert.deepEqual(legacy.localFamilies, ["Pretendard"]);
  assert.deepEqual(legacy.url.searchParams.getAll("family"), ["Noto Sans KR:400"]);
  assert.equal(
    stripBundledFamiliesFromGoogleFontsCssUrl(
      validateGoogleFontsCssUrl("https://fonts.googleapis.com/css?family=Pretendard:400,700"),
    ).url,
    null,
  );

  // A URL without bundled families is returned untouched, same object.
  const untouched = validateGoogleFontsCssUrl(NOTO_CSS_URL);
  const result = stripBundledFamiliesFromGoogleFontsCssUrl(untouched);
  assert.equal(result.url, untouched);
  assert.deepEqual(result.localFamilies, []);
});

test("a deck whose only Google request is a bundled family never contacts Google", async (t) => {
  const requests = [];
  const collector = new GoogleFontsCollector({
    cacheDirectory: await makeCacheDirectory(t),
    enabled: true,
    fetchImpl: recordingFetch(requests),
  });
  const html = deck(PRETENDARD_ONLY_CSS_URL, "Pretendard, sans-serif");
  const prepared = await collector.prepareHtml(html);
  assert.deepEqual(requests, []);
  assert.equal(prepared.status, "not-requested");
  assert.equal(prepared.requestedStylesheets, 0);
  assert.deepEqual(prepared.localFamilies, ["Pretendard"]);
  assert.doesNotMatch(prepared.htmlForPreflight, /fonts\.googleapis\.com/);

  const withBundled = await prepareBundledFontsHtml(prepared);
  assert.deepEqual(withBundled.bundled.families, ["Pretendard"]);
  assert.equal(withBundled.bundled.fontFiles, 2);
  assert.deepEqual(withBundled.trustedFamilies, ["Pretendard"]);
  assert.match(withBundled.htmlForChrome, /font-family:'Pretendard';font-style:normal;font-weight:400/);
  assert.deepEqual(preflightTrustedSelfContainedAuthoredHtml(withBundled.htmlForChrome), { ok: true });
});

test("a mixed request fetches only the non-bundled families and keeps Noto Sans KR as itself", async (t) => {
  const requests = [];
  const collector = new GoogleFontsCollector({
    cacheDirectory: await makeCacheDirectory(t),
    enabled: true,
    fetchImpl: recordingFetch(requests),
  });
  const html = deck(MIXED_CSS_URL, '"Noto Sans KR", Pretendard, sans-serif');
  const prepared = await collector.prepareHtml(html);
  assert.equal(prepared.status, "success", JSON.stringify(prepared.failures));
  assert.equal(requests.length, 2);
  assert.ok(requests.every((url) => !/pretendard/i.test(url)), requests.join("\n"));
  assert.match(requests[0], /^https:\/\/fonts\.googleapis\.com\/css2\?/);
  assert.equal(new URL(requests[0]).searchParams.getAll("family").join(), "Noto Sans KR:wght@400;700");
  assert.deepEqual(prepared.localFamilies, ["Pretendard"]);
  assert.deepEqual(prepared.trustedFamilies, ["Noto Sans KR"]);

  const withBundled = await prepareBundledFontsHtml(prepared);
  assert.deepEqual(withBundled.trustedFamilies, ["Noto Sans KR", "Pretendard"]);
  assert.match(withBundled.htmlForChrome, /font-family:'Noto Sans KR'/);
  assert.match(withBundled.htmlForChrome, /font-family:'Pretendard'/);
});

test("a family outside the manifest keeps today's Google path unchanged", async (t) => {
  const requests = [];
  const collector = new GoogleFontsCollector({
    cacheDirectory: await makeCacheDirectory(t),
    enabled: true,
    fetchImpl: recordingFetch(requests),
  });
  const prepared = await collector.prepareHtml(deck(NOTO_CSS_URL, '"Noto Sans KR", sans-serif'));
  assert.equal(prepared.status, "success");
  assert.equal(requests[0], NOTO_CSS_URL);
  assert.deepEqual(prepared.localFamilies, []);
  const withBundled = await prepareBundledFontsHtml(prepared);
  assert.deepEqual(withBundled.bundled.families, []);
  assert.equal(withBundled.htmlForChrome, prepared.htmlForChrome);
});

test("an inline Google @font-face for a bundled family is dropped without a fetch", async (t) => {
  const requests = [];
  const collector = new GoogleFontsCollector({
    cacheDirectory: await makeCacheDirectory(t),
    enabled: true,
    fetchImpl: recordingFetch(requests),
  });
  const html =
    "<!doctype html><html><head><style>@font-face{font-family:'Pretendard';font-style:normal;font-weight:400;" +
    "src:url(https://fonts.gstatic.com/s/pretendard/v1/Pretendard.woff2) format('woff2')}" +
    "body{font-family:Pretendard}</style></head><body><p>본문</p></body></html>";
  const prepared = await collector.prepareHtml(html);
  assert.deepEqual(requests, []);
  assert.equal(prepared.requestedFontFaces, 0);
  assert.deepEqual(prepared.localFamilies, ["Pretendard"]);
  assert.doesNotMatch(prepared.htmlForPreflight, /@font-face/);
});

test("DISABLE_GOOGLE_FONTS_COLLECTION renders a Pretendard deck identically to a networked host", async (t) => {
  const cacheDirectory = await makeCacheDirectory(t);
  const offline = new GoogleFontsCollector({ cacheDirectory, enabled: false });
  const online = new GoogleFontsCollector({
    cacheDirectory,
    enabled: true,
    fetchImpl: async (input) => {
      throw new Error(`unexpected network access: ${String(input)}`);
    },
  });
  for (const html of [
    // The shape 2,012 generated fixtures use: a plain stack, no Google link.
    deck(null, "Pretendard, 'Malgun Gothic', sans-serif"),
    // A document that also asked Google for Pretendard.
    deck(PRETENDARD_ONLY_CSS_URL, "Pretendard, sans-serif"),
  ]) {
    const [offlineResult, onlineResult] = await Promise.all([
      offline.prepareHtml(html).then(prepareBundledFontsHtml),
      online.prepareHtml(html).then(prepareBundledFontsHtml),
    ]);
    assert.equal(offlineResult.htmlForChrome, onlineResult.htmlForChrome);
    assert.deepEqual(offlineResult.trustedFamilies, ["Pretendard"]);
    assert.equal(offlineResult.bundled.fontFiles, 2);
    assert.deepEqual(offlineResult.failures, []);
    assert.deepEqual(preflightTrustedSelfContainedAuthoredHtml(offlineResult.htmlForChrome), { ok: true });
  }
});
