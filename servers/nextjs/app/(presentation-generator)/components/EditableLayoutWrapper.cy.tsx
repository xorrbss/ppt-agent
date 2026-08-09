import React from "react";
import { Provider } from "react-redux";

import { makeStore } from "@/store/store";
import EditableLayoutWrapper from "./EditableLayoutWrapper";

type AxeRuntime = typeof import("axe-core");

const slideData = {
  hero: {
    __image_url__: "/accessibility-image.png",
    __image_prompt__: "A keyboard-accessible image",
  },
  badge: {
    __icon_url__: "/accessibility-icon.svg",
    __icon_query__: "accessibility",
  },
};

function mountEditableAssets() {
  cy.intercept("GET", "**/api/upload-limits", {
    document: { bytes: 10485760, mb: 10, hardMaxMb: 512 },
    image: { bytes: 10485760, mb: 10, hardMaxMb: 64 },
    requestTotal: { bytes: 10485760, mb: 10, hardMaxMb: 512 },
    reason: "test",
  });
  cy.intercept("GET", "**/api/v1/ppt/images/generated", []);
  cy.intercept("GET", "**/api/v1/ppt/icons/search*", []);

  cy.mount(
    <Provider store={makeStore()}>
      <EditableLayoutWrapper slideIndex={0} slideData={slideData}>
        <div className="grid grid-cols-2 gap-4 p-8">
          {/* eslint-disable-next-line @next/next/no-img-element -- Native asset fixture mirrors template output. */}
          <img src="/accessibility-image.png" alt="Quarterly revenue chart" />
          <span
            data-path="/accessibility-icon.svg"
            role="img"
            aria-label="Accessibility symbol"
          >
            <svg viewBox="0 0 24 24" aria-hidden="true">
              <circle cx="12" cy="12" r="10" />
            </svg>
          </span>
        </div>
      </EditableLayoutWrapper>
    </Provider>,
  );

  cy.get('[data-editable-asset="image"]', { timeout: 3000 }).should("exist");
  cy.get('[data-editable-asset="icon"]', { timeout: 3000 }).should("exist");
}

function assertNoSevereAxeViolations() {
  cy.readFile("node_modules/axe-core/axe.min.js", "utf8")
    .then((source: string) =>
      cy.window().then((window) => {
        window.Function(source)();
        const pageAxe = (window as typeof window & { axe?: AxeRuntime }).axe;
        if (!pageAxe) throw new Error("axe-core did not attach to the component window");
        const dialog = window.document.querySelector('[role="dialog"]');
        if (!dialog) throw new Error("Editor dialog was not mounted for the axe audit");
        return pageAxe.run(dialog);
      }),
    )
    .then((results) => {
      const severe = results.violations.filter(
        ({ impact }) => impact === "serious" || impact === "critical",
      );
      const summary = severe
        .map(
          (violation) =>
            `${violation.id}: ${violation.nodes
              .map((node) => node.target.join(" > "))
              .join(", ")}`,
        )
        .join("\n");
      expect(severe, summary).to.have.length(0);
    });
}

describe("EditableLayoutWrapper accessibility", () => {
  beforeEach(() => {
    mountEditableAssets();
  });

  it("opens image editing with Tab and Enter, then returns focus on Escape", () => {
    cy.get('[data-editable-asset="image"]')
      .should("have.attr", "role", "button")
      .and("have.attr", "tabindex", "0")
      .and("have.attr", "aria-haspopup", "dialog")
      .and("have.attr", "aria-label", "Edit image: Quarterly revenue chart");

    cy.get("body").click(0, 0);
    cy.press(Cypress.Keyboard.Keys.TAB);
    cy.focused().should("have.attr", "data-editable-asset", "image");
    cy.press(Cypress.Keyboard.Keys.ENTER);

    cy.get('[role="dialog"]').should(($dialog) => {
      expect($dialog[0].contains($dialog[0].ownerDocument.activeElement)).to.eq(true);
    });
    cy.press(Cypress.Keyboard.Keys.ESC);
    cy.get('[role="dialog"]').should("not.exist");
    cy.get('[data-editable-asset="image"]').should("be.focused");
  });

  it("opens an SVG icon wrapper with Space and preserves its mouse path", () => {
    cy.get('[data-editable-asset="icon"]')
      .should("match", "span[data-path]")
      .and("have.attr", "role", "button")
      .and("have.attr", "aria-label", "Edit icon: Accessibility symbol")
      .focus();
    cy.press(Cypress.Keyboard.Keys.SPACE);
    cy.get('[role="dialog"]').should("be.visible");
    cy.press(Cypress.Keyboard.Keys.ESC);
    cy.get('[data-editable-asset="icon"]').should("be.focused").click();
    cy.get('[role="dialog"]').should("be.visible");
    assertNoSevereAxeViolations();
  });

  it("supports keyboard focus-point adjustment inside the image editor", () => {
    cy.get('[data-editable-asset="image"]').focus();
    cy.press(Cypress.Keyboard.Keys.ENTER);
    cy.contains('[role="tab"]', "편집").click();
    cy.get('[role="button"][aria-label^="Adjust image focus point"]')
      .focus()
      .should("have.attr", "aria-pressed", "false");
    cy.press(Cypress.Keyboard.Keys.SPACE);
    cy.focused().should("have.attr", "aria-pressed", "true");
    cy.press(Cypress.Keyboard.Keys.RIGHT);
    cy.focused().should("have.attr", "aria-label").and("contain", "Horizontal 52 percent");
    cy.press(Cypress.Keyboard.Keys.ESC);
    cy.focused().should("have.attr", "aria-pressed", "false");
  });
});
