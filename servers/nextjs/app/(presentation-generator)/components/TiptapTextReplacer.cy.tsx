import React from "react";
import { mount } from "cypress/react";

import TiptapTextReplacer from "./TiptapTextReplacer";

// The replacer binds edits back to slide content. An explicit data-edit-path must
// win over the string-match fallback — so a custom template can bind reliably even
// when two fields share the same text.
describe("TiptapTextReplacer data-edit-path binding", () => {
  it("binds by the declared path, not by matching duplicate text", () => {
    const onChange = cy.stub().as("onChange");

    // Both fields render "dup". Without data-edit-path the first leaf would bind to
    // "a" (occurrence 0); the annotation forces the first leaf to "b".
    mount(
      <TiptapTextReplacer
        slideData={{ a: "dup", b: "dup" }}
        slideIndex={0}
        onContentChange={onChange}
      >
        <div>
          <h1 data-edit-path="b">dup</h1>
          <h2>dup</h2>
        </div>
      </TiptapTextReplacer>
    );

    // The edit-mode DOM walk runs on a ~1s timer, then mounts inline editors.
    cy.get(".tiptap-text-editor [contenteditable='true']", { timeout: 8000 })
      .first()
      .click()
      .type(" x")
      .blur();

    cy.get("@onChange").should("have.been.called");
    cy.get("@onChange")
      .its("firstCall.args.1")
      .should("deep.equal", { kind: "path", key: "b" });
  });
});
