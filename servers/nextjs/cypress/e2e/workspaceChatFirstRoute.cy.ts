const PRESENTATION_ID = "route-workspace-deck";
const WORKSPACE_ROOT = "**/api/v1/ppt/workspace";

// Next 16's dev-only Turbopack performance reporter can issue a negative
// Performance.measure timestamp under Electron. It is unrelated to application
// state and does not occur in production builds, so keep this exception filter
// intentionally limited to that reporter defect.
Cypress.on("uncaught:exception", (error) => {
  if (error.message.includes("cannot have a negative time stamp")) return false;
});

const firstUserMessage = {
  anchor_type: "message",
  id: "message-first-user",
  position: 10,
  role: "user",
  content: "정보보호 업무 AX 전환 전략을 10장으로 만들어줘",
  attachments: [
    { id: "asset-ready", file_name: "현황 분석.pdf", status: "ready" },
    { id: "asset-processing", file_name: "추진 계획.xlsx", status: "processing" },
  ],
  generation_settings: {
    n_slides: 10,
    language: "ko",
    tone: "professional",
    api_key: "must-not-be-rendered",
  },
};

const operation = (overrides: Record<string, unknown> = {}) => ({
  anchor_type: "operation",
  id: "operation-generate",
  operation_id: "operation-generate",
  position: 20,
  operation_type: "presentation.generate",
  title: "발표자료 생성",
  status: "running",
  version: 3,
  progress: 42,
  events: [
    {
      id: "event-progress",
      operation_id: "operation-generate",
      operation_version: 3,
      sequence: 2,
      type: "progress",
      message: "슬라이드 레이아웃을 작성하고 있습니다.",
      progress: 42,
    },
  ],
  ...overrides,
});

const presentation = {
  id: PRESENTATION_ID,
  title: "AX 전환 전략",
  language: "ko",
  n_slides: 0,
  mode: "authored",
  theme: { mode: "authored" },
  slides: [],
};

function interceptPresentation(): void {
  cy.intercept("GET", `**/api/v1/ppt/presentation/${PRESENTATION_ID}`, {
    statusCode: 200,
    body: presentation,
  }).as("presentation");
}

function interceptAuthenticatedShell(): void {
  // The runner disables only the route's server-side auth gate. Everything the
  // browser requests remains behind explicit Cypress fixtures: no credentials
  // are created and no request is allowed to depend on FastAPI.
  cy.intercept("GET", "**/api/v1/auth/status", {
    statusCode: 200,
    body: { configured: true, authenticated: true, username: "e2e", role: "admin" },
  }).as("authStatus");

  // Select the immutable/server-managed configuration branch, then provide its
  // runtime configuration. This prevents an unmocked setup redirect while the
  // normal authenticated shell mounts ahead of the workspace panel.
  cy.intercept("GET", "**/api/can-change-keys", {
    statusCode: 200,
    body: { canChange: false },
  }).as("canChangeKeys");
  cy.intercept("GET", "**/api/runtime-config", {
    statusCode: 200,
    body: { configured: true, config: {} },
  }).as("runtimeConfig");
  cy.intercept("GET", "**/api/telemetry-status", {
    statusCode: 200,
    body: { telemetryEnabled: false },
  });
  cy.intercept("GET", "**/api/v1/ppt/authored-styles", {
    statusCode: 200,
    body: [],
  });
  cy.intercept("GET", "**/api/v1/ppt/structured-templates*", {
    statusCode: 200,
    body: [],
  });
  cy.intercept("GET", "**/api/v1/ppt/themes/all", {
    statusCode: 200,
    body: [],
  });
}

function interceptCapability(): void {
  cy.intercept("GET", `${WORKSPACE_ROOT}/capabilities`, {
    statusCode: 200,
    body: { supported: true, enabled: true, timeline: true, commands: true, events: true },
  }).as("workspaceCapability");
}

function visitWorkspace(): void {
  cy.visit(`/presentation?id=${PRESENTATION_ID}`, {
    onBeforeLoad(window) {
      // The test server enables the compile-time public feature flag. Clear
      // browser state so an existing user's local session cannot affect route
      // coverage.
      window.localStorage.clear();
    },
  });
}

function interceptIdleWorkspaceEvents(cursor: string): void {
  cy.intercept("GET", `${WORKSPACE_ROOT}/presentations/${PRESENTATION_ID}/events*`, (request) => {
    const url = new URL(request.url);
    if (url.searchParams.get("stream") === "1") {
      request.destroy();
      return;
    }
    request.reply({
      statusCode: 200,
      body: { events: [], event_high_watermark: cursor },
    });
  });
}

describe("Chat-first Slides Workbench route", () => {
  beforeEach(() => {
    interceptAuthenticatedShell();
    interceptPresentation();
    interceptCapability();
  });

  it("keeps the bootstrap request as the first durable chat message after a browser reload", () => {
    let timelineCalls = 0;
    const timeline = {
      items: [firstUserMessage, operation()],
      event_high_watermark: "cursor-2",
      revision: 7,
      has_more: false,
    };
    cy.intercept("GET", `${WORKSPACE_ROOT}/presentations/${PRESENTATION_ID}/timeline*`, (request) => {
      timelineCalls += 1;
      request.reply({ statusCode: 200, body: timeline });
    }).as("timeline");
    cy.intercept("GET", `${WORKSPACE_ROOT}/presentations/${PRESENTATION_ID}/events*`, (request) => {
      request.destroy();
    }).as("workspaceEvents");

    visitWorkspace();
    cy.wait(["@presentation", "@workspaceCapability", "@timeline"]);
    cy.get('[aria-label="프레젠테이션 작업 대화"]').should("be.visible");
    cy.get('[aria-label="사용자 메시지"]').contains(firstUserMessage.content).should("exist");
    cy.get('[aria-label="첨부 파일"]').contains("현황 분석.pdf").should("be.visible");
    cy.contains("생성 설정").click();
    cy.contains("n_slides").should("be.visible");
    cy.contains("must-not-be-rendered").should("not.exist");

    cy.reload();
    cy.wait(["@presentation", "@workspaceCapability", "@timeline"]);
    cy.get('[aria-label="사용자 메시지"]').contains(firstUserMessage.content).should("exist");
    cy.wrap(null).then(() => expect(timelineCalls).to.be.greaterThan(1));
  });

  it("rebases the real route's chat timeline after an expired event cursor", () => {
    let timelineCalls = 0;
    let streamCalls = 0;
    cy.intercept("GET", `${WORKSPACE_ROOT}/presentations/${PRESENTATION_ID}/timeline*`, (request) => {
      timelineCalls += 1;
      request.reply({
        statusCode: 200,
        body:
          timelineCalls === 1
            ? { items: [firstUserMessage, operation({ progress: 10 })], event_high_watermark: "cursor-old", revision: 7 }
            : {
                items: [
                  firstUserMessage,
                  operation({ status: "succeeded", progress: 100, result: { summary: "복구된 완료 결과" } }),
                ],
                event_high_watermark: "cursor-rebased",
                revision: 8,
              },
      });
    }).as("rebaseTimeline");
    cy.intercept("GET", `${WORKSPACE_ROOT}/presentations/${PRESENTATION_ID}/events*`, (request) => {
      const url = new URL(request.url);
      if (url.searchParams.get("stream") !== "1") {
        request.reply({ statusCode: 200, body: { events: [], event_high_watermark: "cursor-old" } });
        return;
      }
      streamCalls += 1;
      if (streamCalls === 1) {
        request.reply({ statusCode: 410, body: { code: "event_cursor_expired", message: "cursor expired" } });
        return;
      }
      request.destroy();
    }).as("reconnectEvents");

    visitWorkspace();
    cy.wait("@rebaseTimeline");
    cy.wait("@reconnectEvents");
    cy.get('[data-operation-id="operation-generate"]', { timeout: 10_000 })
      .find('[role="progressbar"]')
      .should("have.attr", "aria-valuenow", "100");
    cy.contains("복구된 완료 결과").should("be.visible");
    cy.wrap(null).then(() => expect(timelineCalls).to.be.greaterThan(1));
  });

  it("keeps generation completion and a following edit in one route-level timeline", () => {
    const commandBodies: Array<Record<string, unknown>> = [];
    const generated = operation({
      status: "succeeded",
      progress: 100,
      result: { summary: "첫 슬라이드를 게시했습니다.", revision: 7, slide_count: 1 },
    });
    const edited = operation({
      id: "operation-edit",
      operation_id: "operation-edit",
      operation_type: "slide.edit",
      title: "슬라이드 수정",
      position: 40,
      status: "succeeded",
      progress: 100,
      result: { summary: "제목을 간결하게 수정했습니다.", revision: 8 },
      events: [],
    });
    cy.intercept("GET", `${WORKSPACE_ROOT}/presentations/${PRESENTATION_ID}/timeline*`, {
      statusCode: 200,
      body: {
        items: [firstUserMessage, generated],
        event_high_watermark: "cursor-generated",
        revision: 7,
        has_more: false,
      },
    }).as("generatedTimeline");
    interceptIdleWorkspaceEvents("cursor-generated");
    cy.intercept("POST", `${WORKSPACE_ROOT}/presentations/${PRESENTATION_ID}/commands`, (request) => {
      commandBodies.push(request.body as Record<string, unknown>);
      request.reply({
        statusCode: 200,
        body: {
          items: [
            firstUserMessage,
            generated,
            {
              anchor_type: "message",
              id: "message-edit",
              position: 30,
              role: "user",
              content: "제목을 더 짧게 다듬어 줘",
              attachments: [],
              generation_settings: {},
            },
            edited,
          ],
          event_high_watermark: "cursor-edited",
          revision: 8,
          has_more: false,
        },
      });
    }).as("editCommand");

    visitWorkspace();
    cy.wait("@generatedTimeline");
    cy.contains("첫 슬라이드를 게시했습니다.").should("be.visible");
    cy.get("#workspace-command").type("제목을 더 짧게 다듬어 줘{enter}");
    cy.wait("@editCommand").then(() => {
      expect(commandBodies).to.have.length(1);
      expect(commandBodies[0]).to.include({
        command_type: "slide.edit",
        expected_revision: 7,
      });
    });
    cy.get('[data-operation-id="operation-edit"]')
      .should("have.attr", "data-operation-status", "succeeded")
      .and("contain.text", "제목을 간결하게 수정했습니다.");
  });

  it("routes an authored PPTX export through chat and marks its immutable artifact stale", () => {
    cy.viewport(1440, 900);
    const commandBodies: Array<Record<string, unknown>> = [];
    const exportCard = operation({
      id: "operation-export",
      operation_id: "operation-export",
      operation_type: "export.pptx",
      title: "PPTX 내보내기",
      position: 30,
      status: "succeeded",
      progress: 100,
      events: [],
      result: {
        artifact_id: "artifact-route-1",
        artifact_kind: "pptx",
        artifact_sha256: "a".repeat(64),
        artifact_size_bytes: 2048,
        file_name: "ax-strategy.pptx",
        format: "pptx",
        source_revision: 7,
        expires_at: "2099-08-03T00:00:00Z",
      },
    });
    cy.intercept("GET", `${WORKSPACE_ROOT}/presentations/${PRESENTATION_ID}/timeline*`, {
      statusCode: 200,
      body: {
        items: [firstUserMessage, operation({ status: "succeeded", progress: 100 })],
        event_high_watermark: "cursor-before-export",
        revision: 7,
        has_more: false,
      },
    }).as("exportTimeline");
    interceptIdleWorkspaceEvents("cursor-before-export");
    cy.intercept("POST", `${WORKSPACE_ROOT}/presentations/${PRESENTATION_ID}/commands`, (request) => {
      commandBodies.push(request.body as Record<string, unknown>);
      request.reply({
        statusCode: 200,
        body: {
          items: [firstUserMessage, exportCard],
          event_high_watermark: "cursor-exported",
          revision: 8,
          has_more: false,
        },
      });
    }).as("exportCommand");
    cy.intercept("GET", `${WORKSPACE_ROOT}/artifacts/artifact-route-1/download`, {
      statusCode: 200,
      headers: {
        "content-type": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        "content-disposition": 'attachment; filename="ax-strategy.pptx"',
      },
      body: "route-fixture-pptx",
    }).as("artifactDownload");

    visitWorkspace();
    cy.wait("@exportTimeline");
    cy.get('[data-testid="export-trigger"]').click();
    cy.get('[data-testid="authored-export-hybrid"]').click();
    cy.wait("@exportCommand").then(() => {
      expect(commandBodies[0]).to.include({
        command_type: "export.pptx",
        expected_revision: 7,
      });
      expect(commandBodies[0]).to.have.nested.property("context.format", "pptx");
    });
    cy.get('[data-testid="export-artifact-result"]')
      .should("contain.text", "ax-strategy.pptx")
      .and("contain.text", "소스 리비전 7");
    cy.get('[data-testid="artifact-stale"]').should("be.visible");
    cy.get('[data-testid="artifact-download"]').click();
    cy.wait("@artifactDownload");
  });

  it("keeps destructive approval and cancellation as versioned chat actions", () => {
    const actionBodies: Array<Record<string, unknown>> = [];
    let state = "awaiting_approval";
    let version = 3;
    const approvalCard = () =>
      operation({
        id: "operation-regenerate",
        operation_id: "operation-regenerate",
        operation_type: "slide.regenerate",
        title: "슬라이드 다시 생성",
        status: state,
        version,
        progress: state === "running" ? 15 : 0,
        approval_expires_at: "2099-08-03T00:00:00Z",
        events: [],
      });
    cy.intercept("GET", `${WORKSPACE_ROOT}/presentations/${PRESENTATION_ID}/timeline*`, {
      statusCode: 200,
      body: {
        items: [firstUserMessage, approvalCard()],
        event_high_watermark: "cursor-approval",
        revision: 7,
        has_more: false,
      },
    }).as("approvalTimeline");
    interceptIdleWorkspaceEvents("cursor-approval");
    cy.intercept("POST", `${WORKSPACE_ROOT}/operations/operation-regenerate/actions`, (request) => {
      actionBodies.push(request.body as Record<string, unknown>);
      if (request.body.action === "approve") {
        state = "running";
        version = 4;
      } else if (request.body.action === "cancel") {
        state = "cancelled";
        version = 5;
      }
      request.reply({
        statusCode: 200,
        body: {
          items: [firstUserMessage, approvalCard()],
          event_high_watermark: `cursor-${state}`,
          revision: 7,
          has_more: false,
        },
      });
    }).as("operationAction");

    visitWorkspace();
    cy.wait("@approvalTimeline");
    cy.get('[data-operation-id="operation-regenerate"] [data-testid="operation-approve"]').click();
    cy.wait("@operationAction");
    cy.get('[data-operation-id="operation-regenerate"]')
      .should("have.attr", "data-operation-status", "running")
      .find('[data-testid="operation-cancel"]')
      .click();
    cy.wait("@operationAction");
    cy.get('[data-operation-id="operation-regenerate"]')
      .should("have.attr", "data-operation-status", "cancelled");
    cy.wrap(null).then(() => {
      expect(actionBodies.map((body) => body.action)).to.deep.equal(["approve", "cancel"]);
      expect(actionBodies.map((body) => body.expected_operation_version)).to.deep.equal([3, 4]);
      expect(actionBodies[0].idempotency_key).not.to.equal(actionBodies[1].idempotency_key);
    });
  });

  it("deduplicates a poll/stream race while advancing the same operation card", () => {
    let pollCalls = 0;
    let streamCalls = 0;
    const progressEvent = {
      id: "event-race-progress",
      operation_id: "operation-generate",
      operation_version: 4,
      sequence: 3,
      type: "progress",
      status: "running",
      message: "레이스 진행 이벤트",
      progress: 60,
    };
    cy.intercept("GET", `${WORKSPACE_ROOT}/presentations/${PRESENTATION_ID}/timeline*`, {
      statusCode: 200,
      body: {
        items: [firstUserMessage, operation({ progress: 10 })],
        event_high_watermark: "cursor-race-1",
        revision: 7,
        has_more: false,
      },
    }).as("raceTimeline");
    cy.intercept("GET", `${WORKSPACE_ROOT}/presentations/${PRESENTATION_ID}/events*`, (request) => {
      const url = new URL(request.url);
      if (url.searchParams.get("stream") !== "1") {
        pollCalls += 1;
        request.reply({
          statusCode: 200,
          body: {
            events: pollCalls === 1 ? [progressEvent] : [],
            event_high_watermark: "cursor-race-2",
          },
        });
        return;
      }
      streamCalls += 1;
      if (streamCalls === 1) {
        request.reply({
          statusCode: 200,
          headers: { "content-type": "text/event-stream" },
          body: `id: cursor-race-2\ndata: ${JSON.stringify(progressEvent)}\n\n`,
        });
        return;
      }
      request.destroy();
    }).as("raceEvents");

    visitWorkspace();
    cy.wait("@raceTimeline");
    cy.get('[data-operation-id="operation-generate"] [role="progressbar"]')
      .should("have.attr", "aria-valuenow", "60");
    cy.contains("레이스 진행 이벤트").should("have.length", 1);
    cy.wrap(null).should(() => {
      expect(pollCalls).to.be.greaterThan(0);
      expect(streamCalls).to.be.greaterThan(0);
    });
  });

  it("reuses an idempotency key after an ambiguous submit and explicitly rebases a tab conflict", () => {
    const ambiguousBodies: Array<Record<string, unknown>> = [];
    const conflictBodies: Array<Record<string, unknown>> = [];
    let timelineRevision = 7;
    cy.intercept("GET", `${WORKSPACE_ROOT}/presentations/${PRESENTATION_ID}/timeline*`, (request) => {
      request.reply({
        statusCode: 200,
        body: {
          items: [firstUserMessage],
          event_high_watermark: `cursor-revision-${timelineRevision}`,
          revision: timelineRevision,
          has_more: false,
        },
      });
    }).as("retryTimeline");
    interceptIdleWorkspaceEvents("cursor-revision-7");
    cy.intercept("POST", `${WORKSPACE_ROOT}/presentations/${PRESENTATION_ID}/commands`, (request) => {
      const body = request.body as Record<string, unknown>;
      if (body.command === "모호한 제출") {
        ambiguousBodies.push(body);
        if (ambiguousBodies.length === 1) {
          request.reply({
            statusCode: 503,
            body: { error: { code: "gateway_unavailable", safe_message: "Try again." } },
          });
          return;
        }
        request.reply({
          statusCode: 200,
          body: { items: [firstUserMessage], revision: 7, has_more: false },
        });
        return;
      }
      conflictBodies.push(body);
      if (conflictBodies.length === 1) {
        timelineRevision = 8;
        request.reply({
          statusCode: 409,
          body: { error: { code: "revision_conflict", safe_message: "The deck changed." } },
        });
        return;
      }
      request.reply({
        statusCode: 200,
        body: { items: [firstUserMessage], revision: 8, has_more: false },
      });
    }).as("retryCommand");

    visitWorkspace();
    cy.wait("@retryTimeline");
    cy.get("#workspace-command").type("모호한 제출{enter}");
    cy.wait("@retryCommand");
    cy.get('[data-testid="workspace-retry-intent"]').click();
    cy.wait("@retryCommand").then(() => {
      expect(ambiguousBodies).to.have.length(2);
      expect(ambiguousBodies[1].idempotency_key).to.equal(ambiguousBodies[0].idempotency_key);
      expect(ambiguousBodies[1].expected_revision).to.equal(ambiguousBodies[0].expected_revision);
    });

    cy.get("#workspace-command").type("다른 탭 이후 수정{enter}");
    cy.wait("@retryCommand");
    cy.wait("@retryTimeline");
    cy.wrap(null).then(() => expect(conflictBodies).to.have.length(1));
    cy.get('[data-testid="workspace-retry-intent"]').click();
    cy.wait("@retryCommand").then(() => {
      expect(conflictBodies).to.have.length(2);
      expect(conflictBodies[1].idempotency_key).to.equal(conflictBodies[0].idempotency_key);
      expect(conflictBodies[0].expected_revision).to.equal(7);
      expect(conflictBodies[1].expected_revision).to.equal(8);
    });
  });

  it("keeps the named workspace and progress state visible at mobile, tablet, and Gate E desktop sizes", () => {
    cy.intercept("GET", `${WORKSPACE_ROOT}/presentations/${PRESENTATION_ID}/timeline*`, {
      statusCode: 200,
      body: {
        items: [firstUserMessage, operation()],
        event_high_watermark: "cursor-responsive",
        revision: 7,
        has_more: false,
      },
    }).as("responsiveTimeline");
    cy.intercept("GET", `${WORKSPACE_ROOT}/presentations/${PRESENTATION_ID}/events*`, (request) => {
      request.destroy();
    });

    visitWorkspace();
    cy.wait(["@presentation", "@workspaceCapability", "@responsiveTimeline"]);

    const viewports = [
      { name: "mobile", width: 375, height: 667 },
      { name: "tablet", width: 768, height: 1024 },
      { name: "drawer-1024", width: 1024, height: 768 },
      { name: "desktop-1366", width: 1366, height: 768 },
      { name: "desktop-1440", width: 1440, height: 900 },
      { name: "desktop-1920", width: 1920, height: 1080 },
    ];

    for (const viewport of viewports) {
      cy.viewport(viewport.width, viewport.height);
      cy.log(`${viewport.name}: ${viewport.width}x${viewport.height}`);

      if (viewport.width < 1024) {
        cy.get('[data-testid="responsive-chat-tab"]')
          .should("be.visible")
          .and("have.attr", "aria-selected", "true");
        if (viewport.width === 375) {
          cy.get('[data-testid="responsive-slides-tab"]').click();
          cy.get('[data-testid="workspace-chat-pane"]').should("not.be.visible");
          cy.get("#presentation-slides-pane").should("be.visible");
          cy.get('[data-testid="responsive-chat-tab"]').click();
        }
      } else if (viewport.width === 1024) {
        cy.get('[data-testid="workspace-drawer-toggle"]')
          .should("be.visible")
          .and("have.attr", "aria-expanded", "true")
          .click();
        cy.get('[data-testid="workspace-chat-pane"]').should("not.be.visible");
        cy.get('[data-testid="workspace-drawer-toggle"]')
          .should("have.attr", "aria-expanded", "false")
          .click();
      }

      cy.get('[aria-label="프레젠테이션 작업 대화"]')
        .should("be.visible")
        .and(($workspace) => {
          const bounds = $workspace[0].getBoundingClientRect();
          expect(bounds.width, `${viewport.name} workspace width`).to.be.greaterThan(0);
          expect(bounds.height, `${viewport.name} workspace height`).to.be.greaterThan(0);
          expect(bounds.left, `${viewport.name} workspace left edge`).to.be.at.least(-0.5);
          expect(bounds.right, `${viewport.name} workspace right edge`).to.be.at.most(viewport.width + 0.5);
        });
      cy.get('[data-testid="workspace-command-form"]').should(($composer) => {
        const bounds = $composer[0].getBoundingClientRect();
        expect(bounds.left, `${viewport.name} composer left edge`).to.be.at.least(-0.5);
        expect(bounds.right, `${viewport.name} composer right edge`).to.be.at.most(viewport.width + 0.5);
      });
      cy.get('[aria-label="사용자 메시지"]').should("be.visible");
      cy.get('[role="progressbar"][aria-label="작업 진행률"]')
        .should("be.visible")
        .and("have.attr", "aria-valuemin", "0")
        .and("have.attr", "aria-valuemax", "100")
        .and("have.attr", "aria-valuenow", "42");
    }
  });
});
