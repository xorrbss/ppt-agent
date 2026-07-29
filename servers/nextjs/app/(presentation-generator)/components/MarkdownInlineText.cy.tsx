import React from "react";
import { mount } from "cypress/react";
import MarkdownInlineText from "./MarkdownInlineText";

describe("MarkdownInlineText sanitization", () => {
  it("renders safe inline Markdown without preserving scripts or event handlers", () => {
    (window as typeof window & { __inlineMarkdownXss?: boolean }).__inlineMarkdownXss = false;

    mount(
      <div data-testid="inline-markdown-root">
        <MarkdownInlineText
          content={'**Safe** <img src="x" onerror="window.__inlineMarkdownXss = true"> <script>window.__inlineMarkdownXss = true</script> [unsafe](javascript:window.__inlineMarkdownXss)'}
        />
      </div>
    );

    cy.get('[data-testid="inline-markdown-root"]').within(() => {
      cy.get("strong").should("have.text", "Safe");
      cy.get("script").should("not.exist");
      cy.get('img[onerror]').should("not.exist");
      cy.get('a[href^="javascript:"]').should("not.exist");
    });
    cy.window().its("__inlineMarkdownXss").should("eq", false);
  });
});
