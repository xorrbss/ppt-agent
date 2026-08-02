import React from "react";

import WorkspaceChatPanel from "@/app/(presentation-generator)/presentation/workspace/WorkspaceChatPanel";

let BACKEND = "http://127.0.0.1:8765";
const PRESENTATION_ID = "11111111-1111-4111-8111-111111111111";
const FIRST_PROMPT = "첨부한 기획서를 바탕으로 경영진용 발표를 만들어 줘.";
const SUCCESS_COMMAND = "제목을 더 짧게 다듬어 줘. [live-success]";
const FAILURE_COMMAND = "실패 상태도 채팅에 남겨 줘. [live-failure]";
let WORKSPACE_ROOT = `${BACKEND}/api/v1/ppt/workspace`;

type TimelineItem = {
  anchor_type?: string;
  id?: string;
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
        throw new Error("Timed out waiting for the durable workspace timeline");
      }
      return cy
        .wait(75, { log: false })
        .then(() => waitForTimeline(predicate, attempts - 1));
    });
}

function advanceRevision(attempts = 8): Cypress.Chainable<Cypress.Response<{ revision: number }>> {
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
      .its("body")
      .should("deep.include", {
        ready: true,
        presentation_id: PRESENTATION_ID,
        storage: "sqlite",
        runner: "workspace-operation-runner",
      });
  });

  beforeEach(() => {
    selectLiveBackend();
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

    mountPanel();
    cy.wait("@liveTimeline").then(({ response }) => {
      expect(response?.statusCode).to.equal(200);
      expect(response?.headers).to.have.property(
        "x-workspace-live-harness",
        "sqlite-runner-v1",
      );
    });

    cy.get('[aria-label="사용자 메시지"]').within(() => {
      cy.contains(FIRST_PROMPT).should("be.visible");
      cy.get('[aria-label="첨부 파일"]').within(() => {
        cy.contains("strategy-brief.pdf").should("be.visible");
        cy.contains("업로드됨").should("be.visible");
      });
      cy.contains("생성 설정").click();
      cy.contains("tone").should("be.visible");
      cy.contains("executive").should("be.visible");
      cy.contains("language").should("be.visible");
      cy.contains("ko").should("be.visible");
      cy.contains("n_slides").should("be.visible");
      cy.contains("5").should("be.visible");
    });
    cy.get('[data-operation-status="waiting_for_assets"]').should("exist");

    cy.get("#workspace-command").type(`${SUCCESS_COMMAND}{enter}`);
    cy.wait("@liveCommand").then(({ request, response }) => {
      expect(request.body.command).to.equal(SUCCESS_COMMAND);
      expect(request.body.command_type).to.equal("presentation.edit");
      expect(response?.statusCode).to.equal(200);
      expect(response?.headers).to.have.property(
        "x-workspace-live-harness",
        "sqlite-runner-v1",
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
              cy.contains("revision: 0").should("be.visible");
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
                candidate.error === "browser_fixture_failure",
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
                cy.contains("browser_fixture_failure").should("be.visible");
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
          return item?.status === "failed" && item.error === "browser_fixture_failure";
        });
      })
      .then(() => cy.request(`${BACKEND}/__test__/stats`))
      .then((response) => {
        expect(response.body.execution_counts[duplicateCommand]).to.equal(1);
      });
  });

  it("rebases a real SQLite revision conflict and runs only after explicit user retry", () => {
    const conflictCommand = "다른 탭의 변경 이후 제목을 수정해 줘. [live-success]";
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
    advanceRevision()
      .its("body.revision")
      .should("equal", 1);

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
