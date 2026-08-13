import assert from "node:assert/strict";
import fs from "node:fs/promises";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

const directory = path.dirname(fileURLToPath(import.meta.url));

test("authored slides never enter the SmartHtmlEditor application DOM", async () => {
  const source = await fs.readFile(path.join(directory, "V1ContentRender.tsx"), "utf8");
  const authoredBranch = source.slice(
    source.indexOf("if (isAuthored)"),
    source.indexOf("// Show loading state for custom templates")
  );
  assert.ok(authoredBranch.length > 0);
  assert.doesNotMatch(authoredBranch, /SmartHtmlEditor|innerHTML/);
  assert.match(authoredBranch, /AuthoredSlidePreview/);
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
