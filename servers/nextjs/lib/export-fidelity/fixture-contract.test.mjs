import assert from "node:assert/strict";
import fs from "node:fs/promises";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

import { renderTemplateV2GeneralPresentationHtml } from "../template-v2-general-renderer.mjs";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const ROOT = path.join(HERE, "fixtures", "template-v2-general");

test("Template V2 fidelity corpus keeps persisted template-only cases", async () => {
  const manifest = JSON.parse(await fs.readFile(path.join(ROOT, "manifest.json"), "utf8"));
  assert.equal(manifest.contract, "template-v2-general-export-fidelity-v1");
  assert.deepEqual(manifest.canvas, { width: 1280, height: 720 });
  assert.deepEqual(manifest.cases.map((fixture) => fixture.id), ["title-body", "geometry", "text-fit", "rich-elements"]);
  for (const fixture of manifest.cases) {
    assert.deepEqual(fixture.templateIdentity, { version: "v2-standard", mode: "template" });
    const template = JSON.parse(await fs.readFile(path.join(ROOT, fixture.directory, "template.v2.json"), "utf8"));
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
});
