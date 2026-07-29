import assert from "node:assert/strict";
import test from "node:test";

import type { StudioSelection } from "./template-v2-studio.ts";
import { toggleTemplateV2Selection } from "./template-v2-studio-ui.ts";

function selection(elementPath: StudioSelection["elementPath"]): StudioSelection {
  return {
    layoutId: "layout",
    componentId: "component",
    elementPath,
  };
}

test("modifier selection adds and removes same-parent siblings in index order", () => {
  const first = selection([2]);
  const second = selection([0]);

  assert.deepEqual(toggleTemplateV2Selection([first], second, true), [
    second,
    first,
  ]);
  assert.deepEqual(
    toggleTemplateV2Selection([second, first], second, true),
    [first]
  );
});

test("modifier selection starts a new selection when the sibling parent changes", () => {
  const topLevel = selection([0]);
  const groupedChild = selection([1, "children", 0]);

  assert.deepEqual(
    toggleTemplateV2Selection([topLevel], groupedChild, true),
    [groupedChild]
  );
});

test("plain selection always replaces the current selection", () => {
  const first = selection([0]);
  const second = selection([1]);

  assert.deepEqual(toggleTemplateV2Selection([first], second, false), [second]);
});
