import React from "react";
import { mount } from "cypress/react";
import { NextIntlClientProvider } from "next-intl";
import { Provider } from "react-redux";
import { AppRouterContext } from "next/dist/shared/lib/app-router-context.shared-runtime";

import { makeTemplateV2SelectionId } from "@/app/hooks/useStructuredTemplates";
import { notify } from "@/components/ui/sonner";
import koMessages from "@/i18n/messages/ko.json";
import { setPptGenUploadState } from "@/store/slices/presentationGenUpload";
import { store } from "@/store/store";

import { AUTHORED_TEMPLATE_ID } from "../components/TemplateSelection";
import { ApiError } from "../../services/api/api-error-handler";
import { PresentationGenerationApi } from "../../services/api/presentation-generation";
import { usePresentationGeneration } from "./usePresentationGeneration";

const DURABLE_TASK_STORAGE_KEY = "presenton:durable-generation-tasks:v1";

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
  presentationId = "outline-shell-test",
}: {
  selectedTemplate?: string;
  presentationId?: string | null;
}) {
  const { handleSubmit, loadingState } = usePresentationGeneration(
    presentationId,
    [{ content: "# Selected outline" }],
    selectedTemplate,
    () => undefined
  );

  return (
    <>
      <button type="button" onClick={() => void handleSubmit()}>
        Generate
      </button>
      <output data-cy="generation-loading">
        {loadingState.isLoading ? "loading" : "idle"}
      </output>
    </>
  );
}

function mountHarness(
  selectedTemplate?: string,
  presentationId?: string | null,
  generationKey = "initial"
) {
  return mount(
    <NextIntlClientProvider locale="ko" messages={koMessages} timeZone="UTC">
      <AppRouterContext.Provider value={router as any}>
        <Provider store={store}>
          <GenerationHarness
            key={generationKey}
            selectedTemplate={selectedTemplate}
            presentationId={presentationId}
          />
        </Provider>
      </AppRouterContext.Provider>
    </NextIntlClientProvider>
  );
}

describe("usePresentationGeneration async generation", () => {
  beforeEach(() => {
    cy.stub(router, "replace").as("replace");
    cy.window().then((window) => window.localStorage.clear());
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
      presentation_id: "outline-shell-test",
      content: "# Selected outline",
      slides_markdown: ["# Selected outline"],
      language: null,
      vision_qa: true,
      authored_style: "strategic-navy",
      idempotency_key: Cypress.sinon.match.string,
    });
  });

  it("opens the staging workspace immediately after chat-first authored admission", () => {
    const sourcePresentationId = "chat-first-authored-shell";
    cy.stub(PresentationGenerationApi, "generateAuthoredAsync").resolves({
      id: "workspace-operation-authored",
      status: "queued",
    } as any);
    cy.stub(PresentationGenerationApi, "getGenerationStatus").as("getStatus");

    mountHarness(
      AUTHORED_TEMPLATE_ID,
      sourcePresentationId,
      "chat-first-authored");
    cy.contains("button", "Generate").click();

    cy.get("@replace").should(
      "have.been.calledOnceWith",
      `/presentation?id=${sourcePresentationId}`
    );
    cy.get("@getStatus").should("not.have.been.called");
    cy.get('[data-cy="generation-loading"]').should("have.text", "idle");
  });

  it("opens the staging workspace immediately after chat-first template-v2 admission", () => {
    const sourcePresentationId = "chat-first-template-v2-shell";
    cy.stub(PresentationGenerationApi, "generateTemplateV2Async").resolves({
      id: "workspace-operation-template-v2",
      status: "queued",
    } as any);
    cy.stub(PresentationGenerationApi, "getGenerationStatus").as("getStatus");

    mountHarness(
      makeTemplateV2SelectionId("brand-template", 4),
      sourcePresentationId,
      "chat-first-template-v2",
      true
    );
    cy.contains("button", "Generate").click();

    cy.get("@replace").should(
      "have.been.calledOnceWith",
      `/presentation?id=${sourcePresentationId}`
    );
    cy.get("@getStatus").should("not.have.been.called");
    cy.get('[data-cy="generation-loading"]').should("have.text", "idle");
  });

  it("uses async generation and canonical custom template ids in chat-first mode", () => {
    const sourcePresentationId = "chat-first-standard-shell";
    cy.stub(PresentationGenerationApi, "generateStandardAsync")
      .resolves({ id: "workspace-operation-standard", status: "queued" } as any)
      .as("generateStandard");
    cy.stub(PresentationGenerationApi, "presentationPrepare").as("prepare");

    mountHarness(
      "c5f6f44a-9d2e-4eef-a44b-8f6389b43dac",
      sourcePresentationId,
      "chat-first-standard");
    cy.contains("button", "Generate").click();

    cy.get("@generateStandard").should("have.been.calledOnceWith", {
      presentation_id: sourcePresentationId,
      content: "# Selected outline",
      slides_markdown: ["# Selected outline"],
      language: null,
      template: "custom-c5f6f44a-9d2e-4eef-a44b-8f6389b43dac",
      idempotency_key: Cypress.sinon.match.string,
    });
    cy.get("@prepare").should("not.have.been.called");
    cy.get("@replace").should(
      "have.been.calledOnceWith",
      `/presentation?id=${sourcePresentationId}`
    );
    cy.get('[data-cy="generation-loading"]').should("have.text", "idle");
  });

  it("opens the chat-first workspace after authored admission fails", () => {
    const sourcePresentationId = "chat-first-authored-admission-error";
    cy.stub(PresentationGenerationApi, "generateAuthoredAsync").rejects(
      new ApiError("Generation admission timed out", {
        status: 503,
        code: "generation_admission_timeout",
        workspaceProjected: true,
      })
    );
    cy.stub(notify, "error").as("notifyError");

    mountHarness(
      AUTHORED_TEMPLATE_ID,
      sourcePresentationId,
      "chat-first-authored-admission-error");
    cy.contains("button", "Generate").click();

    cy.get("@replace").should(
      "have.been.calledOnceWith",
      `/presentation?id=${sourcePresentationId}`
    );
    cy.get("@notifyError").should("not.have.been.called");
  });

  it("opens the chat-first workspace after template-v2 admission fails", () => {
    const sourcePresentationId = "chat-first-template-v2-admission-error";
    cy.stub(PresentationGenerationApi, "generateTemplateV2Async").rejects(
      new ApiError("Generation admission was rejected", {
        status: 422,
        code: "template_v2_generation_invalid",
        workspaceProjected: true,
      })
    );
    cy.stub(notify, "error").as("notifyError");

    mountHarness(
      makeTemplateV2SelectionId("brand-template", 4),
      sourcePresentationId,
      "chat-first-template-v2-admission-error",
      true
    );
    cy.contains("button", "Generate").click();

    cy.get("@replace").should(
      "have.been.calledOnceWith",
      `/presentation?id=${sourcePresentationId}`
    );
    cy.get("@notifyError").should("not.have.been.called");
  });

  it("stays put after an ambiguous standard admission and retries with the same key", () => {
    const sourcePresentationId = "chat-first-standard-admission-error";
    const generateStandard = cy
      .stub(PresentationGenerationApi, "generateStandardAsync")
      .as("generateStandard");
    generateStandard.onFirstCall().rejects(new TypeError("Failed to fetch"));
    generateStandard.onSecondCall().resolves({
      id: "workspace-operation-standard-replayed",
      status: "queued",
    } as any);
    cy.stub(notify, "error").as("notifyError");

    mountHarness(
      "c5f6f44a-9d2e-4eef-a44b-8f6389b43dac",
      sourcePresentationId,
      "chat-first-standard-admission-error");
    cy.contains("button", "Generate").click();

    cy.get("@replace").should("not.have.been.called");
    cy.get("@notifyError")
      .should("have.been.calledOnce")
      .then((notifyError: any) => {
        const action = notifyError.firstCall.args[2]?.action;
        expect(action?.label).to.equal("다시 시도");
        action?.onClick();
      });

    cy.get("@generateStandard")
      .should("have.been.calledTwice")
      .then((generate: any) => {
        expect(generate.firstCall.args[0].idempotency_key).to.equal(
          generate.secondCall.args[0].idempotency_key
        );
      });
    cy.get("@replace").should(
      "have.been.calledOnceWith",
      `/presentation?id=${sourcePresentationId}`
    );
  });

  it("keeps the inline admission error when chat-first bootstrap is missing", () => {
    cy.stub(PresentationGenerationApi, "generateStandardAsync").rejects(
      new ApiError("chat_first_bootstrap_operation_not_found", {
        status: 409,
        detail: "chat_first_bootstrap_operation_not_found",
      })
    );
    cy.stub(notify, "error").as("notifyError");

    mountHarness(
      "c5f6f44a-9d2e-4eef-a44b-8f6389b43dac",
      "chat-first-missing-bootstrap",
      "chat-first-missing-bootstrap");
    cy.contains("button", "Generate").click();

    cy.get("@replace").should("not.have.been.called");
    cy.get("@notifyError").should("have.been.calledOnce");
  });

  it("clears an unmarked definitive 409 so the next click uses a fresh key", () => {
    const generateAuthored = cy
      .stub(PresentationGenerationApi, "generateAuthoredAsync")
      .as("generateAuthored");
    generateAuthored.onFirstCall().rejects(
      new ApiError("idempotency_key_payload_conflict", {
        status: 409,
        code: "idempotency_key_payload_conflict",
      })
    );
    generateAuthored.onSecondCall().resolves({
      id: "workspace-operation-authored-fresh-key",
      status: "queued",
    } as any);
    cy.stub(notify, "error").as("notifyError");

    mountHarness(
      AUTHORED_TEMPLATE_ID,
      "chat-first-idempotency-conflict",
      "chat-first-idempotency-conflict");
    cy.contains("button", "Generate").click();

    cy.get("@replace").should("not.have.been.called");
    cy.get("@notifyError").should("have.been.calledOnce");
    cy.contains("button", "Generate").click();

    cy.get("@generateAuthored")
      .should("have.been.calledTwice")
      .then((generate: any) => {
        expect(generate.firstCall.args[0].idempotency_key).not.to.equal(
          generate.secondCall.args[0].idempotency_key
        );
      });
    cy.get("@replace").should(
      "have.been.calledOnceWith",
      "/presentation?id=chat-first-idempotency-conflict"
    );
  });

  it("does not open a workspace for an unmarked incomplete idempotency record", () => {
    cy.stub(PresentationGenerationApi, "generateTemplateV2Async").rejects(
      new ApiError("idempotency_record_incomplete", {
        status: 409,
        code: "idempotency_record_incomplete",
      })
    );
    cy.stub(notify, "error").as("notifyError");

    mountHarness(
      makeTemplateV2SelectionId("brand-template", 4),
      "chat-first-incomplete-idempotency",
      "chat-first-incomplete-idempotency",
      true
    );
    cy.contains("button", "Generate").click();

    cy.get("@replace").should("not.have.been.called");
    cy.get("@notifyError").should("have.been.calledOnce");
  });

  it("does not open a workspace for an unmarked 503", () => {
    cy.stub(PresentationGenerationApi, "generateAuthoredAsync").rejects(
      new ApiError("Generation admission unavailable", {
        status: 503,
        code: "generation_admission_unavailable",
      })
    );
    cy.stub(notify, "error").as("notifyError");

    mountHarness(
      AUTHORED_TEMPLATE_ID,
      "chat-first-unmarked-503",
      "chat-first-unmarked-503");
    cy.contains("button", "Generate").click();

    cy.get("@replace").should("not.have.been.called");
    cy.get("@notifyError").should("have.been.calledOnce");
  });

  it("uses one stable idempotency key when an authored generation is retried", () => {
    cy.stub(PresentationGenerationApi, "generateAuthoredAsync")
      .rejects(
        new ApiError("Presentation generation failed", {
          status: 503,
          code: "upstream_overloaded",
        })
      )
      .as("generateAuthored");
    cy.stub(notify, "error").as("notifyError");

    mountHarness(undefined, "outline-shell-idempotency");
    cy.contains("button", "Generate").click();
    cy.contains("button", "Generate").click();

    cy.get("@generateAuthored").should("have.been.calledTwice").then((stub) => {
      const first = (stub as any).getCall(0).args[0].idempotency_key;
      const second = (stub as any).getCall(1).args[0].idempotency_key;
      expect(first).to.be.a("string").and.not.be.empty;
      expect(second).to.equal(first);
    });
  });

  it("opens the stable source workspace without polling a persisted durable task", () => {
    const sourcePresentationId = "chat-first-persisted-shell";
    const idempotencyKey = "ppt-generation-persisted-chat-first";
    cy.window().then((window) => {
      window.localStorage.setItem(
        DURABLE_TASK_STORAGE_KEY,
        JSON.stringify({
          [idempotencyKey]: {
            version: 1,
            idempotencyKey,
            taskId: "persisted-chat-first-task",
            mode: "authored",
            sourcePresentationId,
            status: "running",
            createdAt: Date.now(),
            deadlineAt: Date.now() + 30 * 60 * 1000,
            progressDurationSeconds: 120,
          },
        })
      );
    });
    cy.stub(PresentationGenerationApi, "generateAuthoredAsync").as(
      "generateAuthored"
    );
    cy.stub(PresentationGenerationApi, "getGenerationStatus").as("getStatus");

    mountHarness(AUTHORED_TEMPLATE_ID, sourcePresentationId);
    cy.contains("button", "Generate").click();

    cy.get("@replace").should(
      "have.been.calledOnceWith",
      `/presentation?id=${sourcePresentationId}`
    );
    cy.get("@generateAuthored").should("not.have.been.called");
    cy.get("@getStatus").should("not.have.been.called");
    cy.window().then((window) => {
      expect(window.localStorage.getItem(DURABLE_TASK_STORAGE_KEY)).to.equal(
        "{}"
      );
    });
  });

  it("shows a retry action without a dev error overlay for transient authored failures", () => {
    cy.stub(console, "error").as("consoleError");
    cy.stub(console, "warn");
    cy.stub(notify, "error").as("notifyError");
    cy.stub(PresentationGenerationApi, "generateAuthoredAsync").rejects(
      new ApiError("Presentation generation failed", {
        status: 503,
        code: "upstream_overloaded",
        requestId: "authored-request-6",
      })
    );

    mountHarness();
    cy.contains("button", "Generate").click();

    cy.get("@notifyError").should(
      "have.been.calledWithMatch",
      "발표자료 생성이 일시 중단되었습니다",
      "생성을 시작하기 전에 연결이 끊겼습니다. 안전하게 다시 시도할 수 있습니다.",
      Cypress.sinon.match({
        action: Cypress.sinon.match({ label: "다시 시도" }),
      })
    );
    // Guard the actual regression (the expected failure being dumped through
    // console.error, which Next.js dev renders as a blocking overlay) without
    // failing on unrelated dev-mode warnings React logs via console.error.
    cy.get("@consoleError").should((stub) => {
      const calls = (stub as unknown as Cypress.Agent<sinon.SinonStub>)
        .getCalls()
        .map((call) => call.args.map(String).join(" "));
      const offending = calls.filter((args) =>
        args.includes("Presentation generation")
      );
      expect(offending, `console.error calls: ${JSON.stringify(calls)}`).to.be
        .empty;
    });
  });

  it("shows the localized generation error for revision conflicts", () => {
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
      "발표자료 생성을 시작하는 중 문제가 발생했습니다."
    );
  });

});
