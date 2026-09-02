import assert from "node:assert/strict";
import crypto from "node:crypto";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

import {
  buildGenerateRequest,
  computeMetrics,
  formatMetrics,
  loadFixture,
  parseArgs,
  readReceipt,
  receiptFileName,
  slideMarkdown,
} from "./run-benchmark.mjs";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const FIXTURE = path.join(HERE, "fixture-50.json");

test("the checked-in fixture is fifty slides and covers every archetype", () => {
  const fixture = loadFixture(FIXTURE);
  assert.equal(fixture.slides.length, 50);
  const planned = new Set(fixture.slides.map((slide) => slide.archetype_hint));
  for (const archetype of fixture.archetype_coverage) {
    assert.ok(planned.has(archetype), `archetype not exercised: ${archetype}`);
  }
  assert.equal(fixture.archetype_coverage.length, 20);
  const indices = fixture.slides.map((slide) => slide.index);
  assert.deepEqual(indices, Array.from({ length: 50 }, (_, i) => i + 1));
});

test("every figure slide carries three to six bullets the plan can copy", () => {
  const fixture = loadFixture(FIXTURE);
  for (const slide of fixture.slides) {
    if (slide.archetype_hint === "none") continue;
    assert.ok(
      slide.bullets.length >= 3 && slide.bullets.length <= 6,
      `slide ${slide.index} has ${slide.bullets.length} bullets`
    );
    assert.ok(slide.takeaway && slide.takeaway.length > 0, `slide ${slide.index}`);
  }
});

test("--limit takes a subset without mutating the fixture on disk", () => {
  const before = fs.readFileSync(FIXTURE, "utf8");
  const subset = loadFixture(FIXTURE, 2);
  assert.equal(subset.slides.length, 2);
  assert.equal(fs.readFileSync(FIXTURE, "utf8"), before);
});

test("the request body matches the authored generate contract", () => {
  const fixture = loadFixture(FIXTURE, 2);
  const body = buildGenerateRequest(fixture, { style: "default" });
  assert.equal(body.strategy, "authored");
  assert.equal(body.template, "authored");
  assert.equal(body.n_slides, 2);
  assert.equal(body.language, "ko");
  assert.equal(body.slides_markdown.length, 2);
  assert.ok(body.slides_markdown[0].startsWith("# "));
  assert.equal(body.include_title_slide, false);
  assert.equal(body.vision_qa, false);
});

test("a slide with no bullets still carries its takeaway into markdown", () => {
  const markdown = slideMarkdown({ title: "1장", takeaway: "요지", bullets: [] });
  assert.equal(markdown, "# 1장\n\n- 요지");
});

test("--from starts the window at a figure slide", () => {
  const subset = loadFixture(FIXTURE, 2, 4);
  assert.deepEqual(
    subset.slides.map((slide) => slide.archetype_hint),
    ["kpi", "icon_list"]
  );
  assert.throws(() => loadFixture(FIXTURE, 2, 999), /selected no slides/);
});

test("parseArgs rejects an unknown flag rather than ignoring it", () => {
  assert.throws(() => parseArgs(["--nope"]), /unknown flag/);
  const args = parseArgs(["--limit", "2", "--dry-run", "--base", "http://x"]);
  assert.equal(args.limit, 2);
  assert.equal(args.dryRun, true);
  assert.equal(args.base, "http://x");
});

test("the receipt filename mirrors the backend's hashing", () => {
  const id = "9ed1b7ad-ebb7-4119-b466-357f9a9ab8f1";
  const digest = crypto.createHash("sha256").update(id, "utf8").digest("hex").slice(0, 32);
  assert.equal(receiptFileName(id), `deck-${digest}-slide-quality.json`);
});

test("metrics report unmeasured critic scores as absent, never as zero", () => {
  const fixtureSlides = [
    { archetype_hint: "process" },
    { archetype_hint: "kpi" },
    { archetype_hint: "none" },
  ];
  const receipt = [
    { archetype: "process", fallbackCount: 0, criticScore: null, variantChosen: null },
    { archetype: "kpi", fallbackCount: 5, criticScore: null, variantChosen: null },
    { archetype: "none", fallbackCount: 1 },
  ];
  const metrics = computeMetrics(fixtureSlides, receipt, { elapsedMs: 1000 });
  assert.equal(metrics.slidesMeasured, 3);
  assert.equal(metrics.figureSlides, 2);
  assert.equal(metrics.slidesOverFallbackBudget, 1);
  assert.equal(metrics.criticScoresMeasured, 0);
  const kpi = metrics.byArchetype.find((row) => row.archetype === "kpi");
  assert.equal(kpi.criticScore, null);
  assert.equal(kpi.fallbacksPerSlide, 5);
});

test("an archetype the fixture asked for but the plan never chose is named", () => {
  const metrics = computeMetrics(
    [{ archetype_hint: "venn" }, { archetype_hint: "kpi" }],
    [{ archetype: "kpi" }, { archetype: "icon_list" }]
  );
  assert.deepEqual(metrics.archetypesRequestedButNeverPlanned, ["venn"]);
  assert.ok(formatMetrics(metrics).includes("venn"));
});

test("a missing receipt reads back as null instead of throwing", () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "infographic-bench-"));
  try {
    assert.equal(readReceipt(dir, "missing-deck"), null);
    const id = "deck-under-test";
    fs.writeFileSync(
      path.join(dir, receiptFileName(id)),
      JSON.stringify({
        schemaVersion: "authored-slide-quality-manifest/v1",
        slides: {
          1: { archetype: "kpi", slideIndex: 1 },
          0: { archetype: "process", slideIndex: 0 },
        },
      }),
      "utf8"
    );
    assert.deepEqual(
      readReceipt(dir, id).map((record) => record.archetype),
      ["process", "kpi"]
    );
  } finally {
    fs.rmSync(dir, { recursive: true, force: true });
  }
});
