#!/usr/bin/env node
// Regenerate the fixed 50-slide authored infographic benchmark against a
// backend and compute its automatic metrics.
//
//   node scripts/benchmarks/authored-infographic/run-benchmark.mjs \
//     --base http://127.0.0.1:8090 \
//     --receipts-dir <app data>/authored-quality-receipts \
//     --out artifacts/infographic-benchmark-<date>
//
// See README.md next to this file for the isolated-backend recipe. The runner
// never writes into output/ or app_data/; --out must be somewhere else.
//
// Flags: --limit <n> (run the first n slides only), --dry-run (build the
// request and stop, no backend), --metrics-only --presentation-id <uuid>
// (recompute from an existing receipt), --style, --timeout, --fixture.
//
// No npm dependencies. Node 18+.

import crypto from "node:crypto";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const DEFAULT_FIXTURE = path.join(HERE, "fixture-50.json");

// A slide whose export leaves more than this many elements as raster is a
// quality failure, not a rounding error (Stage 4 of the programme brief).
export const FALLBACK_BUDGET_PER_SLIDE = 2;

export function parseArgs(argv) {
  const args = {
    base: "http://127.0.0.1:8090",
    fixture: DEFAULT_FIXTURE,
    receiptsDir: null,
    out: null,
    limit: 0,
    from: 1,
    style: "default",
    timeoutMs: 45 * 60 * 1000,
    dryRun: false,
    metricsOnly: false,
    presentationId: null,
  };
  for (let i = 0; i < argv.length; i += 1) {
    const flag = argv[i];
    if (flag === "--base") args.base = argv[++i];
    else if (flag === "--fixture") args.fixture = path.resolve(argv[++i]);
    else if (flag === "--receipts-dir") args.receiptsDir = path.resolve(argv[++i]);
    else if (flag === "--out") args.out = path.resolve(argv[++i]);
    else if (flag === "--limit") args.limit = Number.parseInt(argv[++i], 10) || 0;
    else if (flag === "--from") args.from = Number.parseInt(argv[++i], 10) || 1;
    else if (flag === "--style") args.style = argv[++i];
    else if (flag === "--timeout") args.timeoutMs = Number.parseInt(argv[++i], 10) * 1000;
    else if (flag === "--dry-run") args.dryRun = true;
    else if (flag === "--metrics-only") args.metricsOnly = true;
    else if (flag === "--presentation-id") args.presentationId = argv[++i];
    else if (flag === "--help" || flag === "-h") args.help = true;
    else throw new Error(`unknown flag: ${flag}`);
  }
  return args;
}

export function loadFixture(fixturePath, limit = 0, from = 1) {
  const fixture = JSON.parse(fs.readFileSync(fixturePath, "utf8"));
  if (!Array.isArray(fixture.slides) || fixture.slides.length === 0) {
    throw new Error("fixture carries no slides");
  }
  // ``--from`` is 1-based over the fixture's own slide numbering, so a smoke
  // run can start at a figure slide instead of the cover.
  const start = Math.max(1, from) - 1;
  const window = fixture.slides.slice(start);
  const slides = limit > 0 ? window.slice(0, limit) : window;
  if (slides.length === 0) throw new Error("--from/--limit selected no slides");
  return { ...fixture, slides };
}

export function slideMarkdown(slide) {
  const lines = [`# ${slide.title}`];
  const bullets = Array.isArray(slide.bullets) ? slide.bullets : [];
  if (bullets.length > 0) {
    lines.push("", ...bullets.map((bullet) => `- ${bullet}`));
  } else if (slide.takeaway) {
    lines.push("", `- ${slide.takeaway}`);
  }
  return lines.join("\n");
}

export function buildGenerateRequest(fixture, { style = "default" } = {}) {
  const slidesMarkdown = fixture.slides.map(slideMarkdown);
  return {
    content: slidesMarkdown[0].slice(0, 200),
    slides_markdown: slidesMarkdown,
    n_slides: slidesMarkdown.length,
    language: fixture.language || "ko",
    tone: "default",
    verbosity: "standard",
    instructions: null,
    include_table_of_contents: false,
    include_title_slide: false,
    web_search: false,
    template: "authored",
    strategy: "authored",
    export_as: "pptx",
    vision_qa: false,
    authored_style: style,
  };
}

/** Mirror of ``slide_quality_receipt_path`` in the backend. */
export function receiptFileName(presentationId) {
  const digest = crypto
    .createHash("sha256")
    .update(String(presentationId), "utf8")
    .digest("hex")
    .slice(0, 32);
  return `deck-${digest}-slide-quality.json`;
}

export function readReceipt(receiptsDir, presentationId) {
  if (!receiptsDir) return null;
  const file = path.join(receiptsDir, receiptFileName(presentationId));
  if (!fs.existsSync(file)) return null;
  const payload = JSON.parse(fs.readFileSync(file, "utf8"));
  const slides = payload && payload.slides;
  if (!slides || typeof slides !== "object") return null;
  return Object.keys(slides)
    .sort((a, b) => Number(a) - Number(b))
    .map((key) => slides[key]);
}

function isNumber(value) {
  return typeof value === "number" && Number.isFinite(value);
}

/**
 * Automatic metrics for one benchmark run.
 *
 * ``requested`` is what the fixture asked each slide to be drawn as; ``planned``
 * is what the deck plan actually chose. They are reported side by side rather
 * than scored against each other: the plan is allowed to disagree with a hint,
 * and a systematic disagreement on one archetype is itself the finding.
 */
export function computeMetrics(fixtureSlides, receiptSlides, { elapsedMs = null } = {}) {
  const records = Array.isArray(receiptSlides) ? receiptSlides : [];
  const byArchetype = new Map();
  const bucket = (name) => {
    if (!byArchetype.has(name)) {
      byArchetype.set(name, {
        archetype: name,
        requested: 0,
        planned: 0,
        fallbackSum: 0,
        fallbackMeasured: 0,
        overBudget: 0,
        criticSum: 0,
        criticMeasured: 0,
        variantsChosen: 0,
      });
    }
    return byArchetype.get(name);
  };

  for (const slide of fixtureSlides) {
    bucket(String(slide.archetype_hint || "none")).requested += 1;
  }
  for (const record of records) {
    const name = String(record.archetype || "none");
    const entry = bucket(name);
    entry.planned += 1;
    if (isNumber(record.fallbackCount)) {
      entry.fallbackMeasured += 1;
      entry.fallbackSum += record.fallbackCount;
      if (record.fallbackCount > FALLBACK_BUDGET_PER_SLIDE) entry.overBudget += 1;
    }
    if (isNumber(record.criticScore)) {
      entry.criticMeasured += 1;
      entry.criticSum += record.criticScore;
    }
    if (record.variantChosen) entry.variantsChosen += 1;
  }

  const rows = [...byArchetype.values()]
    .sort((a, b) => a.archetype.localeCompare(b.archetype))
    .map((entry) => ({
      ...entry,
      fallbacksPerSlide: entry.fallbackMeasured
        ? Number((entry.fallbackSum / entry.fallbackMeasured).toFixed(2))
        : null,
      criticScore: entry.criticMeasured
        ? Number((entry.criticSum / entry.criticMeasured).toFixed(2))
        : null,
    }));

  const requestedVocabulary = new Set(
    fixtureSlides.map((slide) => String(slide.archetype_hint || "none"))
  );
  const plannedVocabulary = new Set(records.map((r) => String(r.archetype || "none")));
  const figureSlides = records.filter((r) => String(r.archetype || "none") !== "none");

  return {
    slidesRequested: fixtureSlides.length,
    slidesMeasured: records.length,
    figureSlides: figureSlides.length,
    figureShare: records.length
      ? Number((figureSlides.length / records.length).toFixed(3))
      : null,
    archetypesRequested: [...requestedVocabulary].sort(),
    archetypesPlanned: [...plannedVocabulary].sort(),
    archetypesRequestedButNeverPlanned: [...requestedVocabulary]
      .filter((name) => !plannedVocabulary.has(name))
      .sort(),
    slidesOverFallbackBudget: rows.reduce((sum, row) => sum + row.overBudget, 0),
    criticScoresMeasured: rows.reduce((sum, row) => sum + row.criticMeasured, 0),
    variantsChosen: rows.reduce((sum, row) => sum + row.variantsChosen, 0),
    elapsedMs,
    byArchetype: rows,
  };
}

export function formatMetrics(metrics) {
  const lines = [
    `slides requested: ${metrics.slidesRequested}  measured: ${metrics.slidesMeasured}`,
    `figure slides: ${metrics.figureSlides} (${metrics.figureShare ?? "-"})`,
    `archetypes planned: ${metrics.archetypesPlanned.join(", ") || "-"}`,
    `requested but never planned: ${
      metrics.archetypesRequestedButNeverPlanned.join(", ") || "(none)"
    }`,
    `slides over fallback budget: ${metrics.slidesOverFallbackBudget}`,
    `critic scores measured: ${metrics.criticScoresMeasured}  variants chosen: ${metrics.variantsChosen}`,
    "",
    "archetype           req  plan  fallback/slide  >budget  critic",
  ];
  for (const row of metrics.byArchetype) {
    lines.push(
      [
        row.archetype.padEnd(19),
        String(row.requested).padStart(3),
        String(row.planned).padStart(5),
        String(row.fallbacksPerSlide ?? "-").padStart(15),
        String(row.overBudget).padStart(8),
        String(row.criticScore ?? "-").padStart(7),
      ].join(" ")
    );
  }
  return lines.join("\n");
}

async function generate(base, body, timeoutMs) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  try {
    const response = await fetch(`${base}/api/v1/ppt/presentation/generate`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
      signal: controller.signal,
    });
    const text = await response.text();
    if (!response.ok) {
      throw new Error(`generate failed ${response.status}: ${text.slice(0, 500)}`);
    }
    return JSON.parse(text);
  } finally {
    clearTimeout(timer);
  }
}

function writeOut(outDir, name, payload) {
  if (!outDir) return null;
  fs.mkdirSync(outDir, { recursive: true });
  const file = path.join(outDir, name);
  fs.writeFileSync(file, JSON.stringify(payload, null, 2), "utf8");
  return file;
}

export async function main(argv) {
  const args = parseArgs(argv);
  if (args.help) {
    console.log(fs.readFileSync(path.join(HERE, "README.md"), "utf8"));
    return 0;
  }
  const fixture = loadFixture(args.fixture, args.limit, args.from);
  console.log(`fixture: ${args.fixture} (${fixture.slides.length} slides)`);

  if (args.metricsOnly) {
    if (!args.presentationId) throw new Error("--metrics-only needs --presentation-id");
    const records = readReceipt(args.receiptsDir, args.presentationId);
    const metrics = computeMetrics(fixture.slides, records);
    console.log(formatMetrics(metrics));
    writeOut(args.out, "metrics.json", metrics);
    return 0;
  }

  const body = buildGenerateRequest(fixture, { style: args.style });
  if (args.dryRun) {
    console.log(`dry run: would POST ${args.base}/api/v1/ppt/presentation/generate`);
    console.log(`n_slides=${body.n_slides} style=${body.authored_style}`);
    writeOut(args.out, "request.json", body);
    return 0;
  }

  const started = Date.now();
  const result = await generate(args.base, body, args.timeoutMs);
  const elapsedMs = Date.now() - started;
  const presentationId = result.presentation_id || args.presentationId;
  console.log(`presentation: ${presentationId} in ${(elapsedMs / 1000).toFixed(1)}s`);

  const records = readReceipt(args.receiptsDir, presentationId);
  if (records === null) {
    console.warn(
      `no slide-quality receipt found; pass --receipts-dir pointing at the backend's ` +
        `app-data authored-quality-receipts directory`
    );
  }
  const metrics = computeMetrics(fixture.slides, records, { elapsedMs });
  console.log(formatMetrics(metrics));
  const file = writeOut(args.out, "metrics.json", { presentationId, ...metrics });
  if (file) console.log(`wrote ${file}`);
  return 0;
}

const invokedDirectly =
  process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url);
if (invokedDirectly) {
  main(process.argv.slice(2)).then(
    (code) => process.exit(code),
    (error) => {
      console.error(error.message);
      process.exit(1);
    }
  );
}
