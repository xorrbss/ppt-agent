import assert from "node:assert/strict";
import test from "node:test";

import {
  getSlideNavigationIntent,
  isEditorEditableTarget,
} from "./slideKeyboardNavigation.ts";

test("maps unmodified arrows to bounded slide-navigation intents", () => {
  assert.equal(getSlideNavigationIntent({ key: "ArrowLeft" }), -1);
  assert.equal(getSlideNavigationIntent({ key: "ArrowUp" }), -1);
  assert.equal(getSlideNavigationIntent({ key: "ArrowRight" }), 1);
  assert.equal(getSlideNavigationIntent({ key: "ArrowDown" }), 1);
  assert.equal(getSlideNavigationIntent({ key: "Enter" }), null);
});

test("leaves consumed, composing, repeated, modified, and editable events alone", () => {
  assert.equal(getSlideNavigationIntent({ key: "ArrowRight", defaultPrevented: true }), null);
  assert.equal(getSlideNavigationIntent({ key: "ArrowRight", isComposing: true }), null);
  assert.equal(getSlideNavigationIntent({ key: "ArrowRight", repeat: true }), null);
  assert.equal(getSlideNavigationIntent({ key: "ArrowRight", ctrlKey: true }), null);
  assert.equal(getSlideNavigationIntent({ key: "ArrowRight", target: { tagName: "TEXTAREA" } }), null);
  assert.equal(
    getSlideNavigationIntent({
      key: "ArrowRight",
      target: { tagName: "DIV" },
      composedPath: () => [{ contentEditable: "plaintext-only" }],
    }),
    null,
  );
});

test("recognizes standard native and rich-text editing targets", () => {
  assert.equal(isEditorEditableTarget({ tagName: "INPUT" }), true);
  assert.equal(isEditorEditableTarget({ nodeName: "textarea" }), true);
  assert.equal(isEditorEditableTarget({ contentEditable: "true" }), true);
  assert.equal(isEditorEditableTarget({ isContentEditable: true }), true);
  assert.equal(isEditorEditableTarget({ tagName: "BUTTON" }), false);
});
