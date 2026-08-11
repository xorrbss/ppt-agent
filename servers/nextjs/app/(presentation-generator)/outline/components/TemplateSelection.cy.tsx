import React, { useState } from "react";
import { mount } from "cypress/react";
import { Provider } from "react-redux";
import { AppRouterContext } from "next/dist/shared/lib/app-router-context.shared-runtime";

import type { TemplateLayoutsWithSettings } from "@/app/presentation-templates/utils";
import { store } from "@/store/store";

import TemplateSelection, { AUTHORED_TEMPLATE_ID } from "./TemplateSelection";
import { catalogStyles } from "./TemplateSelection.test-data";

import "@/app/globals.css";

const createRouter = () => ({
  push: cy.stub(),
  back: cy.stub(),
  forward: cy.stub(),
  refresh: cy.stub(),
  replace: cy.stub(),
  prefetch: cy.stub().resolves(),
  route: "/",
  pathname: "/",
  query: {},
  asPath: "/",
});

function SelectionHarness({
  initialSelection = null,
  onSelect,
}: {
  initialSelection?: string | null;
  onSelect: (template: TemplateLayoutsWithSettings | string) => void;
}) {
  const [selectedTemplate, setSelectedTemplate] = useState<string | null>(
    initialSelection
  );

  return (
    <TemplateSelection
      selectedTemplate={selectedTemplate}
      onSelectTemplate={(template) => {
        onSelect(template);
        setSelectedTemplate(typeof template === "string" ? template : template.id);
      }}
    />
  );
}

function mountSelection(initialSelection: string | null = null) {
  const onSelect = cy.stub().as("selectTemplate");
  mount(
    <AppRouterContext.Provider value={createRouter() as any}>
      <Provider store={store}>
        <SelectionHarness initialSelection={initialSelection} onSelect={onSelect} />
      </Provider>
    </AppRouterContext.Provider>
  );
}

function interceptAuthoredCatalog() {
  cy.intercept("GET", "**/api/v1/ppt/authored/styles", {
    statusCode: 200,
    body: catalogStyles,
  }).as("authoredStyles");
}

describe("<TemplateSelection /> Template V2 catalog", () => {
  beforeEach(() => {
    cy.viewport(1280, 900);
  });

  it("keeps Authored separate and selects the single V2 default", () => {
    cy.intercept("GET", "**/api/v1/ppt/structured-templates*", {
      statusCode: 200,
      body: [
        {
          id: "quarterly-review",
          name: "Quarterly Review",
          description: "Native editable business template",
          revision: 12,
          is_default: true,
          source: {
            repository: "https://github.com/hugohe3/ppt-master",
            commit_sha: "8858c4960b870f12baf952994fbd0c76ab5ba4ec",
            path: "examples/ppt169_swiss_grid_systems",
          },
          provenance: { repository: "hugohe3/ppt-master" },
          capability_badges: ["Editable PPTX", "Master retained"],
          assets: { thumbnail: "/app_data/templates/quarterly-review/thumbnail.png" },
          layouts: { layouts: [{ id: "cover" }, { id: "content" }] },
        },
      ],
    }).as("structuredTemplates");
    interceptAuthoredCatalog();
    mountSelection();

    cy.wait("@structuredTemplates");
    cy.get('[data-testid="authored-template-section"]').should("be.visible");
    cy.get('[data-testid="layout-template-grid"]').should("be.visible");
    cy.get('[data-testid="structured-template-quarterly-review"]')
      .should("contain", "Template V2")
      .and("contain", "R12")
      .and("contain", "기본")
      .and("contain", "2개 레이아웃");
    cy.get('[data-testid="structured-template-source-quarterly-review"]').should(
      "contain",
      "github.com/hugohe3/ppt-master · examples/ppt169_swiss_grid_systems"
    );
    cy.get('[data-testid="structured-template-capabilities-quarterly-review"]').should(
      "contain",
      "마스터 유지"
    );
    cy.get("a[href='/custom-template']").should("not.exist");
    cy.get("[data-testid^='custom-template-']").should("not.exist");
    cy.get("[data-testid^='inbuilt-template-']").should("not.exist");
    cy.get("@selectTemplate").should(
      "have.been.calledWith",
      "template-v2:quarterly-review?revision=12"
    );
  });

  it("does not fall back to legacy cards when the V2 catalog is empty", () => {
    cy.intercept("GET", "**/api/v1/ppt/structured-templates*", {
      statusCode: 200,
      body: [],
    }).as("emptyStructuredTemplates");
    interceptAuthoredCatalog();
    mountSelection();

    cy.wait("@emptyStructuredTemplates");
    cy.get('[data-testid="template-v2-catalog-empty"]').should("be.visible");
    cy.get('[data-testid="layout-template-grid"]').should("not.exist");
    cy.get("a[href='/custom-template']").should("not.exist");
  });

  it("fails closed when the catalog has no unique default", () => {
    cy.intercept("GET", "**/api/v1/ppt/structured-templates*", {
      statusCode: 200,
      body: [
        { id: "one", name: "One", description: null, revision: 1, is_default: true },
        { id: "two", name: "Two", description: null, revision: 1, is_default: true },
      ],
    }).as("invalidStructuredTemplates");
    interceptAuthoredCatalog();
    mountSelection();

    cy.wait("@invalidStructuredTemplates");
    cy.get('[data-testid="template-v2-catalog-configuration-error"]').should("be.visible");
    cy.get('[data-testid="layout-template-grid"]').should("not.exist");
    cy.get("@selectTemplate").should("not.have.been.called");
  });

  it("preserves a rehydrated legacy selection instead of replacing it", () => {
    cy.intercept("GET", "**/api/v1/ppt/structured-templates*", {
      statusCode: 200,
      body: [
        { id: "v2", name: "V2", description: null, revision: 3, is_default: true },
      ],
    }).as("structuredTemplates");
    interceptAuthoredCatalog();
    mountSelection("adaptive");

    cy.wait("@structuredTemplates");
    cy.get("@selectTemplate").should("not.have.been.called");
    cy.get('[data-testid="structured-template-v2"]').should("be.visible");
  });

  it("continues to offer Authored as its own mode", () => {
    cy.intercept("GET", "**/api/v1/ppt/structured-templates*", {
      statusCode: 200,
      body: [],
    }).as("emptyStructuredTemplates");
    interceptAuthoredCatalog();
    mountSelection();
    cy.wait("@authoredStyles");

    cy.get('[data-testid="authored-style-select-default"]').click();
    cy.get("@selectTemplate").should("have.been.calledWith", AUTHORED_TEMPLATE_ID);
  });
});
