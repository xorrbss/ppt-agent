import assert from "node:assert/strict";
import test from "node:test";

import type { ActiveEmissionExportBinding } from "./active-emission.ts";
import {
  nativeEmbeddingFamilies,
  normalizedTypeface,
} from "./export-font-embedding.ts";
import { EDITABLE_HYBRID_SHADOW_PROFILE_ID } from "./presentation-mode.ts";

function bindingWithFonts(
  ...fontFamilies: readonly string[]
): ActiveEmissionExportBinding {
  return {
    slides: [{ fontFamilies }],
  } as unknown as ActiveEmissionExportBinding;
}

test("normalizes quoted typeface names for face matching", () => {
  assert.equal(normalizedTypeface('  "Noto Sans KR"  '), "noto sans kr");
});

test("native embedding includes Noto Sans KR for Korean and sealed profiles", () => {
  assert.deepEqual(
    nativeEmbeddingFamilies(bindingWithFonts("Aptos", "Pretendard"), undefined),
    ["Noto Sans KR", "Aptos", "Pretendard"],
  );
  assert.deepEqual(
    nativeEmbeddingFamilies(bindingWithFonts("Aptos"), EDITABLE_HYBRID_SHADOW_PROFILE_ID),
    ["Noto Sans KR", "Aptos"],
  );
});

test("native embedding retains authored order while de-duplicating families", () => {
  assert.deepEqual(
    nativeEmbeddingFamilies(bindingWithFonts("Aptos", "Aptos", "IBM Plex Mono"), undefined),
    ["Aptos", "IBM Plex Mono"],
  );
});
