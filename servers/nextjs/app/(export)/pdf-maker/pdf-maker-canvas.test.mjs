import assert from "node:assert/strict";
import fs from "node:fs/promises";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

const directory = path.dirname(fileURLToPath(import.meta.url));

test("pdf-maker sizes the print stylesheet from the presentation's canvas, not a literal 1280x720", async () => {
  const source = await fs.readFile(
    path.join(directory, "PdfMakerPage.tsx"),
    "utf8",
  );
  assert.match(source, /from "@\/lib\/authored-canvas"/);
  assert.match(source, /resolveAuthoredCanvas/);
  assert.match(source, /function pdfPrintStyle\(canvas: AuthoredCanvasContract\): string/);
  assert.match(source, /width: \$\{widthPx\}px !important;/);
  assert.match(source, /height: \$\{heightPx\}px !important;/);
  assert.match(source, /size: \$\{widthPx\}px \$\{heightPx\}px;/);
  assert.match(source, /pdfPrintStyle\(canvas\)/);
  // No leftover hardcoded 1280x720 in the print stylesheet or the authored-html slide box.
  assert.doesNotMatch(source, /width: 1280px !important/);
  assert.doesNotMatch(source, /height: 720px !important/);
  assert.doesNotMatch(source, /size: 1280px 720px/);
  assert.doesNotMatch(source, /h-\[720px\]\s+w-\[1280px\]/);
});

test("pdf-maker falls back to the legacy landscape canvas when there is no theme", async () => {
  const source = await fs.readFile(
    path.join(directory, "PdfMakerPage.tsx"),
    "utf8",
  );
  assert.match(source, /LEGACY_AUTHORED_CANVAS/);
  assert.match(source, /if \(!presentationData\?\.theme\) return LEGACY_AUTHORED_CANVAS;/);
});
