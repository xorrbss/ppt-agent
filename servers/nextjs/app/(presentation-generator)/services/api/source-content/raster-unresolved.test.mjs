import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";
import { transform } from "esbuild";

const sourcePath = path.join(path.dirname(fileURLToPath(import.meta.url)), "raster-unresolved.ts");

async function loadModule() {
  const source = await readFile(sourcePath, "utf8");
  const { code } = await transform(source, { loader: "ts", format: "esm" });
  return import(`data:text/javascript;base64,${Buffer.from(code).toString("base64")}`);
}

const unresolved = [
  "advisory:vision_structurer_unavailable",
  "local_ocr_unavailable",
  "page_1:human_review_required",
  "advisory:page_1:p1-r3:low_ocr_confidence",
  "advisory:page_2:heuristic_plan:structuring_failed:TimeoutError",
  "advisory:page_2:page_structuring_failed:ValueError",
  "advisory:page_2:p2-r1:unconfirmed_number",
  "advisory:page_12:heuristic_plan:structuring_rejected:plan_not_lossless",
];

test("a page lists only its own entries plus deck-level blocking ones", async () => {
  const { pageUnresolvedIssues } = await loadModule();
  assert.deepEqual(pageUnresolvedIssues(unresolved, 1).map((issue) => [issue.message, issue.severity]), [
    ["local_ocr_unavailable", "blocking"],
    ["page_1:human_review_required", "blocking"],
    ["advisory:page_1:p1-r3:low_ocr_confidence", "warning"],
  ]);
});

test("a degraded page shows one kept-as-pixels item, not one per entry", async () => {
  const { pageUnresolvedIssues } = await loadModule();
  const issues = pageUnresolvedIssues(unresolved, 2);
  assert.deepEqual(issues.map((issue) => issue.code), [
    "unresolved_source_content",
    "page_kept_as_pixels",
    "unresolved_source_content",
  ]);
  assert.equal(issues.filter((issue) => issue.code === "page_kept_as_pixels").length, 1);
  assert.equal(pageUnresolvedIssues(unresolved, 12)[1].code, "page_kept_as_pixels"); // page_12 is not page_1
  assert.equal(pageUnresolvedIssues(unresolved, 3).length, 1); // only the deck-level blocker
});

test("the deck-level vision notice is detected once and not counted as a page advisory", async () => {
  const { visionReaderUnavailable, otherAdvisoryCount } = await loadModule();
  assert.equal(visionReaderUnavailable(unresolved), true);
  assert.equal(visionReaderUnavailable(unresolved.slice(1)), false);
  assert.equal(otherAdvisoryCount(6, unresolved), 5);
  assert.equal(otherAdvisoryCount(0, unresolved), 0);
});
