import assert from "node:assert/strict";
import test from "node:test";

import {
  getTemplateV2CanvasKeyboardIntent,
  getTemplateV2HistoryKeyboardIntent,
  isTemplateV2EditableKeyboardTarget,
  TEMPLATE_V2_COARSE_NUDGE_STEP,
  type TemplateV2CanvasKeyboardAvailability,
  type TemplateV2HistoryKeyboardAvailability,
  type TemplateV2KeyboardEvent,
} from "./template-v2-studio-keyboard.ts";

const available: TemplateV2HistoryKeyboardAvailability = {
  canUndo: true,
  canRedo: true,
};

const movable: TemplateV2CanvasKeyboardAvailability = {
  hasSelection: true,
  canMove: true,
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
