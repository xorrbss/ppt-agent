type AxeRuntime = typeof import("axe-core");

const PRESENTATION_ID = "route-workspace-deck";
const WORKSPACE_ROOT = "**/api/v1/ppt/workspace";

const enabledCapability = {
  supported: true,
  enabled: true,
  timeline: true,
  commands: true,
  events: true,
};
const disabledCapability = {
  supported: true,
  enabled: false,
  timeline: true,
  commands: false,
  events: true,
  reason: "chat_first_kill_switch_enabled",
};
let capabilityResponse: typeof enabledCapability & { reason?: string } =
  enabledCapability;
let capabilityRequestAlias: string | undefined;
let capabilityRequestCount = 0;
let secondaryWorkspaceWindow: Window | null = null;
let secondaryWorkspaceFrame: HTMLIFrameElement | null = null;

function waitForWindowSelector(
  window: Window,
  selector: string,
  {
    present = true,
    timeoutMs = 30_000,
    label = "secondary selector",
  }: { present?: boolean; timeoutMs?: number; label?: string } = {},
): Cypress.Promise<void> {
  const deadline = Date.now() + timeoutMs;
  return new Cypress.Promise<void>((resolve, reject) => {
    const poll = () => {
      if (window.closed) {
        reject(
          new Error(
            `${label}: secondary workspace browsing context closed unexpectedly.`,
          ),
        );
        return;
      }
      const matches = Boolean(window.document.querySelector(selector));
      if (matches === present) {
        resolve();
        return;
      }
      if (Date.now() >= deadline) {
        const readyState = window.document.readyState;
        const bodyMounted = Boolean(window.document.body);
        reject(
          new Error(
            `${label}: timed out waiting for ${selector} present=${present}; actual=${matches}; readyState=${readyState}; bodyMounted=${bodyMounted}; closed=${window.closed}.`,
          ),
        );
        return;
      }
      window.setTimeout(poll, 100);
    };
    poll();
  });
}

function waitForWindowText(
  window: Window,
  expectedText: string,
  {
    timeoutMs = 30_000,
    label = "secondary text",
  }: { timeoutMs?: number; label?: string } = {},
): Cypress.Promise<void> {
  const deadline = Date.now() + timeoutMs;
  return new Cypress.Promise<void>((resolve, reject) => {
    const poll = () => {
      if (window.closed) {
        reject(
          new Error(
            `${label}: secondary workspace browsing context closed unexpectedly.`,
          ),
        );
        return;
      }
      if (window.document.body?.textContent?.includes(expectedText)) {
        resolve();
        return;
      }
      if (Date.now() >= deadline) {
        const readyState = window.document.readyState;
        const bodyMounted = Boolean(window.document.body);
        reject(
          new Error(
            `${label}: timed out waiting for timeline text; textPresent=false; readyState=${readyState}; bodyMounted=${bodyMounted}; closed=${window.closed}.`,
          ),
        );
        return;
      }
      window.setTimeout(poll, 100);
    };
    poll();
  });
}

// Next 16's dev-only Turbopack performance reporter can issue a negative
// Performance.measure timestamp under Electron. It is unrelated to application
// state and does not occur in production builds, so keep this exception filter
// intentionally limited to that reporter defect.
Cypress.on("uncaught:exception", (error) => {
  if (error.message.includes("cannot have a negative time stamp")) return false;
});

const firstUserMessage = {
  schema_version: 1,
  anchor_type: "message",
  id: "11111111-1111-4111-8111-111111111112",
  position: 10,
  role: "user",
  content: "정보보호 업무 AX 전환 전략을 10장으로 만들어줘",
  attachments: [
    { id: "asset-ready", file_name: "현황 분석.pdf", status: "ready" },
    {
      id: "asset-processing",
      file_name: "추진 계획.xlsx",
      status: "processing",
    },
  ],
  generation_settings: {
    n_slides: 10,
    language: "ko",
    tone: "professional",
    api_key: "must-not-be-rendered",
  },
};

const templateV2Runtime = {
  profile: {
    schema_version: 1,
    driver_id: "template-v2",
    content_model: "template-v2",
    generation_strategy: "template",
    style_source: "imported",
    generation_mode: "standard",
  },
  capabilities: {
    schema_version: 1,
    driver_id: "template-v2",
    renderer_adapter_id: "template-v2",
    editor_adapter_id: "template-v2",
    editor: {
      element_text_edit: true,
      element_geometry_edit: true,
      chart_edit: true,
      theme_apply: true,
      template_apply: true,
      slide_image_regenerate: false,
    },
    agent_tool_capability: "template-v2",
    export_adapter_id: "template-v2-general",
    export_editable_pptx: true,
  },
};

const authoredRuntime = {
  profile: {
    schema_version: 1,
    driver_id: "authored",
    content_model: "authored",
    generation_strategy: "authored",
    style_source: "authored",
    generation_mode: "standard",
  },
  capabilities: {
    schema_version: 1,
    driver_id: "authored",
    renderer_adapter_id: "authored",
    editor_adapter_id: "authored-readonly",
    editor: {
      element_text_edit: false,
      element_geometry_edit: false,
      chart_edit: false,
      theme_apply: false,
      template_apply: true,
      slide_image_regenerate: true,
    },
    agent_tool_capability: "authored",
    export_adapter_id: "authored-hybrid",
    export_editable_pptx: true,
  },
};

const renderableSlide = {
  id: "route-slide-1",
  index: 0,
  content: {},
  ui: {
    id: "__blank_slide__",
    description: "Empty slide.",
    background: "#FFFFFF",
    components: [],
    elements: [
      {
        type: "vector",
        shape: "polygon",
        points: [
          { x: 0, y: 0 },
          { x: 1280, y: 0 },
          { x: 1280, y: 720 },
          { x: 0, y: 720 },
        ],
        closed: true,
        fill: { color: "#FFFFFF" },
        decorative: true,
      },
    ],
  },
  layout_group: "template-v2",
  layout: "__blank_slide__",
  presentation: PRESENTATION_ID,
};

const operation = (overrides: Record<string, unknown> = {}) => {
  const status =
    typeof overrides.status === "string" ? overrides.status : "running";
  const operationType =
    typeof overrides.operation_type === "string"
      ? overrides.operation_type
      : "presentation.generate";
  const allowedActions =
    status === "approval_required" || status === "awaiting_approval"
      ? [
          { id: "approve", label: "승인", variant: "primary", enabled: true },
          { id: "cancel", label: "취소", variant: "secondary", enabled: true },
        ]
      : status === "succeeded" && operationType.startsWith("export.")
        ? [
            {
              id: "download",
              label: "다운로드",
              variant: "primary",
              enabled: true,
            },
            {
              id: "retry",
              label: "다시 내보내기",
              variant: "secondary",
              enabled: true,
            },
          ]
        : status === "running"
          ? [
              {
                id: "cancel",
                label: "취소",
                variant: "secondary",
                enabled: true,
              },
            ]
          : [];
  return {
    schema_version: 1,
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
        schema_version: 1,
        id: "event-progress",
        operation_id: "operation-generate",
        operation_version: 3,
        sequence: 2,
        type: "progress",
        message: "슬라이드 레이아웃을 작성하고 있습니다.",
        progress: 42,
      },
    ],
    allowed_actions: allowedActions,
    ...overrides,
  };
};

const presentation = {
  id: PRESENTATION_ID,
  title: "AX 전환 전략",
  language: "ko",
  n_slides: 0,
  mode: "authored",
  theme: { mode: "authored" },
  slides: [],
};

const readyPresentation = {
  ...presentation,
  title: "게시된 AX 전환 전략",
  n_slides: 1,
  revision: 7,
  lifecycle_status: "published",
  mode: "template",
  version: "v2-standard",
  layout: { name: "blank", ordered: true, slides: [] },
  theme: null,
  ...templateV2Runtime,
  slides: [renderableSlide],
};

const readyAuthoredPresentation = {
  ...presentation,
  title: "게시된 AI 저작 AX 전환 전략",
  n_slides: 1,
  revision: 7,
  lifecycle_status: "published",
  source_sha256: "b".repeat(64),
  mode: "authored",
  version: "v2-standard",
  layout: null,
  theme: { mode: "authored" },
  ...authoredRuntime,
  slides: [
    {
      id: "route-authored-slide-1",
      index: 0,
      ui: null,
      html_content:
        '<section style="width:1280px;height:720px;background:#fff"><h1>AX 전환 전략</h1></section>',
      presentation: PRESENTATION_ID,
    },
  ],
};

function interceptPresentation(): void {
  cy.intercept("GET", `**/api/v1/ppt/presentation/${PRESENTATION_ID}`, {
    statusCode: 200,
    body: presentation,
  }).as("presentation");
}

function interceptReadyPresentation(
  body: typeof readyPresentation | typeof readyAuthoredPresentation =
    readyPresentation,
  alias = "readyPresentation",
): void {
  cy.intercept("GET", `**/api/v1/ppt/presentation/${PRESENTATION_ID}`, {
    statusCode: 200,
    body,
  }).as(alias);
}

function interceptAuthenticatedShell(): void {
  // The runner disables only the route's server-side auth gate. Everything the
  // browser requests remains behind explicit Cypress fixtures: no credentials
  // are created and no request is allowed to depend on FastAPI.
  cy.intercept("GET", "**/api/v1/auth/status", {
    statusCode: 200,
    body: {
      configured: true,
      authenticated: true,
      username: "e2e",
      role: "admin",
    },
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
  cy.intercept("GET", "**/api/v1/ppt/presentation/*/generation-info", {
    statusCode: 200,
    body: {
      prompt: firstUserMessage.content,
      attachment_file_names: firstUserMessage.attachments.map(
        ({ file_name }) => file_name,
      ),
      settings: {
        n_slides: 10,
        language: "ko",
        tone: "professional",
        verbosity: null,
        instructions: null,
        include_table_of_contents: false,
        include_title_slide: true,
        web_search: false,
        mode: "authored",
        version: "v2-standard",
      },
      conversation_id: "11111111-1111-4111-8111-111111111113",
      operation_id: "11111111-1111-4111-8111-111111111114",
      first_user_message_id: firstUserMessage.id,
    },
  });
  // The legacy chat is mounted immediately after a runtime kill-switch. Keep
  // that rollback path fully isolated instead of allowing its history request
  // to escape to the deliberately unavailable backend proxy.
  cy.intercept("GET", "**/api/v1/ppt/chat/conversations?presentation_id=*", {
    statusCode: 200,
    body: [],
  }).as("legacyConversations");
}

function interceptCapability(): void {
  cy.intercept("GET", `${WORKSPACE_ROOT}/capabilities*`, (request) => {
    capabilityRequestCount += 1;
    if (capabilityRequestAlias) request.alias = capabilityRequestAlias;
    request.reply({
      statusCode: 200,
      body: capabilityResponse,
    });
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
  cy.intercept(
    "GET",
    `${WORKSPACE_ROOT}/presentations/${PRESENTATION_ID}/events*`,
    (request) => {
      const url = new URL(request.url);
      if (url.searchParams.get("stream") === "1") {
        request.destroy();
        return;
      }
      request.reply({
        statusCode: 200,
        body: { schema_version: 1, events: [], event_high_watermark: cursor },
      });
    },
  );
}

describe("Chat-first Slides Workbench route", () => {
  beforeEach(() => {
    capabilityResponse = enabledCapability;
    capabilityRequestAlias = undefined;
    capabilityRequestCount = 0;
    interceptAuthenticatedShell();
    interceptPresentation();
    interceptCapability();
  });

  afterEach(() => {
    secondaryWorkspaceFrame?.remove();
    secondaryWorkspaceFrame = null;
    secondaryWorkspaceWindow = null;
  });

  it("keeps the timeline and draft mounted when another tab revokes command capability", () => {
    cy.intercept(
      "GET",
      `${WORKSPACE_ROOT}/presentations/${PRESENTATION_ID}/timeline*`,
      {
        statusCode: 200,
        body: {
          schema_version: 1,
          items: [
            firstUserMessage,
            operation({
              operation_type: "export.pdf",
              status: "succeeded",
              progress: 100,
              events: [],
              result: {
                artifact_id: "artifact-read-only",
                artifact_kind: "pdf",
                file_name: "history.pdf",
                format: "pdf",
                expires_at: "2099-08-03T00:00:00Z",
              },
            }),
          ],
          event_high_watermark: "101",
          revision: 7,
          has_more: false,
        },
      },
    ).as("rollbackTimeline");
    cy.intercept(
      "GET",
      `${WORKSPACE_ROOT}/presentations/${PRESENTATION_ID}/events*`,
      (request) => {
        request.destroy();
      },
    );

    visitWorkspace();
    cy.wait(["@presentation", "@workspaceCapability", "@rollbackTimeline"]);
    cy.get('[data-testid="workspace-command-form"]').should("be.visible");
    cy.get('[data-testid="share-popover-trigger"]').should("not.exist");
    cy.get("#workspace-command").type("중단 이후에도 유지할 초안");

    cy.then(() => {
      capabilityRequestAlias = "broadcastDisableCapability";
      capabilityResponse = disabledCapability;
    });

    cy.window().then((window) => {
      const otherTab = new window.BroadcastChannel(
        "presenton-chat-first-capability-v1",
      );
      otherTab.postMessage({
        kind: "capability",
        key: PRESENTATION_ID,
        snapshot: {
          checkedAt: Date.now() + 60_000,
          capability: {
            supported: true,
            enabled: false,
            timeline: true,
            commands: false,
            events: true,
            reason: "chat_first_kill_switch_enabled",
          },
        },
      });
      return new Cypress.Promise<void>((resolve) => {
        window.setTimeout(() => {
          otherTab.close();
          resolve();
        }, 50);
      });
    });

    cy.wait("@broadcastDisableCapability").then(({ response }) => {
      expect(response?.body.commands).to.equal(false);
    });
    cy.get('[data-testid="workspace-command-form"]').should("not.exist");
    cy.get('[data-testid="share-popover-trigger"]').should("not.exist");
    cy.get('[data-testid="legacy-chat-tab"]').should("not.exist");
    cy.get('[data-testid="workspace-timeline-scroll"]').should("be.visible");
    cy.get('[data-operation-id="operation-generate"]').should("be.visible");
    cy.get('[data-testid="artifact-download"]').should("be.enabled");

    // A channel peer can invalidate an authority decision, but it cannot grant
    // commands. The server remains disabled and must win the revalidation.
    cy.then(() => {
      capabilityRequestAlias = "forgedEnableCapability";
    });
    cy.window().then((window) => {
      const otherTab = new window.BroadcastChannel(
        "presenton-chat-first-capability-v1",
      );
      otherTab.postMessage({
        kind: "capability",
        key: PRESENTATION_ID,
        snapshot: {
          checkedAt: Date.now() + 120_000,
          capability: enabledCapability,
        },
      });
      return new Cypress.Promise<void>((resolve) => {
        window.setTimeout(() => {
          otherTab.close();
          resolve();
        }, 50);
      });
    });
    cy.wait("@forgedEnableCapability").then(({ response }) => {
      expect(response?.body.commands).to.equal(false);
    });
    cy.get('[data-testid="workspace-command-form"]').should("not.exist");

    cy.then(() => {
      capabilityRequestAlias = "authoritativeEnableCapability";
      capabilityResponse = enabledCapability;
    });
    cy.window().then((window) => {
      window.dispatchEvent(new window.Event("focus"));
    });
    cy.wait("@authoritativeEnableCapability").then(({ response }) => {
      expect(response?.body.commands).to.equal(true);
    });
    cy.get('[data-testid="workspace-command-form"]').should("be.visible");
    cy.get("#workspace-command").should(
      "have.value",
      "중단 이후에도 유지할 초안",
    );
    cy.get('[data-operation-id="operation-generate"]').should("be.visible");
  });

  it("revalidates command capability across two real browsing contexts without replacing the workspace", () => {
    const rollbackPresentationId = `route-workspace-server-rollback-deck-${Date.now()}`;
    let requestsBeforeDisable = 0;
    let requestsBeforeEnable = 0;
    cy.intercept(
      "GET",
      `**/api/v1/ppt/presentation/${rollbackPresentationId}`,
      {
        statusCode: 200,
        body: { ...presentation, id: rollbackPresentationId },
      },
    ).as("serverRollbackPresentation");
    cy.intercept(
      "GET",
      `${WORKSPACE_ROOT}/presentations/${rollbackPresentationId}/timeline*`,
      {
        statusCode: 200,
        body: {
          schema_version: 1,
          items: [firstUserMessage, operation()],
          event_high_watermark: "201",
          revision: 7,
          has_more: false,
        },
      },
    ).as("serverRollbackTimeline");
    cy.intercept(
      "GET",
      `${WORKSPACE_ROOT}/presentations/${rollbackPresentationId}/events*`,
      (request) => {
        request.destroy();
      },
    );

    cy.visit(`/presentation?id=${rollbackPresentationId}`, {
      onBeforeLoad(window) {
        window.localStorage.clear();
      },
    });
    cy.wait(
      [
        "@serverRollbackPresentation",
        "@workspaceCapability",
        "@serverRollbackTimeline",
      ],
      { timeout: 30_000 },
    );
    cy.get('[data-testid="workspace-command-form"]', {
      timeout: 30_000,
    }).should("be.visible");
    cy.get('[data-testid="share-popover-trigger"]').should("not.exist");

    cy.window().then({ timeout: 31_000 }, (window) => {
      // Cypress does not expose a top-level popup Window in headless Electron.
      // A child browsing context still creates a fully independent Window,
      // Document, React tree, module state, and BroadcastChannel endpoint while
      // remaining directly observable from the same-origin test page.
      secondaryWorkspaceFrame = window.document.createElement("iframe");
      secondaryWorkspaceFrame.title = "Secondary workspace browser context";
      secondaryWorkspaceFrame.name = "workspace-secondary-context";
      secondaryWorkspaceFrame.style.cssText =
        "position:fixed;left:-20000px;top:0;width:1280px;height:720px;border:0";
      secondaryWorkspaceFrame.src = `${window.location.origin}/presentation?id=${rollbackPresentationId}`;
      window.document.body.append(secondaryWorkspaceFrame);
      secondaryWorkspaceWindow = secondaryWorkspaceFrame.contentWindow;
      expect(
        secondaryWorkspaceWindow,
        "secondary browser context",
      ).not.to.equal(null);
      return waitForWindowSelector(
        secondaryWorkspaceWindow as Window,
        '[data-testid="workspace-command-form"]',
        { label: "initial secondary composer mount" },
      );
    });

    cy.then(() => {
      requestsBeforeDisable = capabilityRequestCount;
      capabilityRequestAlias = "serverRollbackDisableCapability";
      capabilityResponse = disabledCapability;
    });
    cy.window().then((window) => {
      // Both independently-rendered pages currently hold the enabled state.
      // A focus return must fetch the authoritative server state and fan the
      // read-only snapshot out to the real secondary browsing context.
      window.dispatchEvent(new window.Event("focus"));
    });

    cy.wait("@serverRollbackDisableCapability").then(({ response }) => {
      expect(response?.body.commands).to.equal(false);
    });
    cy.then(() => {
      expect(capabilityRequestCount).to.be.greaterThan(requestsBeforeDisable);
    });
    cy.get('[data-testid="workspace-command-form"]').should("not.exist");
    cy.get('[data-testid="share-popover-trigger"]').should("not.exist");
    cy.get('[data-testid="workspace-timeline-scroll"]').should("be.visible");
    cy.get('[data-operation-id="operation-generate"]').should("be.visible");
    cy.then({ timeout: 31_000 }, () =>
      waitForWindowSelector(
        secondaryWorkspaceWindow as Window,
        '[data-testid="workspace-command-form"]',
        { present: false, label: "secondary command revoke" },
      ),
    );
    cy.then({ timeout: 31_000 }, () =>
      waitForWindowText(
        secondaryWorkspaceWindow as Window,
        firstUserMessage.content,
        { label: "secondary timeline during revoke" },
      ),
    );

    // Removing the kill switch restores only command authority. Both mounted
    // timelines keep their existing durable records throughout the transition.
    cy.then(() => {
      requestsBeforeEnable = capabilityRequestCount;
      capabilityRequestAlias = "serverRollbackEnableCapability";
      capabilityResponse = enabledCapability;
    });
    cy.window().then((window) => {
      // Exercise the separate bfcache/page-return revalidation path without
      // racing it against a simultaneous focus refresh.
      window.dispatchEvent(new window.Event("pageshow"));
    });
    cy.wait("@serverRollbackEnableCapability").then(({ response }) => {
      expect(response?.body.commands).to.equal(true);
    });
    cy.then(() => {
      expect(capabilityRequestCount).to.be.greaterThan(requestsBeforeEnable);
    });
    cy.get('[data-testid="workspace-command-form"]', {
      timeout: 30_000,
    }).should("be.visible");
    cy.get('[aria-label="초기 생성 요청"]', { timeout: 30_000 })
      .contains(firstUserMessage.content)
      .should("exist");
    cy.then({ timeout: 31_000 }, () =>
      waitForWindowSelector(
        secondaryWorkspaceWindow as Window,
        '[data-testid="workspace-command-form"]',
        { label: "secondary command restore" },
      ),
    );
    cy.then({ timeout: 31_000 }, () => {
      // The required CI contract asserts this exact recovery witness.
      // prettier-ignore
      return waitForWindowText(secondaryWorkspaceWindow as Window, firstUserMessage.content, {
        label: "secondary timeline after restore",
      });
    });
  });

  it("keeps the bootstrap request as the first durable chat message after a browser reload", () => {
    let timelineCalls = 0;
    const timeline = {
      schema_version: 1,
      items: [firstUserMessage, operation()],
      event_high_watermark: "301",
      revision: 7,
      has_more: false,
    };
    cy.intercept(
      "GET",
      `${WORKSPACE_ROOT}/presentations/${PRESENTATION_ID}/timeline*`,
      (request) => {
        timelineCalls += 1;
        request.reply({ statusCode: 200, body: timeline });
      },
    ).as("timeline");
    cy.intercept(
      "GET",
      `${WORKSPACE_ROOT}/presentations/${PRESENTATION_ID}/events*`,
      (request) => {
        request.destroy();
      },
    ).as("workspaceEvents");

    visitWorkspace();
    cy.wait(["@presentation", "@workspaceCapability", "@timeline"]);
    cy.get('[aria-label="프레젠테이션 작업 대화"]').should("be.visible");
    cy.get('[aria-label="초기 생성 요청"]')
      .contains(firstUserMessage.content)
      .should("exist");
    cy.get('[data-testid="attachment-asset-ready"]')
      .scrollIntoView({ block: "center" })
      .should("be.visible")
      .and("contain.text", firstUserMessage.attachments[0].file_name);
    cy.contains("생성 설정").click();
    cy.contains("슬라이드 수").should("be.visible");
    cy.contains("n_slides").should("not.exist");
    cy.contains("must-not-be-rendered").should("not.exist");

    cy.reload();
    // Presentation/capability responses may be retained by the browser during a
    // same-tab reload. The durable timeline itself must always be re-read.
    cy.wait("@timeline");
    cy.get('[aria-label="초기 생성 요청"]')
      .contains(firstUserMessage.content)
      .should("exist");
    cy.wrap(null).then(() => expect(timelineCalls).to.be.greaterThan(1));
  });

  it("rebases the real route's chat timeline after an expired event cursor", () => {
    let timelineCalls = 0;
    let streamCalls = 0;
    cy.intercept(
      "GET",
      `${WORKSPACE_ROOT}/presentations/${PRESENTATION_ID}/timeline*`,
      (request) => {
        timelineCalls += 1;
        request.reply({
          statusCode: 200,
          body:
            timelineCalls === 1
              ? {
                  schema_version: 1,
                  items: [firstUserMessage, operation({ progress: 10 })],
                  event_high_watermark: "401",
                  revision: 7,
                }
              : {
                  schema_version: 1,
                  items: [
                    firstUserMessage,
                    operation({
                      status: "succeeded",
                      progress: 100,
                      result: { summary: "복구된 완료 결과" },
                    }),
                  ],
                  event_high_watermark: "402",
                  revision: 8,
                },
        });
      },
    ).as("rebaseTimeline");
    cy.intercept(
      "GET",
      `${WORKSPACE_ROOT}/presentations/${PRESENTATION_ID}/events*`,
      (request) => {
        const url = new URL(request.url);
        if (url.searchParams.get("stream") !== "1") {
          request.alias = "rebasePoll";
          request.reply({
            statusCode: 200,
            body: {
              schema_version: 1,
              events: [],
              event_high_watermark: "401",
            },
          });
          return;
        }
        request.alias = "rebaseStream";
        streamCalls += 1;
        if (streamCalls === 1) {
          request.reply({
            statusCode: 410,
            body: { code: "event_cursor_expired", message: "cursor expired" },
          });
          return;
        }
        request.destroy();
      },
    ).as("reconnectEvents");

    visitWorkspace();
    cy.wait("@rebaseTimeline");
    cy.wait("@rebasePoll");
    cy.wait("@rebaseStream");
    cy.wait("@rebaseTimeline");
    cy.get('[data-operation-id="operation-generate"]', { timeout: 10_000 })
      .find('[role="progressbar"]')
      .should("have.attr", "aria-valuenow", "100");
    cy.contains("복구된 완료 결과").should("be.visible");
    cy.wrap(null).then(() => expect(timelineCalls).to.be.greaterThan(1));
  });

  it("keeps generation completion and a following edit in one route-level timeline", () => {
    cy.viewport(1440, 900);
    interceptReadyPresentation();
    const commandBodies: Array<Record<string, unknown>> = [];
    const generated = operation({
      status: "succeeded",
      progress: 100,
      result: {
        summary: "첫 슬라이드를 게시했습니다.",
        revision: 7,
        slide_count: 1,
      },
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
    cy.intercept(
      "GET",
      `${WORKSPACE_ROOT}/presentations/${PRESENTATION_ID}/timeline*`,
      {
        statusCode: 200,
        body: {
          schema_version: 1,
          items: [firstUserMessage, generated],
          event_high_watermark: "501",
          revision: 7,
          has_more: false,
        },
      },
    ).as("generatedTimeline");
    interceptIdleWorkspaceEvents("501");
    cy.intercept(
      "POST",
      `${WORKSPACE_ROOT}/presentations/${PRESENTATION_ID}/commands`,
      (request) => {
        commandBodies.push(request.body as Record<string, unknown>);
        request.reply({
          statusCode: 200,
          body: {
            schema_version: 1,
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
            event_high_watermark: "502",
            revision: 8,
            has_more: false,
          },
        });
      },
    ).as("editCommand");

    visitWorkspace();
    cy.wait(["@readyPresentation", "@generatedTimeline"]);
    cy.contains("첫 슬라이드를 게시했습니다.").should("be.visible");
    cy.get("#workspace-command").type("제목을 더 짧게 다듬어 줘{enter}");
    cy.wait("@editCommand").then(() => {
      expect(commandBodies).to.have.length(1);
      expect(commandBodies[0]).to.include({
        command_type: "presentation.edit",
        expected_revision: 7,
      });
      expect(commandBodies[0]).to.have.nested.property("context.scope", "all");
      expect(commandBodies[0]).not.to.have.nested.property(
        "context.current_slide",
      );
    });
    cy.get('[data-operation-id="operation-edit"]')
      .should("have.attr", "data-operation-status", "succeeded")
      .and("contain.text", "제목을 간결하게 수정했습니다.");
  });

  it("routes an authored Fidelity PPTX export through chat and marks its immutable artifact stale", () => {
    cy.viewport(1440, 900);
    interceptReadyPresentation(
      readyAuthoredPresentation,
      "readyAuthoredPresentation",
    );
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
    cy.intercept(
      "GET",
      `${WORKSPACE_ROOT}/presentations/${PRESENTATION_ID}/timeline*`,
      {
        statusCode: 200,
        body: {
          schema_version: 1,
          items: [
            firstUserMessage,
            operation({ status: "succeeded", progress: 100 }),
          ],
          event_high_watermark: "601",
          revision: 7,
          has_more: false,
        },
      },
    ).as("exportTimeline");
    interceptIdleWorkspaceEvents("601");
    cy.intercept(
      "POST",
      `${WORKSPACE_ROOT}/presentations/${PRESENTATION_ID}/commands`,
      (request) => {
        commandBodies.push(request.body as Record<string, unknown>);
        request.reply({
          statusCode: 200,
          body: {
            schema_version: 1,
            items: [firstUserMessage, exportCard],
            event_high_watermark: "602",
            revision: 8,
            has_more: false,
          },
        });
      },
    ).as("exportCommand");
    cy.intercept(
      "GET",
      `${WORKSPACE_ROOT}/artifacts/artifact-route-1/download`,
      {
        statusCode: 200,
        headers: {
          "content-type":
            "application/vnd.openxmlformats-officedocument.presentationml.presentation",
          "content-disposition": 'attachment; filename="ax-strategy.pptx"',
        },
        body: "route-fixture-pptx",
      },
    ).as("artifactDownload");

    visitWorkspace();
    cy.wait(["@readyAuthoredPresentation", "@exportTimeline"]);
    cy.get('[data-testid="export-trigger"]').click();
    cy.get('[data-testid="export-pptx"]').click();
    cy.wait("@exportCommand").then(() => {
      expect(commandBodies[0]).to.include({
        command_type: "export.pptx",
        expected_revision: 7,
      });
      expect(commandBodies[0]).to.have.nested.property(
        "context.format",
        "pptx",
      );
      expect(commandBodies[0]).to.have.nested.property(
        "context.pptx_mode",
        "fidelity",
      );
      expect(commandBodies[0]).to.have.nested.property(
        "context.font_embedding",
        false,
      );
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
    cy.intercept(
      "GET",
      `${WORKSPACE_ROOT}/presentations/${PRESENTATION_ID}/timeline*`,
      {
        statusCode: 200,
        body: {
          schema_version: 1,
          items: [firstUserMessage, approvalCard()],
          event_high_watermark: "701",
          revision: 7,
          has_more: false,
        },
      },
    ).as("approvalTimeline");
    interceptIdleWorkspaceEvents("701");
    cy.intercept(
      "POST",
      `${WORKSPACE_ROOT}/operations/operation-regenerate/actions`,
      (request) => {
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
            schema_version: 1,
            items: [firstUserMessage, approvalCard()],
            event_high_watermark: state === "running" ? "702" : "703",
            revision: 7,
            has_more: false,
          },
        });
      },
    ).as("operationAction");

    visitWorkspace();
    cy.wait("@approvalTimeline");
    cy.get('[data-operation-id="operation-regenerate"]').within(() => {
      cy.get('[data-testid="operation-approval-alert"]')
        .should("have.attr", "role", "alert")
        .and("have.attr", "aria-live", "assertive");
      // Exactly one polite card-status announcer prevents repeated progress
      // announcements from turning a long-running operation into screen-reader chatter.
      cy.get('[role="status"][aria-live="polite"]').should("have.length", 1);
    });
    cy.readFile("node_modules/axe-core/axe.min.js", "utf8")
      .then((source: string) =>
        cy.window().then((window) => {
          // Load the published browser bundle into the application window. An
          // axe-core ESM import is evaluated in Cypress' runner frame and can
          // either audit that frame or retain CommonJS `exports` references.
          window.Function(source)();
          const pageAxe = (window as typeof window & { axe?: AxeRuntime }).axe;
          if (!pageAxe)
            throw new Error(
              "axe-core did not attach to the application window",
            );
          return pageAxe.run(window.document);
        }),
      )
      .then((results) => {
        const violationSummary = results.violations
          .map(
            (violation) =>
              `${violation.id}: ${violation.nodes
                .map((node) => node.target.join(" > "))
                .join(", ")}`,
          )
          .join("\n");
        expect(results.violations, violationSummary).to.have.length(0);
      });
    cy.get(
      '[data-operation-id="operation-regenerate"] [data-testid="operation-approve"]',
    ).click();
    cy.wait("@operationAction");
    cy.get('[data-operation-id="operation-regenerate"]')
      .should("have.attr", "data-operation-status", "running")
      .find('[data-testid="operation-cancel"]')
      .click();
    cy.wait("@operationAction");
    cy.get('[data-operation-id="operation-regenerate"]').should(
      "have.attr",
      "data-operation-status",
      "cancelled",
    );
    cy.wrap(null).then(() => {
      expect(actionBodies.map((body) => body.action)).to.deep.equal([
        "approve",
        "cancel",
      ]);
      expect(
        actionBodies.map((body) => body.expected_operation_version),
      ).to.deep.equal([3, 4]);
      expect(actionBodies[0].idempotency_key).not.to.equal(
        actionBodies[1].idempotency_key,
      );
    });
  });

  it("deduplicates a poll/stream race while advancing the same operation card", () => {
    let pollCalls = 0;
    let streamCalls = 0;
    const progressEvent = {
      schema_version: 1,
      id: "event-race-progress",
      operation_id: "operation-generate",
      operation_version: 4,
      sequence: 3,
      cursor: "802",
      type: "progress",
      status: "running",
      message: "레이스 진행 이벤트",
      progress: 60,
    };
    cy.intercept(
      "GET",
      `${WORKSPACE_ROOT}/presentations/${PRESENTATION_ID}/timeline*`,
      {
        statusCode: 200,
        body: {
          schema_version: 1,
          items: [firstUserMessage, operation({ progress: 10 })],
          event_high_watermark: "801",
          revision: 7,
          has_more: false,
        },
      },
    ).as("raceTimeline");
    cy.intercept(
      "GET",
      `${WORKSPACE_ROOT}/presentations/${PRESENTATION_ID}/events*`,
      (request) => {
        const url = new URL(request.url);
        if (url.searchParams.get("stream") !== "1") {
          request.alias = "racePoll";
          pollCalls += 1;
          request.reply({
            statusCode: 200,
            body: {
              schema_version: 1,
              events: pollCalls === 1 ? [progressEvent] : [],
              event_high_watermark: "802",
            },
          });
          return;
        }
        request.alias = "raceStream";
        streamCalls += 1;
        if (streamCalls === 1) {
          request.reply({
            statusCode: 200,
            headers: { "content-type": "text/event-stream" },
            body: `event: workspace_event\nid: 802\ndata: ${JSON.stringify(progressEvent)}\n\n`,
          });
          return;
        }
        request.destroy();
      },
    ).as("raceEvents");

    visitWorkspace();
    cy.wait("@raceTimeline");
    cy.wait("@racePoll");
    cy.wait("@raceStream");
    cy.get(
      '[data-operation-id="operation-generate"] [role="progressbar"]',
    ).should("have.attr", "aria-valuenow", "60");
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
    cy.intercept(
      "GET",
      `${WORKSPACE_ROOT}/presentations/${PRESENTATION_ID}/timeline*`,
      (request) => {
        request.reply({
          statusCode: 200,
          body: {
            schema_version: 1,
            items: [firstUserMessage],
            event_high_watermark: `${timelineRevision}`,
            revision: timelineRevision,
            has_more: false,
          },
        });
      },
    ).as("retryTimeline");
    interceptIdleWorkspaceEvents("7");
    cy.intercept(
      "POST",
      `${WORKSPACE_ROOT}/presentations/${PRESENTATION_ID}/commands`,
      (request) => {
        const body = request.body as Record<string, unknown>;
        if (body.command === "모호한 제출") {
          ambiguousBodies.push(body);
          if (ambiguousBodies.length === 1) {
            request.reply({
              statusCode: 503,
              body: {
                error: {
                  code: "gateway_unavailable",
                  safe_message: "Try again.",
                },
              },
            });
            return;
          }
          request.reply({
            statusCode: 200,
            body: {
              schema_version: 1,
              items: [firstUserMessage],
              revision: 7,
              has_more: false,
            },
          });
          return;
        }
        conflictBodies.push(body);
        if (conflictBodies.length === 1) {
          timelineRevision = 8;
          request.reply({
            statusCode: 409,
            body: {
              error: {
                code: "revision_conflict",
                safe_message: "The deck changed.",
              },
            },
          });
          return;
        }
        request.reply({
          statusCode: 200,
          body: {
            schema_version: 1,
            items: [firstUserMessage],
            revision: 8,
            has_more: false,
          },
        });
      },
    ).as("retryCommand");

    visitWorkspace();
    cy.wait("@retryTimeline");
    cy.get("#workspace-command").type("모호한 제출{enter}");
    cy.wait("@retryCommand");
    cy.get('[data-testid="workspace-retry-intent"]').click();
    cy.wait("@retryCommand").then(() => {
      expect(ambiguousBodies).to.have.length(2);
      expect(ambiguousBodies[1].idempotency_key).to.equal(
        ambiguousBodies[0].idempotency_key,
      );
      expect(ambiguousBodies[1].expected_revision).to.equal(
        ambiguousBodies[0].expected_revision,
      );
    });

    cy.get("#workspace-command").type("다른 탭 이후 수정{enter}");
    cy.wait("@retryCommand");
    cy.wait("@retryTimeline");
    cy.wrap(null).then(() => expect(conflictBodies).to.have.length(1));
    cy.get('[data-testid="workspace-retry-intent"]').click();
    cy.wait("@retryCommand").then(() => {
      expect(conflictBodies).to.have.length(2);
      expect(conflictBodies[1].idempotency_key).to.equal(
        conflictBodies[0].idempotency_key,
      );
      expect(conflictBodies[0].expected_revision).to.equal(7);
      expect(conflictBodies[1].expected_revision).to.equal(8);
    });
  });

  it("keeps generation in a full-width chat workspace at mobile, tablet, and desktop sizes", () => {
    cy.intercept(
      "GET",
      `${WORKSPACE_ROOT}/presentations/${PRESENTATION_ID}/timeline*`,
      {
        statusCode: 200,
        body: {
          schema_version: 1,
          items: [firstUserMessage, operation()],
          event_high_watermark: "901",
          revision: 7,
          has_more: false,
        },
      },
    ).as("responsiveTimeline");
    cy.intercept(
      "GET",
      `${WORKSPACE_ROOT}/presentations/${PRESENTATION_ID}/events*`,
      (request) => {
        request.destroy();
      },
    );

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

      cy.get('[data-testid="responsive-chat-tab"]').should("not.exist");
      cy.get('[data-testid="responsive-slides-tab"]').should("not.exist");
      cy.get('[data-testid="workspace-drawer-toggle"]').should("not.exist");
      cy.get('[data-testid="workspace-pane-separator"]').should("not.exist");
      cy.get("#presentation-slides-pane").should("not.exist");

      cy.get('[data-testid="workspace-chat-pane"]')
        .should("be.visible")
        .and(($pane) => {
          const bounds = $pane[0].getBoundingClientRect();
          const parentBounds = $pane[0].parentElement?.getBoundingClientRect();
          expect(parentBounds, `${viewport.name} workspace container`).not.to
            .equal(undefined);
          expect(
            bounds.width,
            `${viewport.name} chat width`,
          ).to.be.greaterThan(0);
          expect(
            bounds.height,
            `${viewport.name} workspace height`,
          ).to.be.greaterThan(0);
          expect(
            bounds.left,
            `${viewport.name} workspace left edge`,
          ).to.be.at.least(-0.5);
          expect(
            bounds.right,
            `${viewport.name} chat right edge`,
          ).to.be.at.most(viewport.width + 0.5);
          expect(
            Math.abs(bounds.width - (parentBounds?.width ?? 0)),
            `${viewport.name} chat fills result area`,
          ).to.be.lessThan(1);
        });
      cy.get('[data-testid="workspace-command-form"]').should(($composer) => {
        const bounds = $composer[0].getBoundingClientRect();
        expect(
          bounds.left,
          `${viewport.name} composer left edge`,
        ).to.be.at.least(-0.5);
        expect(
          bounds.right,
          `${viewport.name} composer right edge`,
        ).to.be.at.most(viewport.width + 0.5);
      });
      cy.get('[aria-label="초기 생성 요청"]').should("be.visible");
      cy.get('[role="progressbar"][aria-label="작업 진행률"]')
        .should("be.visible")
        .and("have.attr", "aria-valuemin", "0")
        .and("have.attr", "aria-valuemax", "100")
        .and("have.attr", "aria-valuenow", "42");
    }
  });

  it("shows compact PPT/chat tabs below 968px and a left-PPT/right-chat split once a result is ready", () => {
    interceptReadyPresentation();
    cy.intercept(
      "GET",
      `${WORKSPACE_ROOT}/presentations/${PRESENTATION_ID}/timeline*`,
      {
        statusCode: 200,
        body: {
          schema_version: 1,
          items: [
            firstUserMessage,
            operation({ status: "succeeded", progress: 100, events: [] }),
          ],
          event_high_watermark: "902",
          revision: 7,
          has_more: false,
        },
      },
    ).as("readyResponsiveTimeline");
    interceptIdleWorkspaceEvents("902");

    cy.viewport(960, 768);
    visitWorkspace();
    cy.wait([
      "@readyPresentation",
      "@workspaceCapability",
      "@readyResponsiveTimeline",
    ]);

    cy.get('[data-testid="responsive-slides-tab"]').should("be.visible").click();
    cy.get("#presentation-slides-pane").should("be.visible");
    cy.get('[data-testid="workspace-chat-pane"]').should("not.be.visible");
    cy.get('[data-testid="responsive-chat-tab"]').should("be.visible").click();
    cy.get('[data-testid="workspace-chat-pane"]').should("be.visible");
    cy.get("#presentation-slides-pane").should("not.be.visible");
    cy.get('[data-testid="workspace-pane-separator"]').should("not.exist");

    cy.viewport(1024, 768);
    cy.get('[data-testid="workspace-pane-separator"]', {
      timeout: 10_000,
    }).should("be.visible");
    cy.get('[data-testid="responsive-slides-tab"]').should("not.exist");
    cy.get('[data-testid="responsive-chat-tab"]').should("not.exist");
    cy.get('[data-testid="workspace-drawer-toggle"]')
      .should("be.visible")
      .and("have.attr", "aria-expanded", "true");
    cy.get("#presentation-slides-pane").should("be.visible");
    cy.get('[data-testid="workspace-chat-pane"]').should("be.visible");
    cy.get("#presentation-slides-pane").then(($slides) => {
      cy.get('[data-testid="workspace-chat-pane"]').then(($chat) => {
        const slidesBounds = $slides[0].getBoundingClientRect();
        const chatBounds = $chat[0].getBoundingClientRect();
        expect(slidesBounds.left, "PPT left edge").to.be.lessThan(
          chatBounds.left,
        );
        expect(slidesBounds.right, "PPT ends before chat").to.be.at.most(
          chatBounds.left,
        );
      });
    });
  });
});
