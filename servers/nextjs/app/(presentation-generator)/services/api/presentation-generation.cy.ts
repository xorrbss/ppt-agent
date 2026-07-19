import { PresentationGenerationApi } from "./presentation-generation";

describe("PresentationGenerationApi.generateAuthoredAsync", () => {
  it("sends the selected authored style without changing other authored fields", () => {
    cy.intercept("POST", "**/api/v1/ppt/presentation/generate/async", {
      statusCode: 200,
      body: { id: "authored-task" },
    }).as("startAuthoredGeneration");

    cy.then(() =>
      PresentationGenerationApi.generateAuthoredAsync({
        content: "Outline title",
        slides_markdown: ["# Outline title"],
        language: "Korean",
        vision_qa: true,
        authored_style: "strategic-navy",
      })
    );

    cy.wait("@startAuthoredGeneration")
      .its("request.body")
      .should("deep.equal", {
        content: "Outline title",
        slides_markdown: ["# Outline title"],
        language: "Korean",
        template: "authored",
        export_as: "pptx",
        vision_qa: true,
        authored_style: "strategic-navy",
      });
  });

  it("uses the default authored style when a caller omits it", () => {
    cy.intercept("POST", "**/api/v1/ppt/presentation/generate/async", {
      statusCode: 200,
      body: { id: "authored-task" },
    }).as("startDefaultAuthoredGeneration");

    cy.then(() =>
      PresentationGenerationApi.generateAuthoredAsync({
        content: "Outline title",
        slides_markdown: ["# Outline title"],
      })
    );

    cy.wait("@startDefaultAuthoredGeneration")
      .its("request.body")
      .should("include", { authored_style: "default" });
  });
});
