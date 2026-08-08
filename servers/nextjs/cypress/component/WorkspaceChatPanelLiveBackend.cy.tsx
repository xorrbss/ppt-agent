import React from "react";

import WorkspaceChatPanel from "@/app/(presentation-generator)/presentation/workspace/WorkspaceChatPanel";

let BACKEND = "http://127.0.0.1:8765";
const PRESENTATION_ID = "11111111-1111-4111-8111-111111111111";
const FIRST_PROMPT = "첨부한 기획서를 바탕으로 경영진용 발표를 만들어 줘";
const SUCCESS_COMMAND =
  "제목을 더 짧게 다듬어 줘. [live-success] [live-backfill]";
const FAILURE_COMMAND = "실패 상태도 채팅에 남겨 줘. [live-failure]";
let WORKSPACE_ROOT = `${BACKEND}/api/v1/ppt/workspace`;

type TimelineItem = {
  anchor_type?: string;
  id?: string;
  role?: string;
  content?: string;
  attachments?: Array<{
    id?: string;
    name?: string;
    status?: string;
    current_state?: string;
    safe_failure?: {
      failure_code?: string;
      safe_message?: string;
      retryable?: boolean;
    };
  }>;
  operation_id?: string;
  operation_type?: string;
  status?: string;
  progress?: number;
  result?: Record<string, unknown>;
  error?: string;
};

type TimelineBody = {
  items?: TimelineItem[];
  revision?: number;
};

function operation(
  body: TimelineBody,
  predicate: (item: TimelineItem) => boolean,
): TimelineItem | undefined {
  return body.items?.find(
    (item) => item.anchor_type === "operation" && predicate(item),
  );
}

function waitForTimeline(
  predicate: (body: TimelineBody) => boolean,
  attempts = 80,
): Cypress.Chainable<Cypress.Response<TimelineBody>> {
  return cy
    .request<TimelineBody>({
      url: `${WORKSPACE_ROOT}/presentations/${PRESENTATION_ID}/timeline`,
      headers: { Accept: "application/json" },
    })
    .then((response) => {
      if (predicate(response.body)) return cy.wrap(response, { log: false });
      if (attempts <= 1) {
        const recentOperations = (response.body.items ?? [])
          .filter((item) => item.anchor_type === "operation")
          .slice(-5)
          .map((item) => ({
            operation_id: item.operation_id,
            operation_type: item.operation_type,
            status: item.status,
            progress: item.progress,
            error: item.error,
          }));
        throw new Error(
          `Timed out waiting for the durable workspace timeline: ${JSON.stringify(
            {
              revision: response.body.revision,
              recent_operations: recentOperations,
            },
          )}`,
        );
      }
      return cy
        .wait(75, { log: false })
        .then(() => waitForTimeline(predicate, attempts - 1));
    });
}

function advanceRevision(
  attempts = 8,
): Cypress.Chainable<Cypress.Response<{ revision: number }>> {
  // The live fixture intentionally uses the production SQLite runner.  Its
  // terminal-event commit can overlap the simulated second-tab write at the
  // start of this scenario.  Treat SQLite's short-lived writer lock as a
  // transport condition in the browser test, rather than turning it into a
  // flaky assertion about revision-conflict behavior.
  return cy
    .request<{ revision: number }>({
      method: "POST",
      url: `${BACKEND}/__test__/presentation/revision/advance`,
      failOnStatusCode: false,
      log: false,
    })
    .then((response) => {
      if (response.status === 200) return cy.wrap(response, { log: false });
      if (attempts <= 1) {
        throw new Error(
          `Timed out advancing the live fixture revision (last status ${response.status}).`,
        );
      }
      return cy
        .wait(250, { log: false })
        .then(() => advanceRevision(attempts - 1));
    });
}

function mountPanel(): void {
  cy.mount(
    <div style={{ height: "720px", width: "460px" }}>
      <WorkspaceChatPanel presentationId={PRESENTATION_ID} />
    </div>,
  );
}

function selectLiveBackend(): void {
  cy.window().then((window) => {
    const url = new URL(window.location.href);
    url.searchParams.set("fastapiUrl", BACKEND);
    window.history.replaceState({}, "", url);
  });
}

describe("WorkspaceChatPanel with live FastAPI persistence and runner", () => {
  before(() => {
    cy.env(["WORKSPACE_LIVE_BACKEND"])
      .then((configuredEnvironment) => {
        const configuredBackend = configuredEnvironment.WORKSPACE_LIVE_BACKEND;
        if (configuredBackend) BACKEND = String(configuredBackend);
        WORKSPACE_ROOT = `${BACKEND}/api/v1/ppt/workspace`;
        return cy.request(`${BACKEND}/__test__/health`);
      })
      .then(({ body }) => {
        expect(body).to.deep.include({
          ready: true,
          presentation_id: PRESENTATION_ID,
          storage: "sqlite",
          runner: "workspace-operation-runner",
          application: "api.main:app",
          database_was_blank: true,
        });
        expect(body.alembic_revision).to.be.a("string");
        expect(body.alembic_revision).not.to.equal("");
        expect(body.alembic_revision).to.equal(body.alembic_head);
      });
  });

  beforeEach(() => {
    selectLiveBackend();
  });

  afterEach(() => {
    // A failed assertion must never strand the production runner behind a
    // test-owned progress gate and cascade into later scenarios.
    cy.request({
      method: "POST",
      url: `${BACKEND}/__test__/release`,
      failOnStatusCode: false,
      log: false,
    });
  });

  it("persists the first turn and reconnects across real progress, success, and failure", () => {
    // These routes are spies only. No workspace response is stubbed in this
    // test; every response below comes from the standalone FastAPI process.
    cy.intercept(
      "GET",
      `${WORKSPACE_ROOT}/presentations/${PRESENTATION_ID}/timeline*`,
    ).as("liveTimeline");
    cy.intercept(
      "POST",
      `${WORKSPACE_ROOT}/presentations/${PRESENTATION_ID}/commands`,
    ).as("liveCommand");
    let attachmentId = "";

    mountPanel();
    cy.wait("@liveTimeline").then(({ response }) => {
      expect(response?.statusCode).to.equal(200);
      expect(response?.headers).to.have.property(
        "x-workspace-live-harness",
        "production-lifespan-alembic-v1",
      );
    });

    waitForTimeline((body) => {
      const firstMessage = body.items?.find(
        (item) =>
          item.anchor_type === "message" &&
          item.role === "user" &&
          item.content === FIRST_PROMPT,
      );
      const attachment = firstMessage?.attachments?.[0];
      const generation = operation(
        body,
        (item) => item.operation_type === "presentation.generate",
      );
      return (
        attachment?.status === "uploaded" &&
        attachment.current_state === "uploaded" &&
        generation?.status === "waiting_for_assets"
      );
    })
      .then(({ body }) => {
        const firstMessage = body.items?.find(
          (item) =>
            item.anchor_type === "message" &&
            item.role === "user" &&
            item.content === FIRST_PROMPT,
        );
        const attachment = firstMessage?.attachments?.[0];
        attachmentId = attachment?.id ?? "";
        expect(attachmentId).not.to.equal("");
        expect(attachment).to.deep.include({
          name: "strategy-brief.pdf",
          status: "uploaded",
          current_state: "uploaded",
        });
        return cy.request("POST", `${BACKEND}/__test__/asset/fail`);
      })
      .then(({ body }) => {
        expect(body).to.deep.include({
          state: "failed",
          failure_code: "workspace_asset_failed",
        });
        return waitForTimeline((timeline) => {
          const firstMessage = timeline.items?.find(
            (item) =>
              item.anchor_type === "message" &&
              item.role === "user" &&
              item.content === FIRST_PROMPT,
          );
          const attachment = firstMessage?.attachments?.[0];
          const generation = operation(
            timeline,
            (item) => item.operation_type === "presentation.generate",
          );
          return (
            attachment?.status === "uploaded" &&
            attachment.current_state === "failed" &&
            attachment.safe_failure?.failure_code ===
              "workspace_asset_failed" &&
            generation?.status === "blocked" &&
            generation.error === "workspace_asset_failed"
          );
        });
      })
      .then(({ body }) => {
        const firstMessage = body.items?.find(
          (item) =>
            item.anchor_type === "message" &&
            item.role === "user" &&
            item.content === FIRST_PROMPT,
        );
        const attachment = firstMessage?.attachments?.[0];
        const generation = operation(
          body,
          (item) => item.operation_type === "presentation.generate",
        );

        expect(attachment).to.deep.include({
          name: "strategy-brief.pdf",
          status: "uploaded",
          current_state: "failed",
        });
        expect(attachment?.safe_failure).to.deep.include({
          failure_code: "workspace_asset_failed",
          retryable: true,
        });
        expect(attachment?.safe_failure?.safe_message).to.be.a("string");
        expect(attachment?.safe_failure?.safe_message).not.to.equal("");
        expect(generation).to.deep.include({
          status: "blocked",
          error: "workspace_asset_failed",
        });
        // Reconnect after the control-plane transition so the component must
        // hydrate the newer live asset projection from durable persistence.
        mountPanel();
        return cy.wait("@liveTimeline");
      });

    cy.contains('[aria-label="사용자 메시지"]', FIRST_PROMPT).should(
      "have.length",
      1,
    );
    cy.then(() => {
      expect(attachmentId).not.to.equal("");
      cy.get(`[data-testid="attachment-${attachmentId}"]`)
        .should("have.length", 1)
        .within(() => {
          cy.contains("strategy-brief.pdf").should("be.visible");
          cy.contains("당시 업로드됨").should("be.visible");
          cy.contains("현재 처리 실패").should("be.visible");
          cy.contains(
            "처리에 실패했습니다. 원본 파일을 확인한 뒤 다시 첨부해 주세요.",
          ).should("be.visible");
        });
    });
    cy.contains('[aria-label="사용자 메시지"]', FIRST_PROMPT).within(() => {
      cy.contains(FIRST_PROMPT).should("be.visible");
      cy.contains("생성 설정").click();
      cy.contains("문체").should("be.visible");
      cy.contains("executive").should("be.visible");
      cy.contains("언어").should("be.visible");
      cy.contains("ko").should("be.visible");
      cy.contains("슬라이드 수").should("be.visible");
      cy.contains("5").should("be.visible");
      cy.contains("tone").should("not.exist");
      cy.contains("language").should("not.exist");
      cy.contains("n_slides").should("not.exist");
    });
    cy.get('[data-operation-status="blocked"]')
      .should("exist")
      .and("contain.text", "사용자 조치 필요");

    cy.get("#workspace-command").type(`${SUCCESS_COMMAND}{enter}`);
    cy.wait("@liveCommand").then(({ request, response }) => {
      expect(request.body.command).to.equal(SUCCESS_COMMAND);
      expect(request.body.command_type).to.equal("presentation.edit");
      expect(response?.statusCode).to.equal(200);
      expect(response?.headers).to.have.property(
        "x-workspace-live-harness",
        "production-lifespan-alembic-v1",
      );
    });

    let successOperationId = "";
    waitForTimeline((body) => {
      const item = operation(
        body,
        (candidate) =>
          candidate.operation_type === "presentation.edit" &&
          candidate.status === "running" &&
          candidate.progress === 25,
      );
      if (item?.operation_id) successOperationId = item.operation_id;
      return Boolean(successOperationId);
    }).then(() => {
      expect(successOperationId).not.to.equal("");

      // Unmount closes the browser stream. The backend then commits 80% while
      // no component exists, making the next render a true server backfill.
      cy.mount(<div data-testid="workspace-disconnected" />);
      cy.get('[data-testid="workspace-disconnected"]').should("exist");
      return cy
        .request("POST", `${BACKEND}/__test__/advance/backfill`)
        .then((response) => {
          expect(response.body).to.deep.include({
            operation_id: successOperationId,
            progress: 80,
          });

          mountPanel();
          cy.wait("@liveTimeline");
          cy.get(`[data-operation-id="${successOperationId}"]`).within(() => {
            cy.get('[role="progressbar"]').should(
              "have.attr",
              "aria-valuenow",
              "80",
            );
            cy.contains("80%").should("be.visible");
          });
          cy.get('[aria-label="사용자 메시지"]')
            .contains(FIRST_PROMPT)
            .should("be.visible");

          return cy
            .request("POST", `${BACKEND}/__test__/advance/complete`)
            .its("body.released")
            .should("equal", true);
        })
        .then(() =>
          waitForTimeline((body) => {
            const item = operation(
              body,
              (candidate) => candidate.operation_id === successOperationId,
            );
            return item?.status === "succeeded" && item.result?.revision === 0;
          }),
        )
        .then(() => {
          // A second reconnect proves terminal state/result and the bootstrap
          // turn are durable snapshots, not component-local optimistic state.
          cy.mount(<div data-testid="workspace-after-success-disconnect" />);
          mountPanel();
          cy.wait("@liveTimeline");
          cy.get(`[data-operation-id="${successOperationId}"]`)
            .should("have.attr", "data-operation-status", "succeeded")
            .within(() => {
              cy.contains("리비전: 0").should("be.visible");
            });
          cy.get('[aria-label="사용자 메시지"]')
            .contains(FIRST_PROMPT)
            .should("be.visible");

          cy.get("#workspace-command").type(`${FAILURE_COMMAND}{enter}`);
          return cy
            .wait("@liveCommand")
            .its("response.statusCode")
            .should("equal", 200);
        })
        .then(() => {
          let failureOperationId = "";
          return waitForTimeline((body) => {
            const item = operation(
              body,
              (candidate) =>
                candidate.operation_type === "presentation.edit" &&
                candidate.operation_id !== successOperationId &&
                candidate.status === "failed" &&
                candidate.error === "workspace_request_failed",
            );
            if (item?.operation_id) failureOperationId = item.operation_id;
            return Boolean(failureOperationId);
          }).then(() => {
            expect(failureOperationId).not.to.equal("");
            cy.mount(<div data-testid="workspace-after-failure-disconnect" />);
            mountPanel();
            cy.wait("@liveTimeline");
            cy.get(`[data-operation-id="${failureOperationId}"]`)
              .should("have.attr", "data-operation-status", "failed")
              .within(() => {
                cy.contains(
                  "The workspace request could not be completed.",
                ).should("be.visible");
              });
          });
        });
    });
  });

  it("executes a duplicate durable command only once for one idempotency key", () => {
    const duplicateCommand = "중복 제출은 한 번만 실행해 줘. [live-failure]";
    const body = {
      command: duplicateCommand,
      command_type: "presentation.edit",
      idempotency_key: "workspace-browser-duplicate-command-v1",
      expected_revision: 0,
      context: {},
    };
    let operationId = "";

    cy.request<TimelineBody>(
      "POST",
      `${WORKSPACE_ROOT}/presentations/${PRESENTATION_ID}/commands`,
      body,
    )
      .then((first) => {
        operationId =
          first.body.items
            ?.filter(
              (candidate) =>
                candidate.anchor_type === "operation" &&
                candidate.operation_type === "presentation.edit",
            )
            .at(-1)?.operation_id ?? "";
        expect(operationId).not.to.equal("");
        return cy.request<TimelineBody>(
          "POST",
          `${WORKSPACE_ROOT}/presentations/${PRESENTATION_ID}/commands`,
          body,
        );
      })
      .then((second) => {
        const replayedOperationId = operation(
          second.body,
          (candidate) => candidate.operation_id === operationId,
        )?.operation_id;
        expect(replayedOperationId).to.equal(operationId);
        return waitForTimeline((timeline) => {
          const item = operation(
            timeline,
            (candidate) => candidate.operation_id === operationId,
          );
          return (
            item?.status === "failed" &&
            item.error === "workspace_request_failed"
          );
        });
      })
      .then(() => cy.request(`${BACKEND}/__test__/stats`))
      .then((response) => {
        expect(response.body.execution_counts[duplicateCommand]).to.equal(1);
      });
  });

  it("rebases a real SQLite revision conflict and runs only after explicit user retry", () => {
    const conflictCommand =
      "다른 탭의 변경 이후 제목을 수정해 줘. [live-success]";
    const commandBodies: Array<Record<string, unknown>> = [];

    cy.intercept(
      "GET",
      `${WORKSPACE_ROOT}/presentations/${PRESENTATION_ID}/timeline*`,
    ).as("liveConflictTimeline");
    cy.intercept(
      "POST",
      `${WORKSPACE_ROOT}/presentations/${PRESENTATION_ID}/commands`,
      (request) => {
        commandBodies.push(request.body as Record<string, unknown>);
      },
    ).as("liveConflictingCommand");

    mountPanel();
    cy.wait("@liveConflictTimeline")
      .its("response.body.revision")
      .should("equal", 0);
    advanceRevision().its("body.revision").should("equal", 1);

    cy.get("#workspace-command").type(`${conflictCommand}{enter}`);
    cy.wait("@liveConflictingCommand")
      .its("response.statusCode")
      .should("equal", 409);
    cy.wait("@liveConflictTimeline")
      .its("response.body.revision")
      .should("equal", 1);
    cy.wrap(null).then(() => expect(commandBodies).to.have.length(1));
    cy.get('[data-testid="workspace-retry-intent"]')
      .should("contain.text", "최신 버전 기준으로 다시 시도")
      .click();
    cy.wait("@liveConflictingCommand")
      .its("response.statusCode")
      .should("equal", 200);
    cy.wrap(null).then(() => {
      expect(commandBodies).to.have.length(2);
      expect(commandBodies[1].idempotency_key).to.equal(
        commandBodies[0].idempotency_key,
      );
      expect(commandBodies[0].expected_revision).to.equal(0);
      expect(commandBodies[1].expected_revision).to.equal(1);
    });
    waitForTimeline((timeline) => {
      const item = operation(
        timeline,
        (candidate) =>
          candidate.operation_type === "presentation.edit" &&
          candidate.status === "succeeded" &&
          candidate.result?.revision === 1,
      );
      return Boolean(item);
    });
    cy.request(`${BACKEND}/__test__/stats`).then((response) => {
      expect(response.body.execution_counts[conflictCommand]).to.equal(1);
    });
  });
});
