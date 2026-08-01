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
        idempotency_key: "authored-generation-key",
      })
    );

    cy.wait("@startAuthoredGeneration").then(({ request }) => {
      expect(request.headers).to.include({
        "idempotency-key": "authored-generation-key",
      });
      expect(request.body).to.deep.equal({
        content: "Outline title",
        slides_markdown: ["# Outline title"],
        language: "Korean",
        template: "authored",
        export_as: "pptx",
        vision_qa: true,
        authored_style: "strategic-navy",
      });
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

describe("PresentationGenerationApi.generateTemplateV2Async", () => {
  it("sends an explicit strategy and immutable template provenance", () => {
    cy.intercept("POST", "**/api/v1/ppt/presentation/generate/async", {
      statusCode: 200,
      body: { id: "template-v2-task" },
    }).as("startTemplateV2Generation");

    cy.then(() =>
      PresentationGenerationApi.generateTemplateV2Async({
        content: "Outline title",
        slides_markdown: ["# Outline title"],
        language: "Korean",
        template_v2_id: "brand-template",
        template_v2_revision: 4,
        idempotency_key: "template-v2-generation-key",
      })
    );

    cy.wait("@startTemplateV2Generation").then(({ request }) => {
      expect(request.headers).to.include({
        "idempotency-key": "template-v2-generation-key",
      });
      expect(request.body).to.deep.equal({
        content: "Outline title",
        slides_markdown: ["# Outline title"],
        language: "Korean",
        strategy: "template_v2",
        template_v2_id: "brand-template",
        template_v2_revision: 4,
        export_as: "pptx",
      });
    });
  });
});

describe("PresentationGenerationApi.getGenerationStatus", () => {
  it("returns authored checkpoint progress and sanitized slide failure fields", () => {
    cy.intercept("GET", "**/api/v1/ppt/presentation/status/task-47", {
      statusCode: 200,
      body: {
        status: "error",
        progress: {
          completed_slides: 46,
          total_slides: 90,
          current_slide: 47,
          current_stage: "render",
          finalizing: false,
        },
        failure: {
          code: "slide_render_failed",
          message: "Browser render failed",
          stage: "render",
          slide_number: 47,
          retryable: true,
        },
      },
    }).as("getGenerationStatus");

    cy.then(() => PresentationGenerationApi.getGenerationStatus("task-47"))
      .should("deep.include", { status: "error" })
      .and((status) => {
        expect(status.progress).to.include({
          completed_slides: 46,
          total_slides: 90,
          current_slide: 47,
          current_stage: "render",
        });
        expect(status.failure).to.include({
          stage: "render",
          slide_number: 47,
          retryable: true,
        });
      });
  });

  it("accepts the completed current stage returned after publish", () => {
    cy.intercept("GET", "**/api/v1/ppt/presentation/status/task-completed", {
      statusCode: 200,
      body: {
        status: "completed",
        progress: {
          completed_slides: 90,
          total_slides: 90,
          percent: 100,
          current_stage: "completed",
          finalizing: false,
        },
        data: { presentation_id: "published-presentation" },
      },
    });

    cy.then(() =>
      PresentationGenerationApi.getGenerationStatus("task-completed")
    ).then((status) => {
      expect(status.status).to.equal("completed");
      expect(status.progress?.current_stage).to.equal("completed");
      expect(status.progress?.percent).to.equal(100);
    });
  });
});
