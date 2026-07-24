import assert from "node:assert/strict";
import fs from "node:fs/promises";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

import { renderTemplateV2GeneralPresentationHtml } from "../template-v2-general-renderer.mjs";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const ROOT = path.join(HERE, "fixtures", "template-v2-general");
const REPO_ROOT = path.resolve(HERE, "..", "..", "..", "..");

function collectElementTypes(template) {
  const types = new Set();
  const visit = (element) => {
    if (!element || typeof element !== "object") return;
    if (typeof element.type === "string") types.add(element.type);
    if (element.child) visit(element.child);
    if (Array.isArray(element.children)) element.children.forEach(visit);
  };
  for (const slide of template.slides ?? []) {
    for (const component of slide.ui?.components ?? []) {
      for (const element of component.elements ?? []) visit(element);
    }
  }
  return types;
}

test("Template V2 fidelity corpus keeps persisted template-only cases", async () => {
  const manifest = JSON.parse(await fs.readFile(path.join(ROOT, "manifest.json"), "utf8"));
  const compatibility = JSON.parse(
    await fs.readFile(path.join(REPO_ROOT, "compatibility", "upstream-compatibility.json"), "utf8")
  );
  const coveredElementTypes = new Set();
  assert.equal(manifest.contract, "template-v2-general-export-fidelity-v1");
  assert.deepEqual(manifest.canvas, { width: 1280, height: 720 });
  assert.ok(manifest.cases.length > 0);
  assert.equal(new Set(manifest.cases.map((fixture) => fixture.id)).size, manifest.cases.length);
  assert.equal(new Set(manifest.cases.map((fixture) => fixture.directory)).size, manifest.cases.length);
  for (const fixture of manifest.cases) {
    assert.deepEqual(fixture.templateIdentity, { version: "v2-standard", mode: "template" });
    const template = JSON.parse(await fs.readFile(path.join(ROOT, fixture.directory, "template.v2.json"), "utf8"));
    collectElementTypes(template).forEach((type) => coveredElementTypes.add(type));
    const source = renderTemplateV2GeneralPresentationHtml(template);
    assert.match(source, /id="presentation-slides-wrapper"/);
    assert.match(source, /class="main-slide"/);
    assert.equal(template.version, "v2-standard");
    assert.equal(template.mode, "template");
    assert.ok(template.slides.every((slide) => slide.ui && !slide.html_content));
    for (const expectedText of fixture.expectedText) assert.ok(source.includes(expectedText));
    if (fixture.id === "rich-elements") {
      assert.match(source, /-webkit-text-stroke:1px rgba\(255,255,255,0.6\)/);
      assert.match(source, /text-shadow:1px 2px 3px rgba\(0,0,0,0.2\)/);
      assert.match(source, /box-shadow:2px 3px 4px rgba\(23,37,84,0.25\)/);
      assert.match(source, /data-template-v2-element="image"/);
      assert.match(source, /transform:scaleX\(-1\)/);
      assert.match(source, /object-position:25% 50%/);
      assert.match(source, /transform:scale\(1.25\)/);
      assert.match(source, /clip-path:circle\(48% at 50% 50%\)/);
      assert.match(source, /data-chart-type="stacked_bar"/);
      assert.match(source, /data-chart-stacked="true"/);
      assert.match(source, /data-stack-start="40" data-stack-end="60"/);
      assert.match(source, /data-chart-axis="x"/);
      assert.match(source, /data-chart-axis="y"/);
      assert.match(source, /data-chart-grid="x"/);
      assert.match(source, /data-chart-grid="y"/);
      assert.match(source, /data-chart-data-label="mid"/);
      assert.match(source, /data-chart-source/);
    }
  }
  assert.deepEqual(
    [...coveredElementTypes].sort(),
    [...compatibility.templateV2Renderer.discriminators].sort(),
    "fidelity corpus must cover every pinned Template V2 renderer discriminator"
  );
});
