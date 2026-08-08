import React from "react";
import { mount } from "cypress/react";
import { AppRouterContext } from "next/dist/shared/lib/app-router-context.shared-runtime";

import "@/app/globals.css";
import { StructuredTemplateLayoutEditor } from "./StructuredTemplateLayoutEditor";

const templateId = "brand-template";

function createRouter() {
  return {
    push: cy.stub(),
    back: cy.stub(),
    forward: cy.stub(),
    refresh: cy.stub(),
    replace: cy.stub(),
    prefetch: cy.stub().resolves(),
  };
}

describe("<StructuredTemplateLayoutEditor />", () => {
  it("keeps an AI-generated layout as a draft until the user saves it", () => {
    let saveRequests = 0;
    const initialLayouts = [
      {
        id: "title-layout",
        description: "Title layout",
        components: [],
      },
    ];
    const generatedLayout = {
      id: "comparison-layout",
      description: "Generated comparison layout",
      components: [],
    };

    cy.viewport(1440, 900);
    cy.intercept(
      "GET",
      `**/api/v1/ppt/structured-templates/${templateId}`,
      {
        statusCode: 200,
        body: {
          id: templateId,
          name: "Brand template",
          layouts: initialLayouts,
          revision: 4,
          is_default: false,
        },
      },
    ).as("loadTemplate");
    cy.intercept(
      "POST",
      "**/api/v1/ppt/template/layouts/generate",
      (request) => {
        expect(request.body).to.deep.equal({
          template_id: templateId,
          prompt: "Create a product comparison",
        });
        request.reply({
          statusCode: 200,
          body: { layout: generatedLayout, response: "Created" },
        });
      },
    ).as("generateLayout");
    cy.intercept(
      "PATCH",
      `**/api/v1/ppt/structured-templates/${templateId}`,
      (request) => {
        saveRequests += 1;
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

    mount(
      <AppRouterContext.Provider value={createRouter() as never}>
        <StructuredTemplateLayoutEditor templateId={templateId} />
      </AppRouterContext.Provider>,
    );

    cy.wait("@loadTemplate");
    cy.contains("button", "AI 레이아웃 생성").click();
    cy.get('input[placeholder^="Start with your idea"]', { timeout: 5000 })
      .type("Create a product comparison")
      .closest("form")
      .find('button[type="submit"]')
      .click();
    cy.wait("@generateLayout");

    // Wait for state updates - look for the prompt overlay to be replaced with generated content
    // The state should update with the new layout
    cy.get('form[role="search"]', { timeout: 8000 }).should("not.exist");

    // Verify the draft layout was added (prompt input should be gone)
    cy.get('input[placeholder^="Start with your idea"]', { timeout: 8000 })
      .should("not.exist");

    // Verify no additional API calls were made to save yet
    cy.then(() => expect(saveRequests).to.equal(0));

    // Verify unsaved changes indicator appears (this means state was updated)
    cy.contains("저장하지 않은 변경 사항", { timeout: 5000 }).should("be.visible");

    // Verify save button is enabled
    cy.contains("button", "저장", { timeout: 5000 }).should("not.be.disabled").click();

    // Verify save request was made with correct data
    cy.wait("@saveTemplate")
      .its("request.body")
      .then((body) => {
        expect(body.expected_revision).to.equal(4);
        expect(body.layouts).to.have.length(2);
        expect(body.layouts[1]).to.deep.equal(generatedLayout);
      });

    // Verify save completed
    cy.contains("Revision 5", { timeout: 5000 }).should("be.visible");
    cy.contains("저장하지 않은 변경 사항", { timeout: 5000 }).should("not.exist");
  });
});
