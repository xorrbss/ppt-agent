import assert from "node:assert/strict";
import test from "node:test";

import {
  getTemplateV2HistoryKeyboardIntent,
  isTemplateV2EditableKeyboardTarget,
  type TemplateV2HistoryKeyboardAvailability,
  type TemplateV2HistoryKeyboardEvent,
} from "./template-v2-studio-keyboard.ts";

const available: TemplateV2HistoryKeyboardAvailability = {
  canUndo: true,
  canRedo: true,
};

function intent(
  event: TemplateV2HistoryKeyboardEvent,
  availability: TemplateV2HistoryKeyboardAvailability = available
) {
  return getTemplateV2HistoryKeyboardIntent(event, availability);
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
