import assert from "node:assert/strict";
import fs from "node:fs/promises";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

const directory = path.dirname(fileURLToPath(import.meta.url));

test("SafeHtmlSlidePreview accepts an optional canvas prop defaulting to 1280x720", async () => {
  const source = await fs.readFile(
    path.join(directory, "SafeHtmlSlidePreview.tsx"),
    "utf8",
  );
  assert.match(source, /canvas\?: SafeHtmlSlidePreviewCanvas/);
  assert.match(source, /canvas = DEFAULT_CANVAS/);
  assert.match(
    source,
    /const DEFAULT_CANVAS: SafeHtmlSlidePreviewCanvas = \{\s*widthPx: SLIDE_WIDTH,\s*heightPx: SLIDE_HEIGHT,\s*\};/,
  );
  // No hardcoded 1280/720 left where the canvas should drive sizing.
  assert.doesNotMatch(source, /width:\s*1280/);
  assert.doesNotMatch(source, /height:\s*720/);
  assert.doesNotMatch(source, /h-\[720px\]\s+w-\[1280px\]/);
  assert.match(source, /isolatedPreviewDocument\(html, fonts, canvas\)/);
  assert.match(source, /width=\$\{widthPx\}, initial-scale=1/);
});
