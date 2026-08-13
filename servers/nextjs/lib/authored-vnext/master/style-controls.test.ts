import assert from "node:assert/strict";
import test from "node:test";

import { DEFAULT_MASTER_CONTRACT } from "./fixtures.ts";
import {
  EDITABLE_SEMANTIC_STYLES,
  setMasterColor,
  setMasterFallbackFonts,
  setMasterFurnitureColor,
  setMasterFurnitureText,
  setMasterPrimaryFont,
  setMasterSemanticFontPt,
  setMasterTitleMinimumFontPt,
} from "./style-controls.ts";

test("Master Designer style controls update the canonical contract fields", () => {
  let contract = structuredClone(DEFAULT_MASTER_CONTRACT);
  contract = setMasterPrimaryFont(contract, "Aptos Display");
  contract = setMasterFallbackFonts(contract, "Aptos, Arial, Noto Sans KR");
  contract = setMasterFurnitureText(contract, "header", "Research briefing");
  contract = setMasterFurnitureText(contract, "pageNumber", "Page {n}");
  contract = setMasterFurnitureColor(contract, "divider", "#123456");
  contract = setMasterFurnitureColor(contract, "pageNumber", "#654321");
  contract = setMasterColor(contract, "accent", "#2468AC");
  for (const style of EDITABLE_SEMANTIC_STYLES) {
    contract = setMasterSemanticFontPt(contract, style, 24);
  }
  contract = setMasterTitleMinimumFontPt(contract, 18);

  assert.equal(contract.fontManifest.primary, "Aptos Display");
  assert.equal(contract.fontManifest.windowsFallback, "Aptos Display");
  assert.deepEqual(contract.fontManifest.themeScripts, {
    latin: "Aptos Display",
    hang: "Aptos Display",
    jpan: "Aptos Display",
    hans: "Aptos Display",
    hant: "Aptos Display",
  });
  assert.equal(contract.tokens.fonts.latin, "Aptos Display");
  assert.deepEqual(contract.fontManifest.fallbacks, [
    "Aptos",
    "Arial",
    "Noto Sans KR",
  ]);
  assert.deepEqual(contract.tokens.fonts.fallbacks, [
    "Aptos",
    "Arial",
    "Noto Sans KR",
  ]);
  assert.equal(contract.furniture.header.text, "Research briefing");
  assert.equal(contract.furniture.pageNumber.text, "Page {n}");
  assert.equal(contract.furniture.divider.stroke, "#123456");
  assert.equal(contract.furniture.pageNumber.fill, "#654321");
  assert.equal(contract.tokens.colors.accent, "#2468AC");
  for (const style of EDITABLE_SEMANTIC_STYLES) {
    assert.equal(contract.semanticStyles[style].fontPt, 24);
  }
  assert.equal(contract.semanticStyles.title.minFontPt, 18);
});
