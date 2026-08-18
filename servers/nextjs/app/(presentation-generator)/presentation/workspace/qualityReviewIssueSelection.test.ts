import assert from "node:assert/strict";
import test from "node:test";

import {
  remainingIssueRows,
  selectableSlideIndexes,
  toggleSelection,
} from "./qualityReviewIssueSelection.ts";

test("an issue with no slide attribution can never be selected for a remake", () => {
  // Deck-level residual issues and the degraded string envelope both arrive
  // without a slide index; a remake needs a page to rebuild.
  const rows = remainingIssueRows([
    { description: "dom.clipping" },
    { slideIndex: 3, description: "text_overflow" },
  ]);
  assert.deepEqual(
    rows.map((row) => row.selectable),
    [false, true],
  );
  assert.deepEqual(selectableSlideIndexes([{ description: "dom.clipping" }]), []);
});

test("two issues on one slide are one checkbox, not two remakes", () => {
  const issues = [
    { slideIndex: 6, description: "text_overflow" },
    { slideIndex: 6, description: "dom.clipping" },
    { slideIndex: 2, description: "contrast" },
  ];
  assert.deepEqual(
    remainingIssueRows(issues).map((row) => row.selectable),
    [true, false, true],
  );
  assert.deepEqual(selectableSlideIndexes(issues), [2, 6]);
});

test("selection holds slide indexes in ascending order and toggles them", () => {
  const first = toggleSelection([], 7);
  assert.deepEqual(first, [7]);
  const second = toggleSelection(first, 2);
  assert.deepEqual(second, [2, 7]);
  assert.deepEqual(toggleSelection(second, 7), [2]);
  // Toggling an already-selected slide removes it rather than duplicating it.
  assert.deepEqual(toggleSelection([2, 7], 2), [7]);
});

test("toggling does not mutate the caller's selection", () => {
  const selected = [4];
  toggleSelection(selected, 9);
  assert.deepEqual(selected, [4]);
});
