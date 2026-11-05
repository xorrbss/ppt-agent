import assert from "node:assert/strict";
import { existsSync } from "node:fs";
import path from "node:path";
import test from "node:test";

import {
  buildSvgPreviewSrcDoc,
  bundledFamiliesUsedBySvg,
  previewFontFacesForSvg,
} from "./svg-preview-fonts.ts";
import { svgPreviewPrefetchTarget } from "./svg-slide-revision.ts";

const ORIGIN = "https://deck.example.test";
const publicDir = path.join(import.meta.dirname, "..", "public", "fonts");

const svg = (family: string) =>
  `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10"><text font-family="${family}">가</text></svg>`;

function cspOf(doc: string): Map<string, string> {
  const content = /http-equiv="Content-Security-Policy" content="([^"]*)"/u.exec(doc)?.[1] ?? "";
  return new Map(
    content.split("; ").map((directive) => {
      const [name, ...rest] = directive.split(" ");
      return [name, rest.join(" ")] as const;
    }),
  );
}

test("iframe document declares the bundled family and CSP allows only that font path", () => {
  const doc = buildSvgPreviewSrcDoc(svg("Pretendard, sans-serif"), ORIGIN, false);
  assert.match(doc, /@font-face\{font-family:'Pretendard'/u);
  const csp = cspOf(doc);
  assert.deepEqual((csp.get("font-src") ?? "").split(" ").sort(), [
    `${ORIGIN}/fonts/pretendard/Pretendard-Bold.woff2`,
    `${ORIGIN}/fonts/pretendard/Pretendard-Regular.woff2`,
  ]);
  assert.doesNotMatch(doc, /Noto Sans KR/u);
  for (const directive of ["default-src", "connect-src", "script-src", "base-uri", "object-src"]) {
    assert.equal(csp.get(directive), "'none'", directive);
  }
});

test("Noto Sans KR is served from its bundled variable file", () => {
  const doc = buildSvgPreviewSrcDoc(svg("'Noto Sans KR'"), ORIGIN, false);
  assert.match(doc, /font-family:'Noto Sans KR'[^}]*font-weight:100 900/u);
  assert.equal(cspOf(doc).get("font-src"), `${ORIGIN}/fonts/noto-sans-kr/NotoSansKR-Variable.ttf`);
});

test("every face the preview can reference exists under public/fonts", () => {
  const faces = previewFontFacesForSvg(svg("Pretendard, Noto Sans KR"));
  assert.equal(faces.length, 3);
  for (const face of faces) {
    assert.ok(existsSync(path.join(publicDir, face.path)), face.path);
  }
});

test("svg naming no bundled family keeps font-src none and no @font-face", () => {
  const doc = buildSvgPreviewSrcDoc(svg("Arial"), ORIGIN, false);
  assert.equal(cspOf(doc).get("font-src"), "'none'");
  assert.doesNotMatch(doc, /@font-face/u);
  assert.deepEqual(bundledFamiliesUsedBySvg(svg("Arial")), []);
});

test("fixed-glyph previews keep data: fonts and add no network font", () => {
  const doc = buildSvgPreviewSrcDoc(svg("Pretendard"), ORIGIN, true);
  assert.equal(cspOf(doc).get("font-src"), "data:");
  assert.doesNotMatch(doc, /@font-face/u);
});

test("style-declared families are found and scripts are stripped", () => {
  const doc = buildSvgPreviewSrcDoc(
    `<svg><script>x()</script><text style="fill:red;font-family:'Pretendard'">a</text></svg>`,
    ORIGIN,
    false,
  );
  assert.match(doc, /@font-face/u);
  assert.doesNotMatch(doc, /<script/u);
});

test("presenter prefetch targets only slides that need materialization", () => {
  assert.equal(svgPreviewPrefetchTarget(null), null);
  assert.equal(svgPreviewPrefetchTarget({ id: "a", svg_content: "<svg/>" }), null);
  assert.deepEqual(svgPreviewPrefetchTarget({ id: "a", svg_content: '<svg id="source-slot-1"/>' }), {
    slideId: "a",
    revision: '<svg id="source-slot-1"/>',
  });
  assert.deepEqual(
    svgPreviewPrefetchTarget({ id: "a", content: { svgAssetManifest: { slideRevision: "r1" } } }),
    { slideId: "a", revision: "r1" },
  );
  assert.equal(
    svgPreviewPrefetchTarget({ id: "a", content: { svgReceipt: { fixedGlyphPreview: {} } } }),
    null,
  );
});
