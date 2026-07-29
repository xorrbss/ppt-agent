import assert from "node:assert/strict";
import test from "node:test";

import {
  getTemplateV2CanvasKeyboardIntent,
  getTemplateV2HistoryKeyboardIntent,
  getTemplateV2SelectionKeyboardIntent,
  isTemplateV2AdditiveSelectionModifier,
  isTemplateV2EditableKeyboardTarget,
  TEMPLATE_V2_COARSE_NUDGE_STEP,
  type TemplateV2CanvasKeyboardAvailability,
  type TemplateV2HistoryKeyboardAvailability,
  type TemplateV2KeyboardEvent,
  type TemplateV2SelectionKeyboardAvailability,
} from "./template-v2-studio-keyboard.ts";

const available: TemplateV2HistoryKeyboardAvailability = {
  canUndo: true,
  canRedo: true,
};

const movable: TemplateV2CanvasKeyboardAvailability = {
  hasSelection: true,
  canMove: true,
};

const selectionAvailable: TemplateV2SelectionKeyboardAvailability = {
  selectionCount: 2,
  canGroup: true,
  canUngroup: true,
  canDelete: true,
  canDuplicate: true,
};

function intent(
  event: TemplateV2KeyboardEvent,
  availability: TemplateV2HistoryKeyboardAvailability = available
) {
  return getTemplateV2HistoryKeyboardIntent(event, availability);
}

function canvasIntent(
  event: TemplateV2KeyboardEvent,
  availability: TemplateV2CanvasKeyboardAvailability = movable
) {
  return getTemplateV2CanvasKeyboardIntent(event, availability);
}

function selectionIntent(
  event: TemplateV2KeyboardEvent,
  availability: TemplateV2SelectionKeyboardAvailability = selectionAvailable
) {
  return getTemplateV2SelectionKeyboardIntent(event, availability);
}

test("recognizes Ctrl/Cmd undo and both redo variants", () => {
  assert.equal(intent({ key: "z", ctrlKey: true }), "undo");
  assert.equal(intent({ key: "Z", metaKey: true }), "undo");
  assert.equal(intent({ key: "z", ctrlKey: true, shiftKey: true }), "redo");
  assert.equal(intent({ key: "Z", metaKey: true, shiftKey: true }), "redo");
  assert.equal(intent({ key: "y", ctrlKey: true }), "redo");
  assert.equal(intent({ key: "Y", metaKey: true }), "redo");
});

test("rejects incomplete, unrelated, Alt, and nonstandard modified chords", () => {
  assert.equal(intent({ key: "z" }), null);
  assert.equal(intent({ key: "x", ctrlKey: true }), null);
  assert.equal(intent({ key: "z", ctrlKey: true, altKey: true }), null);
  assert.equal(
    intent({ key: "y", ctrlKey: true, shiftKey: true }),
    null
  );
});

test("repeat, disabled history, and unavailable directions are no-ops", () => {
  assert.equal(intent({ key: "z", ctrlKey: true, repeat: true }), null);
  assert.equal(
    intent({ key: "z", ctrlKey: true }, { ...available, disabled: true }),
    null
  );
  assert.equal(
    intent({ key: "z", ctrlKey: true }, { canUndo: false, canRedo: true }),
    null
  );
  assert.equal(
    intent(
      { key: "z", ctrlKey: true, shiftKey: true },
      { canUndo: true, canRedo: false }
    ),
    null
  );
  assert.equal(
    intent({ key: "y", ctrlKey: true }, { canUndo: true, canRedo: false }),
    null
  );
  assert.equal(
    intent({ key: "z", ctrlKey: true, isComposing: true }),
    null
  );
  assert.equal(
    intent({ key: "z", ctrlKey: true, metaKey: true }),
    null
  );
});

test("identifies native and contenteditable text-editing targets", () => {
  for (const tagName of ["INPUT", "textarea", "Select"]) {
    assert.equal(isTemplateV2EditableKeyboardTarget({ tagName }), true);
  }
  assert.equal(
    isTemplateV2EditableKeyboardTarget({ isContentEditable: true }),
    true
  );
  assert.equal(
    isTemplateV2EditableKeyboardTarget({ contentEditable: "plaintext-only" }),
    true
  );
  assert.equal(isTemplateV2EditableKeyboardTarget({ tagName: "button" }), false);
});

test("ignores shortcuts when any node in composedPath is editable", () => {
  const child = { nodeName: "#text" };
  const editor = { isContentEditable: true };
  assert.equal(
    intent({
      key: "z",
      ctrlKey: true,
      target: child,
      composedPath: () => [child, editor, { tagName: "DIV" }],
    }),
    null
  );
  assert.equal(
    intent({
      key: "y",
      ctrlKey: true,
      target: { tagName: "SPAN" },
      composedPath: () => [{ tagName: "SPAN" }, { tagName: "TEXTAREA" }],
    }),
    null
  );
});

test("arrow keys nudge by one unit and Shift nudges by the grid step", () => {
  assert.deepEqual(canvasIntent({ key: "ArrowLeft" }), {
    type: "nudge",
    deltaX: -1,
    deltaY: 0,
  });
  assert.deepEqual(canvasIntent({ key: "ArrowDown" }), {
    type: "nudge",
    deltaX: 0,
    deltaY: 1,
  });
  assert.deepEqual(canvasIntent({ key: "ArrowRight", shiftKey: true }), {
    type: "nudge",
    deltaX: TEMPLATE_V2_COARSE_NUDGE_STEP,
    deltaY: 0,
  });
  assert.deepEqual(canvasIntent({ key: "ArrowUp", shiftKey: true }), {
    type: "nudge",
    deltaX: 0,
    deltaY: -TEMPLATE_V2_COARSE_NUDGE_STEP,
  });
});

test("Escape clears an existing selection only", () => {
  assert.deepEqual(canvasIntent({ key: "Escape" }), {
    type: "clear-selection",
  });
  assert.deepEqual(
    canvasIntent({ key: "Escape" }, { hasSelection: true, canMove: false }),
    { type: "clear-selection" }
  );
  assert.equal(
    canvasIntent({ key: "Escape" }, { hasSelection: false, canMove: false }),
    null
  );
});

test("canvas keys yield to history chords, repeat, editing, and locked selections", () => {
  assert.equal(canvasIntent({ key: "ArrowLeft", ctrlKey: true }), null);
  assert.equal(canvasIntent({ key: "ArrowLeft", metaKey: true }), null);
  assert.equal(canvasIntent({ key: "ArrowLeft", altKey: true }), null);
  assert.equal(canvasIntent({ key: "ArrowLeft", repeat: true }), null);
  assert.equal(canvasIntent({ key: "Escape", repeat: true }), null);
  assert.equal(canvasIntent({ key: "Escape", isComposing: true }), null);
  assert.equal(
    canvasIntent({ key: "ArrowLeft", target: { tagName: "INPUT" } }),
    null
  );
  assert.equal(
    canvasIntent({ key: "ArrowLeft" }, { hasSelection: true, canMove: false }),
    null
  );
  assert.equal(
    canvasIntent({ key: "ArrowLeft" }, { hasSelection: false, canMove: true }),
    null
  );
  assert.equal(canvasIntent({ key: "Tab" }), null);
});

test("selection shortcuts map Group, Ungroup, Duplicate, and Delete commands", () => {
  assert.equal(
    selectionIntent({ key: "g", ctrlKey: true }),
    "group-siblings"
  );
  assert.equal(
    selectionIntent({ key: "G", metaKey: true, shiftKey: true }),
    "ungroup"
  );
  assert.equal(
    selectionIntent({ key: "d", ctrlKey: true }),
    "duplicate-selection"
  );
  assert.equal(selectionIntent({ key: "Delete" }), "delete-selection");
  assert.equal(selectionIntent({ key: "Backspace" }), "delete-selection");
});

test("selection shortcuts honor capabilities and exact modifier contracts", () => {
  assert.equal(
    selectionIntent({ key: "g", ctrlKey: true }, {
      ...selectionAvailable,
      canGroup: false,
    }),
    null
  );
  assert.equal(
    selectionIntent({ key: "g", metaKey: true, shiftKey: true }, {
      ...selectionAvailable,
      canUngroup: false,
    }),
    null
  );
  assert.equal(
    selectionIntent({ key: "d", ctrlKey: true }, {
      ...selectionAvailable,
      canDuplicate: false,
    }),
    null
  );
  assert.equal(
    selectionIntent({ key: "Delete" }, {
      ...selectionAvailable,
      canDelete: false,
    }),
    null
  );
  assert.equal(
    selectionIntent(
      { key: "g", ctrlKey: true },
      { ...selectionAvailable, selectionCount: 0 }
    ),
    null
  );
  assert.equal(
    selectionIntent(
      { key: "g", ctrlKey: true },
      { ...selectionAvailable, disabled: true }
    ),
    null
  );
  assert.equal(selectionIntent({ key: "g" }), null);
  assert.equal(selectionIntent({ key: "g", shiftKey: true }), null);
  assert.equal(
    selectionIntent({ key: "g", ctrlKey: true, metaKey: true }),
    null
  );
  assert.equal(
    selectionIntent({ key: "d", ctrlKey: true, shiftKey: true }),
    null
  );
  assert.equal(
    selectionIntent({ key: "Delete", ctrlKey: true }),
    null
  );
  assert.equal(
    selectionIntent({ key: "Backspace", shiftKey: true }),
    null
  );
});

test("selection shortcuts yield to editable targets, IME, repeat, and Alt", () => {
  for (const event of [
    { key: "g", ctrlKey: true, repeat: true },
    { key: "g", ctrlKey: true, isComposing: true },
    { key: "g", ctrlKey: true, altKey: true },
    { key: "Delete", isComposing: true },
    { key: "Delete", target: { tagName: "INPUT" } },
    {
      key: "d",
      metaKey: true,
      target: { nodeName: "#text" },
      composedPath: () => [
        { nodeName: "#text" },
        { contentEditable: "plaintext-only" },
      ],
    },
  ] satisfies TemplateV2KeyboardEvent[]) {
    assert.equal(selectionIntent(event), null);
  }
});

test("additive canvas selection is exactly Ctrl or Cmd without Shift/Alt", () => {
  assert.equal(isTemplateV2AdditiveSelectionModifier({ ctrlKey: true }), true);
  assert.equal(isTemplateV2AdditiveSelectionModifier({ metaKey: true }), true);
  assert.equal(isTemplateV2AdditiveSelectionModifier({}), false);
  assert.equal(
    isTemplateV2AdditiveSelectionModifier({ ctrlKey: true, metaKey: true }),
    false
  );
  assert.equal(
    isTemplateV2AdditiveSelectionModifier({ ctrlKey: true, shiftKey: true }),
    false
  );
  assert.equal(
    isTemplateV2AdditiveSelectionModifier({ metaKey: true, altKey: true }),
    false
  );
});

test("ordinary targets and unusable synthetic composed paths stay safe", () => {
  assert.equal(
    intent({
      key: "z",
      ctrlKey: true,
      target: { tagName: "BUTTON" },
      composedPath: () => {
        throw new Error("synthetic event");
      },
    }),
    "undo"
  );
});
