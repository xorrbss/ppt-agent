import assert from "node:assert/strict";
import test from "node:test";

import {
  BUNDLED_FONT_FAMILIES,
  bundledFontFamily,
  loadBundledBrowserFace,
} from "./bundled-fonts.ts";
import {
  documentUsesBoldFace,
  prepareBundledFontsHtml,
  referencedBundledFamilies,
} from "./bundled-fonts-collector.ts";
import { preflightTrustedSelfContainedAuthoredHtml } from "./security.ts";
import { AUTHORED_RENDER_LIMITS } from "./security-data.ts";

function prepared(html) {
  return {
    htmlForPreflight: html,
    htmlForChrome: html,
    status: "not-requested",
    usedFallback: false,
    requestedStylesheets: 0,
    requestedFontFaces: 0,
    loadedStylesheets: 0,
    fontFiles: 0,
    trustedFamilies: [],
    failures: [],
  };
}

const PRETENDARD_HTML =
  "<!doctype html><html><head><style>body{font-family:\"Pretendard\", \"Malgun Gothic\", sans-serif}</style></head>" +
  "<body><h1>분기 실적</h1><p>매출 12% 증가</p></body></html>";

test("bundled registry ships Pretendard regular and bold faces with a licence", async () => {
  const family = bundledFontFamily("'pretendard'");
  assert.ok(family);
  assert.equal(family.license, "OFL-1.1");
  assert.deepEqual(family.faces.map((face) => face.weight), [400, 700]);
  for (const face of family.faces) {
    const file = await loadBundledBrowserFace(family, face);
    assert.ok(file, `${face.browserFileName} must be bundled`);
    assert.equal(file.data.subarray(0, 4).toString("latin1"), "wOF2");
  }
  assert.equal(BUNDLED_FONT_FAMILIES.length, 1);
});

test("family references are found only in font declarations", () => {
  assert.deepEqual(
    referencedBundledFamilies(PRETENDARD_HTML).map((family) => family.family),
    ["Pretendard"],
  );
  assert.deepEqual(referencedBundledFamilies("<p>Pretendard is a font</p>"), []);
  assert.deepEqual(
    referencedBundledFamilies("<p style=\"font: 700 20px Pretendard\">x</p>").map((f) => f.family),
    ["Pretendard"],
  );
  assert.deepEqual(referencedBundledFamilies("<p style=\"font-family: PretendardX\">x</p>"), []);
});

test("bold delivery follows authored weight usage", () => {
  assert.equal(documentUsesBoldFace("<p style=\"font-weight:400\">x</p>"), false);
  assert.equal(documentUsesBoldFace("<p style=\"font-weight:600\">x</p>"), true);
  assert.equal(documentUsesBoldFace("<h2>x</h2>"), true);
  assert.equal(documentUsesBoldFace("<span class=\"font-semibold\">x</span>"), true);
});

test("Pretendard faces are injected as trusted data font faces for Chrome", async () => {
  const result = await prepareBundledFontsHtml(prepared(PRETENDARD_HTML));
  assert.deepEqual(result.bundled.families, ["Pretendard"]);
  assert.equal(result.bundled.fontFiles, 2);
  assert.equal(result.bundled.failures.length, 0);
  assert.equal(result.htmlForPreflight, PRETENDARD_HTML);
  assert.equal(result.fontFiles, 2);
  assert.deepEqual(result.trustedFamilies, ["Pretendard"]);
  assert.equal((result.htmlForChrome.match(/@font-face\{/g) ?? []).length, 2);
  assert.match(result.htmlForChrome, /font-family:'Pretendard';font-style:normal;font-weight:400;font-display:block;src:url\('data:font\/woff2;base64,/);
  assert.match(result.htmlForChrome, /font-weight:700/);
  assert.ok(Buffer.byteLength(result.htmlForChrome, "utf8") <= AUTHORED_RENDER_LIMITS.maxTrustedHtmlBytes);
  const preflight = preflightTrustedSelfContainedAuthoredHtml(result.htmlForChrome);
  assert.deepEqual(preflight, { ok: true });
});

test("only the regular face is delivered when nothing is bold", async () => {
  const html = "<html><head><style>p{font-family:Pretendard}</style></head><body><p>본문</p></body></html>";
  const result = await prepareBundledFontsHtml(prepared(html));
  assert.equal(result.bundled.fontFiles, 1);
  assert.doesNotMatch(result.htmlForChrome, /font-weight:700/);
});

test("documents without a bundled family reference are returned untouched", async () => {
  const html = "<html><body><p style=\"font-family:'Noto Sans KR'\">x</p></body></html>";
  const result = await prepareBundledFontsHtml(prepared(html));
  assert.equal(result.htmlForChrome, html);
  assert.equal(result.bundled.fontFiles, 0);
});

test("the render font-face ceiling is honoured as a whole-family failure", async () => {
  const faces = Array.from(
    { length: AUTHORED_RENDER_LIMITS.maxFontFaces - 1 },
    (_, index) => `@font-face{font-family:'F${index}';src:url('data:font/woff2;base64,AA==')}`,
  ).join("");
  const input = prepared(PRETENDARD_HTML);
  const result = await prepareBundledFontsHtml({
    ...input,
    htmlForChrome: `<html><head><style>${faces}</style></head><body>x</body></html>`,
  });
  assert.deepEqual(result.bundled.failures, [
    { family: "Pretendard", reason: "bundled-font-limit-exceeded" },
  ]);
  assert.equal(result.bundled.fontFiles, 0);
});
