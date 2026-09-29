import assert from "node:assert/strict";
import { readFileSync, readdirSync } from "node:fs";
import path from "node:path";
import test from "node:test";

// The authored slide size is named once, in lib/authored-hybrid/schema.ts
// (AUTHORED_SLIDE_WIDTH_PX / AUTHORED_SLIDE_HEIGHT_PX). Every other source
// file must import it rather than restate 1280x720, or a non-16:9 deck takes
// the wrong branch there (docs/refactoring-audit-20260929.md section 4,
// hardcoding item 1). Files that legitimately carry the numbers for another
// reason are listed below with that reason.

const CANONICAL = "lib/authored-hybrid/schema.ts";

const EXEMPT = new Map([
  // Preset geometry tables: the other aspect ratios (9:16, 1:1, 21:9) share
  // the 720 edge, so the numbers are canvas presets, not the default canvas.
  ["lib/authored-canvas.ts", "aspect-ratio preset table"],
  ["app/(presentation-generator)/upload/utils/autoCanvas.ts", "picker row geometry table"],
  // Static fixtures for the OOXML vNext contract tests.
  ["lib/ooxml-vnext/fixtures/demo-contract.ts", "test fixture"],
  // Upper bounds on point-valued paragraph metrics, unrelated to the canvas.
  ["lib/authored-hybrid/native-table.ts", "pt metric upper bound"],
]);

const SOURCE_ROOTS = ["app", "components", "lib", "store", "utils"];
const SOURCE_EXTENSIONS = new Set([".ts", ".tsx", ".mts", ".cts", ".js", ".mjs", ".cjs"]);
const LITERAL = /(?<![\w.])(?:1280|720)(?![\w.])/u;

function* walk(dir) {
  for (const entry of readdirSync(dir, { withFileTypes: true })) {
    if (entry.name === "node_modules" || entry.name.startsWith(".next")) continue;
    const full = path.join(dir, entry.name);
    if (entry.isDirectory()) {
      yield* walk(full);
      continue;
    }
    const ext = path.extname(entry.name);
    if (!SOURCE_EXTENSIONS.has(ext)) continue;
    if (/\.(?:test|cy|spec)\.[cm]?[jt]sx?$/u.test(entry.name)) continue;
    yield full;
  }
}

const BLOCK_COMMENT = /\/\*[\s\S]*?\*\//gu;
const LINE_COMMENT = /(^|[^:\\])\/\/[^\n]*/gu;
const TEMPLATE_STRING = /`(?:\\[\s\S]|[^`\\])*`/gu;
const DOUBLE_QUOTED = /"(?:\\.|[^"\\\n])*"/gu;
const SINGLE_QUOTED = /'(?:\\.|[^'\\\n])*'/gu;

function stripCommentsAndStrings(source) {
  return source
    .replace(BLOCK_COMMENT, " ")
    .replace(LINE_COMMENT, "$1")
    .replace(TEMPLATE_STRING, "``")
    .replace(DOUBLE_QUOTED, '""')
    .replace(SINGLE_QUOTED, "''");
}

test("the authored slide size literal lives only in the canonical schema module", () => {
  const root = process.cwd();
  const offenders = [];
  for (const dir of SOURCE_ROOTS) {
    for (const file of walk(path.join(root, dir))) {
      const rel = path.relative(root, file).split(path.sep).join("/");
      if (rel === CANONICAL || EXEMPT.has(rel)) continue;
      const code = stripCommentsAndStrings(readFileSync(file, "utf8"));
      code.split("\n").forEach((line, index) => {
        if (LITERAL.test(line)) offenders.push(`${rel}:${index + 1}: ${line.trim()}`);
      });
    }
  }
  assert.deepEqual(offenders, [], `restated authored slide size:\n${offenders.join("\n")}`);
});

test("the canonical module still names the default authored slide size", () => {
  const source = readFileSync(path.join(process.cwd(), CANONICAL), "utf8");
  assert.match(source, /export const AUTHORED_SLIDE_WIDTH_PX = 1280 as const;/u);
  assert.match(source, /export const AUTHORED_SLIDE_HEIGHT_PX = 720 as const;/u);
});

test("every exempt file still exists and still carries the literal it is exempt for", () => {
  for (const [rel] of EXEMPT) {
    const code = stripCommentsAndStrings(readFileSync(path.join(process.cwd(), rel), "utf8"));
    assert.ok(LITERAL.test(code), `${rel} no longer needs its exemption; remove it`);
  }
});
