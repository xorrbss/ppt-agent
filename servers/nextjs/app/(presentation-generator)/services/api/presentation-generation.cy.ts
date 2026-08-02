import { PresentationGenerationApi } from "./presentation-generation";
import { ApiError } from "./api-error-handler";
import ThemeApi from "./theme";
import { resolveDeckTheme } from "../../upload/components/applyDeckTheme";

describe("resolveDeckTheme", () => {
  it("allowlists a custom theme before create admission", () => {
    cy.stub(ThemeApi, "getThemes").resolves([
      {
        id: "custom-theme",
        name: "Custom Theme",
        description: "Approved palette",
        user: "local",
        logo: "logo-id",
        logo_url: "/assets/logo.png",
        company_name: "Example Co",
        data: { colors: { primary: "#123456" } },
        unexpected_secret: "must-not-be-forwarded",
      } as never,
    ]);

    cy.then(() => resolveDeckTheme("custom-theme")).should("deep.equal", {
      id: "custom-theme",
      name: "Custom Theme",
      description: "Approved palette",
      user: "local",
      logo: "logo-id",
      logo_url: "/assets/logo.png",
      company_name: "Example Co",
      data: { colors: { primary: "#123456" } },
    });
  });
});

describe("PresentationGenerationApi.createPresentation", () => {
  it("admits the resolved theme in the idempotent create request", () => {
    const theme = {
      id: "customer-brand",
      name: "Customer Brand",
      description: "Approved palette",
      user: "local",
      logo: null,
      logo_url: "/assets/logo.png",
      company_name: "Example Co",
      data: { colors: { primary: "#123456" } },
    };
    cy.intercept("POST", "**/api/v1/ppt/presentation/create", {
      statusCode: 200,
      body: { id: "presentation-1" },
    }).as("createPresentation");

    cy.then(() =>
      PresentationGenerationApi.createPresentation({
        presentation_id: "presentation-1",
        idempotency_key: "presentation-create:presentation-1",
        content: "Build a brand deck",
        n_slides: 8,
        file_paths: [],
        language: "Korean",
        theme,
      })
    );

    cy.wait("@createPresentation").then(({ request }) => {
      expect(request.headers).to.include({
        "idempotency-key": "presentation-create:presentation-1",
      });
      expect(request.body).to.deep.equal({
        presentation_id: "presentation-1",
        content: "Build a brand deck",
        n_slides: 8,
        file_paths: [],
        language: "Korean",
        theme,
      });
    });
  });
});

describe("PresentationGenerationApi.generateAuthoredAsync", () => {
  it("sends the selected authored style without changing other authored fields", () => {
    cy.intercept("POST", "**/api/v1/ppt/presentation/generate/async", {
      statusCode: 200,
      body: { id: "authored-task" },
    }).as("startAuthoredGeneration");

    cy.then(() =>
      PresentationGenerationApi.generateAuthoredAsync({
        presentation_id: "authored-shell",
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
        presentation_id: "authored-shell",
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
        presentation_id: "default-authored-shell",
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
        presentation_id: "template-v2-shell",
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
        presentation_id: "template-v2-shell",
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

describe("PresentationGenerationApi.generateStandardAsync", () => {
  it("starts a durable standard generation with the reviewed settings", () => {
    cy.intercept("POST", "**/api/v1/ppt/presentation/generate/async", {
      statusCode: 202,
      headers: { "x-request-id": "standard-request" },
      body: { id: "standard-operation", status: "queued" },
    }).as("startStandardGeneration");

    cy.then(() =>
      PresentationGenerationApi.generateStandardAsync({
        presentation_id: "standard-shell",
        content: "Outline title",
        slides_markdown: ["# Outline title"],
        language: "Korean",
        tone: "professional",
        verbosity: "concise",
        instructions: "Use the brand voice",
        include_table_of_contents: true,
        include_title_slide: false,
        web_search: true,
        template: "custom-brand-template",
        idempotency_key: "standard-generation-key",
      })
    ).should("deep.include", {
      id: "standard-operation",
      request_id: "standard-request",
    });

    cy.wait("@startStandardGeneration").then(({ request }) => {
      expect(request.headers).to.include({
        "idempotency-key": "standard-generation-key",
      });
      expect(request.body).to.deep.equal({
        presentation_id: "standard-shell",
        content: "Outline title",
        slides_markdown: ["# Outline title"],
        language: "Korean",
        tone: "professional",
        verbosity: "concise",
        instructions: "Use the brand voice",
        include_table_of_contents: true,
        include_title_slide: false,
        web_search: true,
        template: "custom-brand-template",
        strategy: "legacy",
        export_as: "pptx",
      });
    });
  });

  it("preserves the projected-workspace response marker on admission failure", () => {
    cy.intercept("POST", "**/api/v1/ppt/presentation/generate/async", {
      statusCode: 503,
      headers: {
        "x-chat-first-workspace-projected": "true",
      },
      body: { detail: "generation_admission_unavailable" },
    });

    cy.then(async () => {
      let caught: unknown;
      try {
        await PresentationGenerationApi.generateStandardAsync({
          presentation_id: "projected-standard-shell",
          content: "Outline title",
          slides_markdown: ["# Outline title"],
          template: "adaptive",
          idempotency_key: "projected-standard-generation-key",
        });
      } catch (error) {
        caught = error;
      }

      expect(caught).to.be.instanceOf(ApiError);
      expect(caught).to.include({
        status: 503,
        workspaceProjected: true,
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
