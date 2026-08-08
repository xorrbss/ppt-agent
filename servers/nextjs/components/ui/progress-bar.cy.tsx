import React from "react";
import { mount } from "cypress/react";

import { ProgressBar } from "./progress-bar";

describe("ProgressBar", () => {
  it("uses controlled slide progress and its label", () => {
    mount(
      <ProgressBar
        duration={600}
        value={51}
        label="슬라이드 46/90 완료"
      />
    );

    cy.get('[role="progressbar"]')
      .should("have.attr", "aria-valuenow", "51")
      .find("div")
      .should("have.attr", "style")
      .and("include", "width: 51%");
    cy.get('[data-cy="progress-label"]').should(
      "have.text",
      "슬라이드 46/90 완료"
    );
    cy.contains("51%");
  });

  it("retains the duration-based fallback when no checkpoint is available", () => {
    cy.clock();
    mount(<ProgressBar duration={10} />);

    cy.get('[role="progressbar"]').should("have.attr", "aria-valuenow", "0");
    cy.tick(5_000);
    cy.get('[role="progressbar"]').should("have.attr", "aria-valuenow", "50");
  });
});
