import assert from "node:assert/strict";
import fs from "node:fs/promises";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

const directory = path.dirname(fileURLToPath(import.meta.url));

test("authored slides use the isolated authored renderer directly", async () => {
  const source = await fs.readFile(
    path.join(directory, "PresentationContentRender.tsx"),
    "utf8",
  );
  assert.doesNotMatch(source, /dangerouslySetInnerHTML/);
  assert.match(source, /authored: AuthoredContent/);
  assert.match(source, /AuthoredSlidePreview/);
});

test("web authored preview fallback is opaque, scriptless, and CSP-restricted", async () => {
  const source = await fs.readFile(path.join(directory, "AuthoredSlidePreview.tsx"), "utf8");
  assert.match(source, /sandbox=""/);
  assert.match(source, /default-src 'none'/);
  assert.match(source, /connect-src 'none'/);
  assert.match(source, /img-src data:/);
  assert.match(source, /font-src data:/);
  assert.match(source, /script-src 'none'/);
  assert.match(source, /srcDoc=\{isolatedHtmlSource\}/);
  assert.match(source, /runtime === "web"/);
  assert.match(source, /window\.electron \? "electron" : "web"/);
  assert.match(source, /useState<"unknown" \| "web" \| "electron">/);
});
