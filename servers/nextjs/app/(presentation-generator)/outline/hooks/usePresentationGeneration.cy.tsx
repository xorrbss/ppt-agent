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
  presentationId = null,
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
  generationKey = "initial"
) {
  return mount(
    <AppRouterContext.Provider value={router as any}>
      <Provider store={store}>
        <GenerationHarness
          key={generationKey}
          selectedTemplate={selectedTemplate}
          presentationId={presentationId}
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
      content: "# Selected outline",
      slides_markdown: ["# Selected outline"],
      language: null,
      vision_qa: true,
      authored_style: "strategic-navy",
      idempotency_key: Cypress.sinon.match.string,
    });
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
