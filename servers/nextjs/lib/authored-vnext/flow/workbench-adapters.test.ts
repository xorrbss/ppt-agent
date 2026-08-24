import assert from "node:assert/strict";
import test from "node:test";

import { browserSafeAssetPreview } from "./workbench-adapters.ts";

test("Workbench previews sealed raster data URLs without admitting active or external sources", () => {
  const png = "data:image/png;base64,iVBORw0KGgo=";
  assert.equal(browserSafeAssetPreview(png), png);
  assert.equal(browserSafeAssetPreview("/api/assets/image.png?rev=17"), "/api/assets/image.png?rev=17");

  assert.equal(browserSafeAssetPreview("data:image/svg+xml,<svg onload=alert(1)></svg>"), undefined);
  assert.equal(browserSafeAssetPreview("https://example.invalid/image.png"), undefined);
  assert.equal(browserSafeAssetPreview("//example.invalid/image.png"), undefined);
  assert.equal(browserSafeAssetPreview("/api/assets/../private.png"), undefined);
});
