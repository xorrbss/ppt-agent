import React from "react";

import { renderMathHtml } from "@/lib/math";

function RenderedMath() {
  return (
    <div
      data-cy="rendered-math"
      style={{ fontSize: "40px", width: "640px" }}
      dangerouslySetInnerHTML={{
        __html: renderMathHtml("\\frac{a + b}{c}", { displayMode: true }),
      }}
    />
  );
}

describe("KaTeX component support", () => {
  it("loads KaTeX classes and gives a rendered formula measurable geometry", () => {
    cy.mount(<RenderedMath />);

    cy.get('[data-cy="rendered-math"] > .katex-display')
      .should("have.class", "katex-display")
      .and("have.css", "display", "block");
    cy.get('[data-cy="rendered-math"] .katex')
      .should("have.class", "katex")
      .and(($math) => {
        const bounds = $math[0].getBoundingClientRect();
        expect(bounds.width).to.be.greaterThan(0);
        expect(bounds.height).to.be.greaterThan(0);
      })
      .invoke("css", "font-family")
      .should("contain", "KaTeX_Main");
    cy.get('[data-cy="rendered-math"] .mfrac .frac-line').should(($line) => {
      const bounds = $line[0].getBoundingClientRect();
      expect(bounds.width).to.be.greaterThan(0);
    });
  });
});
