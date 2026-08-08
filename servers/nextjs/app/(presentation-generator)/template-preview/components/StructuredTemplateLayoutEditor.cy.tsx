import React from "react";
import { AppRouterContext } from "next/dist/shared/lib/app-router-context.shared-runtime";

import "@/app/globals.css";
import { createMockRouter, ApiMockManager, testViewports } from "@/cypress/support/testHelpers";
import { StructuredTemplateLayoutEditor } from "./StructuredTemplateLayoutEditor";

const templateId = "brand-template";

describe("<StructuredTemplateLayoutEditor />", () => {
  let apiMocks: ApiMockManager;

  beforeEach(() => {
    apiMocks = new ApiMockManager();
    cy.viewport(testViewports.desktop.width, testViewports.desktop.height);
  });

  afterEach(() => {
    apiMocks.cleanup();
  });

  it("loads and displays the structured template", () => {
    const initialLayouts = [
      {
        id: "title-layout",
        description: "Title layout",
        components: [],
      },
    ];

    apiMocks.setupGetIntercept(
      `**/api/v1/ppt/structured-templates/${templateId}`,
      {
        id: templateId,
        name: "Brand template",
        layouts: initialLayouts,
        revision: 4,
        is_default: false,
      },
      "loadTemplate"
    );

    cy.mount(
      <AppRouterContext.Provider value={createMockRouter() as never}>
        <StructuredTemplateLayoutEditor templateId={templateId} />
      </AppRouterContext.Provider>,
    );

    cy.wait("@loadTemplate");
    cy.contains("Brand template").should("be.visible");
    cy.contains("Revision 4").should("be.visible");
    cy.contains("button", "AI 레이아웃 생성").should("be.visible");
  });

  it("keeps an AI-generated layout as a draft until the user saves it", () => {
    const initialLayouts = [
      {
        id: "title-layout",
        description: "Title layout",
        components: [],
      },
    ];
    const generatedLayout = {
      id: "ai_layout_2",
      description: "Generated comparison layout",
      components: [],
    };

    apiMocks.setupGetIntercept(
      `**/api/v1/ppt/structured-templates/${templateId}`,
      {
        id: templateId,
        name: "Brand template",
        layouts: initialLayouts,
        revision: 4,
        is_default: false,
      },
      "loadTemplate"
    );

    apiMocks.setupPostIntercept(
      "**/api/v1/ppt/template/layouts/generate",
      () => {
        return { layout: generatedLayout, response: "Created" };
      },
      "generateLayout"
    );

    cy.intercept(
      "PATCH",
      `**/api/v1/ppt/structured-templates/${templateId}`,
      (request) => {
        request.reply({
          statusCode: 200,
          body: {
            id: templateId,
            name: "Brand template",
            layouts: request.body.layouts,
            revision: 5,
            is_default: false,
          },
        });
      },
    ).as("saveTemplate");

    cy.mount(
      <AppRouterContext.Provider value={createMockRouter() as never}>
        <StructuredTemplateLayoutEditor templateId={templateId} />
      </AppRouterContext.Provider>,
    );

    cy.wait("@loadTemplate");
    cy.contains("button", "AI 레이아웃 생성").click();

    cy.get('input[placeholder^="Start with your idea"]', { timeout: 5000 }).type(
      "Create a product comparison"
    );
    cy.get('form button[type="submit"]', { timeout: 5000 }).click();

    cy.wait("@generateLayout");

    // Wait for state changes - unsaved changes should appear
    cy.contains("저장하지 않은 변경 사항", { timeout: 8000 }).should("be.visible");

    // Verify save button is available
    cy.contains("button", "저장").should("not.be.disabled").click();

    // Verify the save request includes both layouts
    cy.wait("@saveTemplate")
      .its("request.body.layouts")
      .should("have.length", 2);

    cy.contains("Revision 5").should("be.visible");
  });
});
