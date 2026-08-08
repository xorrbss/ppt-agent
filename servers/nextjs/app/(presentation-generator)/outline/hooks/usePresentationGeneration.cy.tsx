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
import { DashboardApi } from "../../services/api/dashboard";
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
  chatFirstEnabled = false,
}: {
  selectedTemplate?: string;
  presentationId?: string | null;
  chatFirstEnabled?: boolean;
}) {
  const { handleSubmit, loadingState } = usePresentationGeneration(
    presentationId,
    [{ content: "# Selected outline" }],
    selectedTemplate,
    () => undefined,
    { chatFirstEnabled }
  );

  return (
    <>
      <button type="button" onClick={() => void handleSubmit()}>
        Generate
      </button>
      <output data-cy="generation-loading">
        {loadingState.isLoading ? "loading" : "idle"}
      </output>
      <output data-cy="generation-progress">
        {loadingState.progress
          ? `${loadingState.progress.completed_slides}/${loadingState.progress.total_slides}`
          : "none"}
      </output>
    </>
  );
}

function mountHarness(
  selectedTemplate?: string,
  presentationId?: string | null,
  generationKey = "initial",
  chatFirstEnabled = false
) {
  return mount(
    <AppRouterContext.Provider value={router as any}>
      <Provider store={store}>
        <GenerationHarness
          key={generationKey}
          selectedTemplate={selectedTemplate}
          presentationId={presentationId}
          chatFirstEnabled={chatFirstEnabled}
        />
      </Provider>
    </AppRouterContext.Provider>
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
      "chat-first-authored",
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
      "chat-first-standard",
      true
    );
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
      "chat-first-authored-admission-error",
      true
    );
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
      "chat-first-standard-admission-error",
      true
    );
    cy.contains("button", "Generate").click();

    cy.get("@replace").should("not.have.been.called");
    cy.get("@notifyError")
      .should("have.been.calledOnce")
      .then((notifyError: any) => {
        const action = notifyError.firstCall.args[2]?.action;
        expect(action?.label).to.equal("Retry");
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
      "chat-first-missing-bootstrap",
      true
    );
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
      "chat-first-idempotency-conflict",
      true
    );
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
      "chat-first-unmarked-503",
      true
    );
    cy.contains("button", "Generate").click();

    cy.get("@replace").should("not.have.been.called");
    cy.get("@notifyError").should("have.been.calledOnce");
  });

  it("opens the generated authored presentation and clears loading on completion", () => {
    const sourcePresentationId = "outline-shell-authored-completion";
    cy.stub(PresentationGenerationApi, "generateAuthoredAsync").resolves({
      id: "authored-completed-task",
      request_id: "completed-request-1",
    } as any);
    cy.stub(PresentationGenerationApi, "getGenerationStatus").resolves({
      status: "completed",
      data: { presentation_id: "finished-authored-presentation" },
    } as any);
    cy.stub(DashboardApi, "deletePresentation").resolves(undefined as any);

    mountHarness(AUTHORED_TEMPLATE_ID, sourcePresentationId);
    cy.contains("button", "Generate").click();

    cy.get("@replace").should(
      "have.been.calledOnceWith",
      "/presentation?id=finished-authored-presentation"
    );
    cy.get('[data-cy="generation-loading"]').should("have.text", "idle");
  });

  it("opens the structured-template edit path on completion", () => {
    cy.clock();
    cy.stub(PresentationGenerationApi, "generateTemplateV2Async").resolves({
      id: "template-v2-completed-task",
      request_id: "completed-request-2",
    } as any);
    cy.stub(PresentationGenerationApi, "getGenerationStatus").resolves({
      status: "completed",
      data: {
        presentation_id: "finished-template-v2-presentation",
        edit_path: "/presentation?id=finished-template-v2-presentation&mode=edit",
      },
    } as any);

    mountHarness(makeTemplateV2SelectionId("brand-template", 4));
    cy.contains("button", "Generate").click();
    cy.tick(4000);

    cy.get("@replace").should(
      "have.been.calledOnceWith",
      "/presentation?id=finished-template-v2-presentation&mode=edit"
    );
    cy.get('[data-cy="generation-loading"]').should("have.text", "idle");
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

  it("stores actual completed-slide progress while polling", () => {
    cy.clock();
    cy.stub(PresentationGenerationApi, "generateAuthoredAsync").resolves({
      id: "authored-progress-task",
      request_id: "progress-request",
    } as any);
    cy.stub(PresentationGenerationApi, "getGenerationStatus")
      .onFirstCall()
      .resolves({
        status: "running",
        progress: {
          completed_slides: 46,
          total_slides: 90,
          current_slide: 47,
          current_stage: "render",
          finalizing: false,
        },
      } as any)
      .onSecondCall()
      .resolves({
        status: "completed",
        data: { presentation_id: "progress-completed-presentation" },
      } as any);
    cy.stub(DashboardApi, "deletePresentation").resolves(undefined as any);

    mountHarness(AUTHORED_TEMPLATE_ID, "outline-shell-progress");
    cy.contains("button", "Generate").click();

    cy.get('[data-cy="generation-progress"]').should("have.text", "46/90");
    cy.window().then((window) => {
      const tasks = JSON.parse(
        window.localStorage.getItem(DURABLE_TASK_STORAGE_KEY) || "{}"
      );
      expect((Object.values(tasks)[0] as any).lastProgress).to.include({
        completed_slides: 46,
        total_slides: 90,
      });
    });
    cy.tick(4000);
    cy.get("@replace").should(
      "have.been.calledOnceWith",
      "/presentation?id=progress-completed-presentation"
    );
  });

  it("reposts a retryable failed authored task with the same key and resumes it", () => {
    const sourcePresentationId = "outline-shell-checkpoint-retry";
    cy.stub(notify, "error").as("notifyError");
    cy.stub(DashboardApi, "deletePresentation").resolves(undefined as any);
    cy.stub(PresentationGenerationApi, "generateAuthoredAsync")
      .onFirstCall()
      .resolves({ id: "failed-authored-task" } as any)
      .onSecondCall()
      .resolves({ id: "resumed-authored-task" } as any)
      .as("generateAuthored");
    cy.stub(PresentationGenerationApi, "getGenerationStatus")
      .onFirstCall()
      .resolves({
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
      } as any)
      .onSecondCall()
      .resolves({
        status: "completed",
        data: { presentation_id: "resumed-authored-presentation" },
      } as any);

    mountHarness(AUTHORED_TEMPLATE_ID, sourcePresentationId);
    cy.contains("button", "Generate").click();
    cy.get("@notifyError")
      .should("have.been.calledOnce")
      .then((stub) => {
        const message = (stub as any).getCall(0).args[1];
        expect(message).to.include("47번 슬라이드");
        expect(message).to.include("렌더링 단계");
        expect(message).to.include("슬라이드 46/90까지 저장되었습니다");
        expect(message).to.include("다시 시도하면 이어서 생성됩니다");
      });

    cy.window().then((window) => {
      const tasks = JSON.parse(
        window.localStorage.getItem(DURABLE_TASK_STORAGE_KEY) || "{}"
      );
      const task = Object.values(tasks)[0] as any;
      expect(task.status).to.equal("retryable-error");
      expect(task.lastProgress).to.include({
        completed_slides: 46,
        total_slides: 90,
      });
      expect(task.lastFailure).to.include({
        stage: "render",
        slide_number: 47,
      });
    });

    cy.contains("button", "Generate").click();
    cy.get("@generateAuthored").should("have.been.calledTwice").then((stub) => {
      const firstKey = (stub as any).getCall(0).args[0].idempotency_key;
      const secondKey = (stub as any).getCall(1).args[0].idempotency_key;
      expect(secondKey).to.equal(firstKey);
    });
    cy.get("@replace").should(
      "have.been.calledOnceWith",
      "/presentation?id=resumed-authored-presentation"
    );
    cy.window().then((window) => {
      expect(window.localStorage.getItem(DURABLE_TASK_STORAGE_KEY)).to.equal("{}");
    });
  });

  it("performs a final durable-status read at the foreground deadline before navigation", () => {
    const sourcePresentationId = "outline-shell-deadline";
    const idempotencyKey = "ppt-generation-deadline";
    cy.window().then((window) => {
      window.localStorage.setItem(
        DURABLE_TASK_STORAGE_KEY,
        JSON.stringify({
          [idempotencyKey]: {
            version: 1,
            idempotencyKey,
            taskId: "deadline-task-id",
            mode: "template-v2",
            sourcePresentationId,
            status: "running",
            createdAt: Date.now() - 30 * 60 * 1000,
            deadlineAt: Date.now() - 1,
            progressDurationSeconds: 60,
          },
        })
      );
    });
    cy.stub(DashboardApi, "deletePresentation").resolves(undefined as any);
    cy.stub(PresentationGenerationApi, "getGenerationStatus")
      .resolves({
        status: "completed",
        data: { presentation_id: "deadline-completed-presentation" },
      } as any)
      .as("getStatus");

    mountHarness(makeTemplateV2SelectionId("brand-template", 4), sourcePresentationId);

    cy.get("@getStatus").should("have.been.calledOnceWith", "deadline-task-id");
    cy.get("@replace").should(
      "have.been.calledOnceWith",
      "/presentation?id=deadline-completed-presentation"
    );
  });

  it("clears a persisted task and loading state when recovery finds an error", () => {
    const sourcePresentationId = "outline-shell-error";
    const idempotencyKey = "ppt-generation-error";
    cy.window().then((window) => {
      window.localStorage.setItem(
        DURABLE_TASK_STORAGE_KEY,
        JSON.stringify({
          [idempotencyKey]: {
            version: 1,
            idempotencyKey,
            taskId: "failed-task-id",
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
    cy.stub(notify, "error").as("notifyError");
    cy.stub(PresentationGenerationApi, "getGenerationStatus").resolves({
      status: "error",
      request_id: "recovered-error-request",
      message: "Presentation generation failed",
      error: { status_code: 422, code: "invalid_outline" },
    } as any);

    mountHarness(AUTHORED_TEMPLATE_ID, sourcePresentationId);

    cy.get("@notifyError").should("have.been.calledOnce");
    cy.get('[data-cy="generation-loading"]').should("have.text", "idle");
    cy.window().then((window) => {
      expect(window.localStorage.getItem(DURABLE_TASK_STORAGE_KEY)).to.equal("{}");
    });
  });

  it("resumes a persisted task after remount and opens its completed deck", () => {
    const sourcePresentationId = "outline-shell-recovery";
    const idempotencyKey = "ppt-generation-recovery";
    cy.window().then((window) => {
      window.localStorage.setItem(
        DURABLE_TASK_STORAGE_KEY,
        JSON.stringify({
          [idempotencyKey]: {
            version: 1,
            idempotencyKey,
            taskId: "persisted-task-id",
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
    cy.stub(DashboardApi, "deletePresentation").resolves(undefined as any);
    cy.stub(PresentationGenerationApi, "getGenerationStatus")
      .onFirstCall()
      .resolves({ status: "running" } as any)
      .onSecondCall()
      .resolves({
        status: "completed",
        data: { presentation_id: "recovered-presentation" },
      } as any)
      .as("getStatus");

    mountHarness(AUTHORED_TEMPLATE_ID, sourcePresentationId).then(
      ({ rerender }) => {
        cy.get("@getStatus").should("have.been.calledOnce");
        rerender(<></>);
        cy.then(() => {
          rerender(
            <AppRouterContext.Provider value={router as any}>
              <Provider store={store}>
                <GenerationHarness
                  selectedTemplate={AUTHORED_TEMPLATE_ID}
                  presentationId={sourcePresentationId}
                />
              </Provider>
            </AppRouterContext.Provider>
          );
        });
      }
    );

    cy.get("@getStatus").should("have.been.calledTwice");
    cy.get("@replace").should(
      "have.been.calledOnceWith",
      "/presentation?id=recovered-presentation"
    );
    cy.window().then((window) => {
      expect(window.localStorage.getItem(DURABLE_TASK_STORAGE_KEY)).to.equal("{}");
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
      "생성이 일시 중단되었습니다",
      Cypress.sinon.match("요청 ID: authored-request-6"),
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
    cy.get('[data-cy="generation-loading"]').should("have.text", "idle");
  });

  it("keeps a pending task recoverable in the background at the foreground deadline", () => {
    cy.clock();
    cy.stub(notify, "info").as("notifyInfo");
    cy.stub(PresentationGenerationApi, "generateTemplateV2Async")
      .resolves({
        id: "template-v2-task",
        request_id: "timeout-request-9",
      } as any)
      .as("startGeneration");
    cy.stub(PresentationGenerationApi, "getGenerationStatus")
      .resolves({ status: "pending" } as any)
      .as("getStatus");

    mountHarness(
      makeTemplateV2SelectionId("brand-template", 4),
      "outline-shell-timeout"
    );
    cy.contains("button", "Generate").click();
    cy.get("@startGeneration").should("have.been.calledOnce");
    cy.tick(30 * 60 * 1000);

    cy.get("@getStatus").should("have.been.called");
    cy.get("@startGeneration").should("have.been.calledOnce");
    cy.get("@notifyInfo").should("have.been.calledOnce");
    cy.get('[data-cy="generation-loading"]').should("have.text", "idle");
    cy.window().then((window) => {
      const tasks = JSON.parse(
        window.localStorage.getItem(DURABLE_TASK_STORAGE_KEY) || "{}"
      );
      expect(Object.values(tasks)).to.have.length(1);
      expect((Object.values(tasks)[0] as any).status).to.equal("background");
    });
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

    cy.get("@getStatus").should("have.been.calledOnce");
    cy.get("@notifyError").should("not.have.been.called");
  });
});
