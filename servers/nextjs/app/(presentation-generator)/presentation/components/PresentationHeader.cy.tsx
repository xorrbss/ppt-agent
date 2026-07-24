import React from "react";
import { Provider } from "react-redux";
import { AppRouterContext } from "next/dist/shared/lib/app-router-context.shared-runtime";
import { PathnameContext } from "next/dist/shared/lib/hooks-client-context.shared-runtime";

import PresentationHeader from "./PresentationHeader";
import { PresentationGenerationApi } from "../../services/api/presentation-generation";
import ThemeApi from "../../services/api/theme";
import { store } from "@/store/store";
import {
  setPresentationData,
  setStreaming,
} from "@/store/slices/presentationGeneration";
import { isAuthoredPresentation } from "../utils/isAuthoredPresentation";

const mountHeader = (isAuthoredDeck: boolean) => {
  store.dispatch(
    setPresentationData({
      id: "deck-1",
      language: "en",
      layout: isAuthoredDeck ? null : { name: "adaptive", ordered: false, slides: [] },
      n_slides: 1,
      title: "deck",
      slides: [{ id: "slide-1", layout: "standard" }],
      theme: null,
      mode: isAuthoredDeck ? "authored" : "adaptive",
    } as any)
  );
  store.dispatch(setStreaming(false));

  cy.mount(
    <AppRouterContext.Provider
      value={{
        back: cy.stub(),
        forward: cy.stub(),
        refresh: cy.stub(),
        push: cy.stub(),
        replace: cy.stub(),
        prefetch: cy.stub(),
        hmrRefresh: cy.stub(),
      } as any}
    >
      <PathnameContext.Provider value="/presentation/deck-1">
        <Provider store={store}>
          <PresentationHeader
            presentation_id="deck-1"
            isPresentationSaving={false}
            isAuthoredDeck={isAuthoredDeck}
          />
        </Provider>
      </PathnameContext.Provider>
    </AppRouterContext.Provider>
  );
};

const openExportOptions = () => cy.get('[data-testid="export-trigger"]').click();

const expectExportBody = (expected: Record<string, string>) =>
  cy
    .wait("@exportPresentation")
    .its("request.body")
    .then((body) => expect(JSON.parse(body)).to.deep.equal(expected));

describe("PresentationHeader authored export", () => {
  beforeEach(() => {
    cy.stub(ThemeApi, "getThemes").as("getThemes").resolves([]);
    cy.stub(PresentationGenerationApi, "updatePresentationContent").resolves({});
    cy.intercept("POST", "**/api/export-presentation", (request) => {
      request.reply({
        statusCode: 200,
        headers: {
          "content-type": "application/octet-stream",
          "content-disposition": 'attachment; filename="deck.pptx"',
        },
        body: { path: "/exports/deck.pptx" },
      });
    }).as("exportPresentation");
  });

  it("shows template conversion instead of an ineffective theme control for authored decks", () => {
    mountHeader(true);

    cy.get('[data-testid="template-change-trigger"]')
      .should("be.visible")
      .and("contain.text", "템플릿 변경");
    cy.get('[data-testid="quality-review-trigger"]')
      .should("be.visible")
      .and("contain.text", "고품질 검수");
    cy.contains("button", "테마").should("not.exist");
    cy.get("@getThemes").should("not.have.been.called");
  });

  it("keeps the theme control for regular editable decks", () => {
    mountHeader(false);

    cy.get('[data-testid="template-change-trigger"]').should("not.exist");
    cy.contains("button", "테마").should("be.visible");
    cy.get("@getThemes").should("have.been.calledOnce");
  });

  it("sends fidelity by default for the authored design-preserving option", () => {
    mountHeader(true);

    openExportOptions();
    cy.get('[data-testid="authored-export-fidelity"]').click();
    expectExportBody({
      format: "pptx",
      id: "deck-1",
      title: "deck",
      pptxMode: "fidelity",
    });
  });

  it("sends hybrid for editable text and preserves the PDF request shape", () => {
    mountHeader(true);

    openExportOptions();
    cy.get('[data-testid="authored-export-hybrid"]').click({ force: true });
    expectExportBody({
      format: "pptx",
      id: "deck-1",
      title: "deck",
      pptxMode: "hybrid",
    });

    cy.get('[data-testid="export-trigger"]').should("not.be.disabled");
    openExportOptions();
    cy.get('[data-testid="export-pdf"]').click({ force: true });
    expectExportBody({
      format: "pdf",
      id: "deck-1",
      title: "deck",
    });
  });

  it("does not expose authored choices or alter the adaptive PPTX body", () => {
    mountHeader(false);

    openExportOptions();
    cy.get('[data-testid="authored-export-fidelity"]').should("not.exist");
    cy.get('[data-testid="authored-export-hybrid"]').should("not.exist");
    cy.get('[data-testid="export-pptx"]').click();
    expectExportBody({
      format: "pptx",
      id: "deck-1",
      title: "deck",
    });
  });

  it("keeps the authored export trigger focusable and restores focus after export", () => {
    cy.viewport(360, 640);
    mountHeader(true);

    cy.get('[data-testid="export-trigger"]').should(
      "have.prop",
      "tagName",
      "BUTTON",
    );
    cy.get('[data-testid="export-trigger"]')
      .focus()
      .should("be.focused");
    cy.get('[data-testid="export-trigger"]').click();
    cy.get('[data-testid="authored-export-fidelity"]')
      .should("have.attr", "aria-label")
      .and("not.be.empty");
    cy.get('[data-testid="authored-export-fidelity"]').click();
    cy.wait("@exportPresentation");
    cy.get('[data-testid="export-trigger"]').should("be.focused");
  });

  it("disables a second request while the export is loading", () => {
    cy.intercept("POST", "**/api/export-presentation", {
      statusCode: 200,
      delay: 500,
      body: { path: "/exports/deck.pptx" },
    }).as("slowExport");
    mountHeader(true);

    openExportOptions();
    cy.get('[data-testid="authored-export-fidelity"]').click();
    cy.get('[data-testid="export-trigger"]').should("be.disabled");
    cy.get('[role="status"]').should("exist");
    cy.wait("@slowExport");
    cy.get("@slowExport.all").should("have.length", 1);
  });

  it("recovers from an export error without leaving the trigger disabled", () => {
    cy.intercept("POST", "**/api/export-presentation", { statusCode: 500 }).as(
      "failedExport"
    );
    mountHeader(true);

    openExportOptions();
    cy.get('[data-testid="authored-export-fidelity"]').click();
    cy.wait("@failedExport");
    cy.get('[data-testid="export-trigger"]').should("not.be.disabled");
  });
});

describe("legacy authored presentation detection", () => {
  it("recognizes a saved authored deck from its slide sentinel", () => {
    expect(
      isAuthoredPresentation({
        mode: "template",
        theme: null,
        layout: { name: "legacy-layout" },
        slides: [
          {
            layout_group: "authored",
            layout: "authored:content",
            content: { __authored__: true },
          },
        ],
      }),
    ).to.equal(true);
  });

  it("does not classify a regular saved template as authored", () => {
    expect(
      isAuthoredPresentation({
        mode: "template",
        theme: null,
        slides: [
          {
            layout_group: "business-template",
            layout: "business-template:content",
            content: {},
          },
        ],
      }),
    ).to.equal(false);
  });
});
