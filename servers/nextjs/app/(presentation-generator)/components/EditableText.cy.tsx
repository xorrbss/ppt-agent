import React from "react";
import { mount } from "cypress/react";

import EditableText from "./EditableText";
import { EditableTextProvider } from "./EditableTextContext";

describe("EditableText", () => {
  it("read-only: renders the semantic tag with synchronous markdown and no editor", () => {
    mount(
      <EditableTextProvider
        value={{ slideIndex: 0, isEditMode: false, onEdit: cy.stub() }}
      >
        <EditableText as="h1" path="title" value="Hello **World**" />
      </EditableTextProvider>
    );

    cy.get("h1[data-editable-native]").should("exist");
    // markdown emphasis is present on first paint (export invariant)
    cy.get("h1[data-editable-native] strong").should("contain.text", "World");
    // the DOM-surgery editor must not appear in read-only mode
    cy.get(".tiptap-text-editor").should("not.exist");
  });

  it("edit: renders an inline editor and writes back by path on blur", () => {
    const onEdit = cy.stub().as("onEdit");
    mount(
      <EditableTextProvider value={{ slideIndex: 0, isEditMode: true, onEdit }}>
        <EditableText as="h1" path="title" value="Hi" />
      </EditableTextProvider>
    );

    cy.get("h1[data-editable-native]").should("exist");
    cy.get(".tiptap-text-editor [contenteditable='true']")
      .should("exist")
      .click()
      .type(" there")
      .blur();

    cy.get("@onEdit").should("have.been.calledWith", "title");
  });

  it("read-only default (no provider): still renders, no editor", () => {
    mount(<EditableText as="p" path="body" value="plain" />);
    cy.get("p[data-editable-native]").should("contain.text", "plain");
    cy.get(".tiptap-text-editor").should("not.exist");
  });
});
