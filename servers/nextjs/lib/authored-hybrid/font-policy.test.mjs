import assert from "node:assert/strict";
import test from "node:test";

import {
  cssFontStack,
  expandKnownFontFamilyList,
  FONT_FALLBACK_REGISTRY,
  powerPointFontMetricScale,
  resolveFontCandidates,
  resolveFontFamily,
  rewriteKnownFontFamiliesInCss,
} from "./font-policy.ts";

const expectedFallbacks = new Map([
  ["Noto Sans KR", "Malgun Gothic"],
  ["Pretendard", "Malgun Gothic"],
  ["Noto Serif KR", "Batang"],
  ["Inter", "Aptos"],
  ["Roboto", "Aptos"],
  ["DM Sans", "Aptos"],
  ["Source Serif 4", "Cambria"],
  ["IBM Plex Mono", "Consolas"],
]);

test("logical authored fonts resolve to deterministic local Office typefaces", () => {
  assert.deepEqual(
    new Map(FONT_FALLBACK_REGISTRY.map(({ logical, local }) => [logical, local])),
    expectedFallbacks
  );
  for (const [logical, local] of expectedFallbacks) {
    assert.equal(resolveFontFamily(logical), local);
    assert.equal(resolveFontFamily(`  "${logical.toUpperCase()}" `), local);
    assert.match(cssFontStack(logical), new RegExp(`^"${logical}", "${local}", `));
  }
});

test("unknown local fonts are preserved and generic candidates are skipped", () => {
  assert.equal(resolveFontFamily("Acme Corporate"), "Acme Corporate");
  assert.equal(cssFontStack("Acme Corporate"), '"Acme Corporate"');
  assert.equal(
    resolveFontCandidates(["system-ui", "Roboto", "sans-serif"], "Aptos"),
    "Aptos"
  );
  assert.equal(resolveFontCandidates(["serif"], "Source Serif 4"), "Cambria");
  assert.equal(
    resolveFontCandidates(
      ["system-ui", "Noto Sans KR", "sans-serif"],
      "Aptos",
      ["Noto Sans KR"]
    ),
    "Noto Sans KR"
  );
  assert.equal(
    resolveFontCandidates(["Noto Sans KR", "sans-serif"], "Aptos", []),
    "Malgun Gothic"
  );
});

test("PowerPoint metric calibration applies only to the retained web typeface", () => {
  assert.equal(powerPointFontMetricScale("Noto Sans KR"), 0.96);
  assert.equal(powerPointFontMetricScale('"NOTO SANS KR"'), 0.96);
  assert.equal(powerPointFontMetricScale("Malgun Gothic"), 1);
  assert.equal(powerPointFontMetricScale("Acme Corporate"), 1);
});

test("CSS family lists keep verified logical fonts and append matching local fallbacks", () => {
  assert.equal(
    expandKnownFontFamilyList('"Source Serif 4", "Noto Serif KR", serif'),
    '"Source Serif 4", "Cambria", "Noto Serif KR", "Batang", serif'
  );
  assert.equal(
    rewriteKnownFontFamiliesInCss(
      "h1{font-family:'DM Sans',sans-serif} p{font-family:Acme Corporate}"
    ),
    'h1{font-family:"DM Sans", "Aptos", sans-serif} p{font-family:Acme Corporate}'
  );
  assert.equal(
    rewriteKnownFontFamiliesInCss("font-family:Roboto"),
    'font-family:"Roboto", "Aptos", sans-serif'
  );
  assert.equal(
    rewriteKnownFontFamiliesInCss(
      'h1{font:700 48px/1.2 "Noto Sans KR",sans-serif!important}'
    ),
    'h1{font:700 48px/1.2 "Noto Sans KR", "Malgun Gothic",sans-serif !important}'
  );
  assert.equal(
    rewriteKnownFontFamiliesInCss("font:italic 600 42px/1.2 Inter"),
    'font:italic 600 42px/1.2 "Inter", "Aptos", sans-serif'
  );
});
