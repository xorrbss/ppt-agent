import React from "react";
import { mount } from "cypress/react";
import MarkdownRenderer from "./MarkDownRender";

describe("MarkdownRenderer sanitization", () => {
  it("keeps Markdown structure while removing executable markup", () => {
    (window as typeof window & { __markdownXss?: boolean }).__markdownXss = false;

    mount(
      <div data-testid="markdown-render-root">
        <MarkdownRenderer
          content={[
            "# Safe heading",
            "",
            "- First item",
            "- Second item",
            "",
            '<img src="x" onerror="window.__markdownXss = true">',
            '<script>window.__markdownXss = true</script>',
            '[unsafe link](javascript:window.__markdownXss)',
          ].join("\n")}
        />
      </div>
    );

    cy.get('[data-testid="markdown-render-root"]').within(() => {
      cy.get("h1").should("have.text", "Safe heading");
      cy.get("li").should("have.length", 2);
      cy.get("script").should("not.exist");
      cy.get('img[onerror]').should("not.exist");
      cy.get('a[href^="javascript:"]').should("not.exist");
    });
    cy.window().its("__markdownXss").should("eq", false);
  });
});
