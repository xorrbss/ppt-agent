import assert from "node:assert/strict";
import test from "node:test";

import {
  bundledFamiliesReferencedByHtml,
  nativeEmbeddingFamilies,
  prepareFontEmbedding,
} from "./export-font-embedding.ts";
import { resolveFontEmbeddingPlan } from "./font-embedding-policy.ts";
import { resolveScriptAwareTypeface } from "./font-policy.ts";
import { preflightNativeTypography } from "./native-plan.ts";
import { readPptxArchive, writePptxArchive } from "./pptx-archive.ts";

const PRETENDARD_STACK = ["Pretendard", "Malgun Gothic", "sans-serif"];
const embeddedPretendard = [
  { typeface: "Pretendard", face: "regular" },
  { typeface: "Pretendard", face: "bold" },
];

function skeletonPptx() {
  return writePptxArchive(new Map([
    ["[Content_Types].xml", Buffer.from('<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="xml" ContentType="application/xml"/><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/></Types>')],
    ["ppt/presentation.xml", Buffer.from('<?xml version="1.0"?><p:presentation xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"><p:sldSz cx="12192000" cy="6858000"/></p:presentation>')],
    ["ppt/_rels/presentation.xml.rels", Buffer.from('<?xml version="1.0"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"/>')],
  ]));
}

test("Hangul runs keep an authored Pretendard stack only when that face is embedded", () => {
  assert.deepEqual(resolveScriptAwareTypeface("cjk", PRETENDARD_STACK), {
    typeface: "Noto Sans KR",
    fallbackTypeface: "Malgun Gothic",
    requiresEmbedding: true,
  });
  assert.deepEqual(
    resolveScriptAwareTypeface("cjk", PRETENDARD_STACK, { embeddedTypefaceFaces: embeddedPretendard, face: "bold" }),
    { typeface: "Pretendard", fallbackTypeface: "Malgun Gothic", requiresEmbedding: true },
  );
  // Noto Sans KR maps to the same Windows substitute but is not Pretendard.
  assert.equal(
    resolveScriptAwareTypeface("cjk", PRETENDARD_STACK, {
      embeddedTypefaceFaces: [{ typeface: "Noto Sans KR", face: "bold" }],
      face: "bold",
    }).typeface,
    "Noto Sans KR",
  );
  assert.equal(
    resolveScriptAwareTypeface("cjk", ["Inter", "sans-serif"], { embeddedTypefaceFaces: embeddedPretendard, face: "regular" }).typeface,
    "Noto Sans KR",
  );
  assert.equal(
    resolveScriptAwareTypeface("cjk", ["Pretendard", "Batang", "serif"], { embeddedTypefaceFaces: embeddedPretendard, face: "regular" }).typeface,
    "Noto Serif KR",
  );
});

test("stored slide HTML decides whether Pretendard joins the embedding plan", () => {
  assert.deepEqual(bundledFamiliesReferencedByHtml(["<p>x</p>", "<style>body{font-family:Pretendard}</style>"]), ["Pretendard"]);
  assert.deepEqual(bundledFamiliesReferencedByHtml(["<p>Pretendard</p>"]), []);
  const binding = { slides: [{ fontFamilies: ["Inter"] }] };
  assert.deepEqual(nativeEmbeddingFamilies(binding, undefined, ["Pretendard"]), ["Inter", "Pretendard"]);
  assert.deepEqual(nativeEmbeddingFamilies(binding, undefined), ["Inter"]);
});

test("typography preflight admits Hangul Pretendard runs against the exact embedded face", () => {
  const run = (text, fontWeight) => ({
    text,
    bounds: { px: { x: 0, y: 0, width: 100, height: 20 }, inches: { x: 0, y: 0, width: 1, height: 0.2 } },
    fragments: [],
    style: {
      fontFamily: "Pretendard",
      fontFamilies: PRETENDARD_STACK,
      cjkFallbackFamilies: ["Malgun Gothic"],
      fontSizePt: 14,
      fontWeight,
      bold: fontWeight >= 700,
      italic: false,
      underline: false,
      strike: false,
      color: { hex: "101828", alpha: 1 },
      lineHeight: { points: 20, multiple: 1.4 },
      letterSpacingPt: 0,
      textAlign: "left",
      direction: "ltr",
    },
  });
  const element = {
    id: "text.1",
    domPath: "body > p:nth-of-type(1)",
    tagName: "p",
    sourceIndex: 1,
    zOrder: 1,
    cssZIndex: null,
    bounds: { px: { x: 0, y: 0, width: 100, height: 20 }, inches: { x: 0, y: 0, width: 1, height: 0.2 } },
    rotationDeg: 0,
    opacity: 1,
    classification: { mode: "native", kind: "text", confidence: "safe" },
    text: {
      role: "body",
      plainText: "매출 12%",
      paragraphs: ["매출 12%"],
      style: run("", 400).style,
      runs: [run("매출 ", 400), run("12%", 700)],
    },
  };
  const withPretendard = preflightNativeTypography([element], { embeddedTypefaceFaces: embeddedPretendard });
  assert.deepEqual(withPretendard.rejectedElementIds, []);
  const rewritten = withPretendard.elements[0].text.runs;
  assert.equal(rewritten[0].style.fontFamily, "Pretendard");
  assert.deepEqual(rewritten[0].style.fontFamilies, ["Pretendard", "Malgun Gothic", "sans-serif"]);
  const withoutFonts = preflightNativeTypography([element], { embeddedTypefaceFaces: [] });
  assert.deepEqual(withoutFonts.rejectedElementIds, ["text.1"]);
  assert.equal(withoutFonts.typographyIssues[0].requiredTypeface, "Noto Sans KR");
});

test("bundled Pretendard resolves into an eligible full-embedding plan and packages into PPTX", async () => {
  const plan = await resolveFontEmbeddingPlan({
    requested: true,
    families: ["Pretendard"],
    strategy: "full",
    allowVariableFonts: false,
  });
  assert.equal(plan.status.eligible, true, JSON.stringify(plan.status.failures));
  assert.deepEqual(
    plan.faces.map((face) => [face.family, face.face, face.weight]),
    [["Pretendard", "regular", 400], ["Pretendard", "bold", 700]],
  );
  assert.ok(plan.files.every((file) => file.source === "bundled-open-font"));
  assert.ok(plan.files.every((file) => file.license.decision === "allowed-installable"));
  assert.ok(plan.files.every((file) => !file.inspection.variable));

  const skeleton = skeletonPptx();
  const embedded = await prepareFontEmbedding(skeleton, plan);
  assert.equal(embedded.status.applied, true);
  assert.equal(embedded.status.embeddedFontFiles, 2);
  assert.deepEqual(embedded.embeddedTypefaceFamilies, ["Pretendard"]);
  assert.deepEqual(embedded.embeddedTypefaceFaces, embeddedPretendard);
  const entries = readPptxArchive(embedded.pptx);
  assert.match(entries.get("ppt/presentation.xml").toString("utf8"), /<p:embeddedFont><p:font typeface="Pretendard" pitchFamily="34" charset="-127"\/><p:regular r:id="rId\d+"\/><p:bold r:id="rId\d+"\/><\/p:embeddedFont>/);
  const sourceBytes = plan.files.reduce((sum, file) => sum + file.sizeBytes, 0);
  const packagedGrowth = embedded.pptx.length - skeleton.length;
  console.info(
    `[pretendard-embedding] source=${sourceBytes} bytes, pptx growth=${packagedGrowth} bytes ` +
      `(${(packagedGrowth / 1024 / 1024).toFixed(2)} MiB)`,
  );
  assert.ok(packagedGrowth > 0);
  assert.ok(packagedGrowth <= sourceBytes * 1.05);
});
