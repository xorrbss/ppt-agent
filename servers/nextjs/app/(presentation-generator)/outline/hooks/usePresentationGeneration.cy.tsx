import React from "react";
import { mount } from "cypress/react";
import { Provider } from "react-redux";
import { AppRouterContext } from "next/dist/shared/lib/app-router-context.shared-runtime";

import { makeTemplateV2SelectionId } from "@/app/hooks/useStructuredTemplates";
import { notify } from "@/components/ui/sonner";
import { setPptGenUploadState } from "@/store/slices/presentationGenUpload";
import { store } from "@/store/store";

import { AUTHORED_TEMPLATE_ID } from "../components/TemplateSelection";
import { ApiError } from "../../services/api/api-error-handler";
import { PresentationGenerationApi } from "../../services/api/presentation-generation";
import { usePresentationGeneration } from "./usePresentationGeneration";

const router = {
  push: () => undefined,
  back: () => undefined,
  forward: () => undefined,
  refresh: () => undefined,
  replace: () => undefined,
  prefetch: () => Promise.resolve(),
};

function GenerationHarness({
  selectedTemplate = AUTHORED_TEMPLATE_ID,
}: {
  selectedTemplate?: string;
}) {
  const { handleSubmit } = usePresentationGeneration(
    null,
    [{ content: "# Selected outline" }],
    selectedTemplate,
    () => undefined
  );

  return (
    <button type="button" onClick={() => void handleSubmit()}>
      Generate
    </button>
  );
}

function mountHarness(selectedTemplate?: string) {
  return mount(
    <AppRouterContext.Provider value={router as any}>
      <Provider store={store}>
        <GenerationHarness selectedTemplate={selectedTemplate} />
      </Provider>
    </AppRouterContext.Provider>
  );
}

describe("usePresentationGeneration async generation", () => {
  beforeEach(() => {
    store.dispatch(
      setPptGenUploadState({
        authoredStyle: "strategic-navy",
        authoredVisionQa: true,
        config: null,
      })
    );
  });

  it("passes the Redux-authored style and vision-QA setting to generation", () => {
    cy.stub(PresentationGenerationApi, "generateAuthoredAsync")
      .resolves({} as any)
      .as("generateAuthored");

    mountHarness();

    cy.contains("button", "Generate").click();
    cy.get("@generateAuthored").should("have.been.calledOnceWith", {
      content: "# Selected outline",
      slides_markdown: ["# Selected outline"],
      language: null,
      vision_qa: true,
      authored_style: "strategic-navy",
    });
  });

  it("shows revision conflicts with the backend request ID", () => {
    cy.stub(notify, "error").as("notifyError");
    cy.stub(PresentationGenerationApi, "generateTemplateV2Async").rejects(
      new ApiError("template_v2_revision_conflict", {
        status: 409,
        code: "template_v2_revision_conflict",
        requestId: "revision-request-7",
      })
    );

    mountHarness(makeTemplateV2SelectionId("brand-template", 4));
    cy.contains("button", "Generate").click();

    cy.get("@notifyError").should(
      "have.been.calledWithMatch",
      "생성 오류",
      Cypress.sinon.match("Request ID: revision-request-7")
    );
  });

  it("shows async schema failures with the original generation request ID", () => {
    cy.clock();
    cy.stub(notify, "error").as("notifyError");
    cy.stub(PresentationGenerationApi, "generateTemplateV2Async").resolves({
      id: "template-v2-task",
      request_id: "start-request-8",
    } as any);
    cy.stub(PresentationGenerationApi, "getGenerationStatus")
      .resolves({
        status: "error",
        message: "Presentation generation failed",
        request_id: "status-request-8",
        error: {
          status_code: 422,
          code: "template_v2_generation_invalid",
          detail: "template_v2_generation_invalid",
        },
      } as any)
      .as("getStatus");

    mountHarness(makeTemplateV2SelectionId("brand-template", 4));
    cy.contains("button", "Generate").click();
    cy.tick(4000);

    cy.get("@getStatus").should("have.been.calledOnce");
    cy.get("@notifyError").should(
      "have.been.calledWithMatch",
      "생성 오류",
      Cypress.sinon.match("Request ID: status-request-8")
    );
  });

  it("includes the start request ID when client polling times out", () => {
    cy.clock();
    cy.stub(notify, "error").as("notifyError");
    cy.stub(PresentationGenerationApi, "generateTemplateV2Async")
      .resolves({
        id: "template-v2-task",
        request_id: "timeout-request-9",
      } as any)
      .as("startGeneration");
    cy.stub(PresentationGenerationApi, "getGenerationStatus")
      .resolves({ status: "pending" } as any)
      .as("getStatus");

    mountHarness(makeTemplateV2SelectionId("brand-template", 4));
    cy.contains("button", "Generate").click();
    cy.get("@startGeneration").should("have.been.calledOnce");
    cy.tick(20 * 60 * 1000 + 1);

    cy.get("@getStatus").should("have.been.calledOnce");
    cy.get("@notifyError").should(
      "have.been.calledWithMatch",
      "생성 오류",
      Cypress.sinon.match("Request ID: timeout-request-9")
    );
  });

  it("stops polling and suppresses errors after unmount", () => {
    cy.clock();
    cy.stub(notify, "error").as("notifyError");
    cy.stub(PresentationGenerationApi, "generateTemplateV2Async")
      .resolves({
        id: "template-v2-task",
        request_id: "unmount-request-10",
      } as any)
      .as("startGeneration");
    cy.stub(PresentationGenerationApi, "getGenerationStatus").as("getStatus");

    mountHarness(makeTemplateV2SelectionId("brand-template", 4)).then(
      ({ rerender }) => {
        cy.contains("button", "Generate").click();
        cy.get("@startGeneration")
          .should("have.been.calledOnce")
          .then(() => {
            rerender(<></>);
            cy.tick(4000);
          });
      }
    );

    cy.get("@getStatus").should("not.have.been.called");
    cy.get("@notifyError").should("not.have.been.called");
  });
});
