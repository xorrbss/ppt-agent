import React from "react";

import WorkspaceChatPanel from "./WorkspaceChatPanel";
import { WorkspaceOperationCard } from "./WorkspaceOperationCard";

const PRESENTATION_ID = "workspace-deck";
const ROOT = "**/api/v1/ppt/workspace";

type TimelineBody = {
  items: unknown[];
  event_high_watermark?: string;
  has_more?: boolean;
  revision?: number;
};

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

const installWorkspaceApi = (timeline: TimelineBody) => {
  cy.intercept("GET", `${ROOT}/presentations/${PRESENTATION_ID}/timeline*`, {
    statusCode: 200,
    body: timeline,
  }).as("timeline");
  cy.intercept("GET", `${ROOT}/presentations/${PRESENTATION_ID}/events*`, (request) => {
    const url = new URL(request.url);
    if (url.searchParams.get("stream") === "1") {
      // A transport failure drives the hook's reconnect path without a finished
      // fixture SSE response causing an immediate reconnect loop.
      request.destroy();
      return;
    }
    request.reply({ statusCode: 200, body: { events: [], event_high_watermark: timeline.event_high_watermark } });
  }).as("workspaceEvents");
};

describe("WorkspaceChatPanel", () => {
  it("renders a dedicated durable result for version restore", () => {
    cy.mount(
      <WorkspaceOperationCard
        operation={{
          anchorType: "operation",
          id: "operation-version-restore",
          operationId: "operation-version-restore",
          position: 1,
          operationType: "version.restore",
          title: "버전 복원",
          status: "succeeded",
          progress: 100,
          result: {
            kind: "version.restore",
            restored_version_id: "version-7",
            source_revision: 11,
            revision: 12,
            slide_count: 7,
          },
          events: [],
        }}
        currentRevision={12}
        onAction={() => undefined}
      />,
    );

    cy.get('[data-testid="version-restore-result"]')
      .should("contain.text", "버전 복원이 완료되었습니다.")
      .and("contain.text", "리비전 11 → 12")
      .and("contain.text", "7장")
      .and("contain.text", "대상 버전 version-7");
  });

  it("expires a visible artifact at its TTL boundary without waiting for a server response", () => {
    cy.clock(new Date("2026-08-03T00:00:00Z").getTime());
    const reExport = cy.stub().as("reExport");
    cy.mount(
      <WorkspaceOperationCard
        operation={{
          anchorType: "operation",
          id: "operation-ttl",
          operationId: "operation-ttl",
          position: 1,
          operationType: "export.pdf",
          title: "PDF 내보내기",
          status: "succeeded",
          result: {
            artifact_id: "artifact-ttl",
            format: "pdf",
            source_revision: 4,
            expires_at: "2026-08-03T00:00:01Z",
          },
          events: [],
        }}
        currentRevision={4}
        onAction={() => undefined}
        onReExport={reExport}
      />,
    );

    cy.get('[data-testid="artifact-download"]').should("be.enabled");
    cy.get('[data-testid="artifact-expired"]').should("not.exist");
    cy.tick(1_001);
    cy.get('[data-testid="artifact-download"]').should("be.disabled");
    cy.get('[data-testid="artifact-expired"]').should("be.visible");
    cy.get('[data-testid="artifact-reexport"]').click();
    cy.get("@reExport").should("have.been.calledOnceWith", "export.pdf");
  });

  it("keeps the durable first user message, attachments, and safe generation settings visible", () => {
    installWorkspaceApi({
      items: [firstUserMessage, operation()],
      event_high_watermark: "cursor-2",
      revision: 7,
      has_more: false,
    });

    cy.mount(<WorkspaceChatPanel presentationId={PRESENTATION_ID} />);
    cy.wait("@timeline");

    cy.get('[aria-label="사용자 메시지"]').contains(firstUserMessage.content).should("be.visible");
    cy.get('[aria-label="첨부 파일"]').within(() => {
      cy.contains("현황 분석.pdf").should("be.visible");
      cy.contains("준비됨").should("be.visible");
      cy.contains("추진 계획.xlsx").should("be.visible");
      cy.contains("처리 중").should("be.visible");
    });
    cy.contains("생성 설정").click();
    cy.contains("n_slides").should("be.visible");
    cy.contains("10").should("be.visible");
    cy.contains("api_key").should("not.exist");
    cy.contains("must-not-be-rendered").should("not.exist");
  });

  it("preserves the visible message position when older history is prepended", () => {
    const message = (id: string, position: number, label: string) => ({
      anchor_type: "message",
      id,
      position,
      role: "user",
      content: `${label}\n${Array.from({ length: 5 }, (_, index) => `세부 기록 ${index + 1}`).join("\n")}`,
      attachments: [],
      generation_settings: {},
    });
    const recentMessages = Array.from({ length: 8 }, (_, index) =>
      message(`message-recent-${index}`, 100 + index, index === 0 ? "recent-anchor" : `recent-${index}`),
    );
    const olderMessages = Array.from({ length: 10 }, (_, index) =>
      message(`message-older-${index}`, 90 + index, `older-${index}`),
    );

    cy.intercept("GET", `${ROOT}/presentations/${PRESENTATION_ID}/timeline*`, (request) => {
      const url = new URL(request.url);
      if (url.searchParams.has("before_position")) {
        request.reply({
          statusCode: 200,
          body: {
            items: olderMessages,
            event_high_watermark: "cursor-history-2",
            next_before_position: 90,
            revision: 7,
            has_more: false,
          },
        });
        return;
      }
      request.reply({
        statusCode: 200,
        body: {
          items: recentMessages,
          event_high_watermark: "cursor-history-1",
          next_before_position: 100,
          revision: 7,
          has_more: true,
        },
      });
    }).as("paginatedTimeline");
    cy.intercept("GET", `${ROOT}/presentations/${PRESENTATION_ID}/events*`, (request) => {
      request.destroy();
    });

    cy.mount(
      <div className="h-[360px]">
        <WorkspaceChatPanel presentationId={PRESENTATION_ID} />
      </div>,
    );
    cy.wait("@paginatedTimeline");
    cy.get('[data-testid="workspace-timeline-scroll"]')
      .then(($scroll) => {
        $scroll[0].style.height = "240px";
        $scroll[0].style.overflowY = "auto";
      })
      .scrollTo("top");

    let anchorOffset = 0;
    cy.contains('[aria-label="사용자 메시지"]', "recent-anchor").then(($anchor) => {
      cy.get('[data-testid="workspace-timeline-scroll"]').then(($scroll) => {
        anchorOffset = $anchor[0].getBoundingClientRect().top - $scroll[0].getBoundingClientRect().top;
      });
    });
    cy.contains("button", "이전 기록 불러오기").click();
    cy.wait("@paginatedTimeline");

    cy.contains('[aria-label="사용자 메시지"]', "recent-anchor").should(($anchor) => {
      const scroll = Cypress.$('[data-testid="workspace-timeline-scroll"]')[0];
      const nextOffset = $anchor[0].getBoundingClientRect().top - scroll.getBoundingClientRect().top;
      expect(nextOffset).to.be.closeTo(anchorOffset, 2);
      expect(scroll.scrollTop).to.be.greaterThan(0);
      expect(scroll.scrollTop).to.be.lessThan(scroll.scrollHeight - scroll.clientHeight);
    });
    cy.contains("older-0").should("exist");
  });

  it("follows event-only card growth at the bottom and preserves an intentionally scrolled-up viewport", () => {
    const messages = Array.from({ length: 9 }, (_, index) => ({
      anchor_type: "message",
      id: `message-scroll-${index}`,
      position: index + 1,
      role: "user",
      content: `Timeline message ${index + 1}\nAdditional context for scroll behavior.`,
      attachments: [],
      generation_settings: {},
    }));
    const firstProgressEvent = {
      id: "event-card-growth-1",
      operation_id: "operation-generate",
      operation_version: 4,
      sequence: 3,
      type: "progress",
      status: "running",
      message: Array.from(
        { length: 8 },
        (_, index) => `Detailed progress update ${index + 1} remains inside the existing operation card.`,
      ).join(" "),
      progress: 60,
    };
    const secondProgressEvent = {
      ...firstProgressEvent,
      id: "event-card-growth-2",
      operation_version: 5,
      sequence: 4,
      message: Array.from(
        { length: 12 },
        (_, index) => `Later progress update ${index + 1} must not move a reader who scrolled upward.`,
      ).join(" "),
      progress: 80,
    };
    let eventPollCalls = 0;
    let eventStreamCalls = 0;

    cy.intercept("GET", `${ROOT}/presentations/${PRESENTATION_ID}/timeline*`, {
      statusCode: 200,
      body: {
        items: [...messages, operation({ position: 20 })],
        event_high_watermark: "cursor-card-growth-0",
        revision: 7,
        has_more: false,
      },
    }).as("cardGrowthTimeline");
    cy.intercept("GET", `${ROOT}/presentations/${PRESENTATION_ID}/events*`, (request) => {
      const url = new URL(request.url);
      if (url.searchParams.get("stream") === "1") {
        eventStreamCalls += 1;
        if (eventStreamCalls === 1) {
          request.reply({
            delay: 1_200,
            statusCode: 200,
            headers: { "content-type": "text/event-stream" },
            body: `id: cursor-card-growth-2\ndata: ${JSON.stringify(secondProgressEvent)}\n\n`,
          });
          return;
        }
        request.destroy();
        return;
      }
      eventPollCalls += 1;
      if (eventPollCalls === 1) {
        request.reply({
          delay: 1_200,
          statusCode: 200,
          body: {
            events: [firstProgressEvent],
            event_high_watermark: "cursor-card-growth-1",
          },
        });
        return;
      }
      request.reply({
        statusCode: 200,
        body: { events: [], event_high_watermark: "cursor-card-growth-2" },
      });
    });

    cy.mount(
      <div style={{ height: "360px", width: "360px" }}>
        <WorkspaceChatPanel presentationId={PRESENTATION_ID} />
      </div>,
    );
    cy.wait("@cardGrowthTimeline");
    cy.get('[data-testid="workspace-timeline-scroll"]').then(($scroll) => {
      const scroll = $scroll[0];
      scroll.style.height = "240px";
      scroll.style.overflowY = "auto";
      scroll.scrollTop = scroll.scrollHeight;
      scroll.dispatchEvent(new Event("scroll"));
    });
    cy.get('[data-operation-id="operation-generate"] [role="progressbar"]')
      .should("have.attr", "aria-valuenow", "60");
    cy.get('[data-testid="workspace-timeline-scroll"]').should(($scroll) => {
      const scroll = $scroll[0];
      expect(scroll.scrollHeight - scroll.scrollTop - scroll.clientHeight).to.be.closeTo(0, 2);
    });

    cy.get('[data-testid="workspace-timeline-scroll"]').then(($scroll) => {
      const scroll = $scroll[0];
      scroll.scrollTop = 0;
      scroll.dispatchEvent(new Event("scroll"));
    });
    cy.get('[data-operation-id="operation-generate"] [role="progressbar"]')
      .should("have.attr", "aria-valuenow", "80");
    cy.get('[data-testid="workspace-timeline-scroll"]').should(($scroll) => {
      expect($scroll[0].scrollTop).to.be.closeTo(0, 1);
    });
  });

  it("renders live progress, durable result and error states, and sends approval/cancel/retry actions", () => {
    const actionBodies: unknown[] = [];
    const cards = [
      operation(),
      operation({
        id: "operation-success",
        operation_id: "operation-success",
        position: 30,
        status: "succeeded",
        progress: 100,
        result: { summary: "10개 슬라이드를 게시했습니다.", revision: 8, slide_count: 10 },
      }),
      operation({
        id: "operation-failed",
        operation_id: "operation-failed",
        position: 40,
        status: "failed",
        error: "안전하게 다시 시도할 수 있습니다.",
      }),
      operation({
        id: "operation-approval",
        operation_id: "operation-approval",
        position: 50,
        status: "approval_required",
      }),
    ];
    const timeline = { items: [firstUserMessage, ...cards], event_high_watermark: "cursor-4", revision: 8 };
    installWorkspaceApi(timeline);
    cy.intercept("POST", `${ROOT}/operations/*/actions`, (request) => {
      actionBodies.push(request.body);
      request.reply({ statusCode: 200, body: timeline });
    }).as("operationAction");

    cy.mount(<WorkspaceChatPanel presentationId={PRESENTATION_ID} />);
    cy.wait("@timeline");

    cy.get('[data-operation-id="operation-generate"]')
      .find('[role="progressbar"]')
      .should("have.attr", "aria-valuenow", "42");
    cy.contains("슬라이드 레이아웃을 작성하고 있습니다.").should("be.visible");
    cy.contains("10개 슬라이드를 게시했습니다.").should("be.visible");
    cy.contains("revision: 8").should("be.visible");
    cy.contains("안전하게 다시 시도할 수 있습니다.").should("be.visible");

    cy.get('[data-operation-id="operation-approval"]').contains("button", "승인").click();
    cy.wait("@operationAction");
    cy.get('[data-operation-id="operation-approval"]').contains("button", "취소").click();
    cy.wait("@operationAction");
    cy.get('[data-operation-id="operation-failed"]').contains("button", "다시 시도").click();
    cy.wait("@operationAction").then(() => {
      expect(actionBodies.map((body) => (body as { action: string }).action)).to.deep.equal([
        "approve",
        "cancel",
        "retry",
      ]);
    });
  });

  it("hides retry and explains deterministic non-retryable operation failures", () => {
    installWorkspaceApi({
      items: [
        firstUserMessage,
        operation({
          id: "operation-non-retryable",
          operation_id: "operation-non-retryable",
          position: 30,
          status: "failed",
          error: "generation_admission_rejected",
          error_message: "생성 요청을 실행할 수 없습니다.",
          retryable: false,
          events: [],
        }),
      ],
      event_high_watermark: "cursor-non-retryable",
      revision: 8,
      has_more: false,
    });

    cy.mount(<WorkspaceChatPanel presentationId={PRESENTATION_ID} />);
    cy.wait("@timeline");
    cy.get('[data-operation-id="operation-non-retryable"]').within(() => {
      cy.get('[data-testid="operation-retry-unavailable"]')
        .should("be.visible")
        .and("contain.text", "이 오류는 재시도할 수 없습니다");
      cy.contains("button", "다시 시도").should("not.exist");
    });
  });

  it("restores quality findings and immutable export artifacts with a stale revision warning", () => {
    installWorkspaceApi({
      items: [
        firstUserMessage,
        operation({
          id: "operation-quality",
          operation_id: "operation-quality",
          operation_type: "quality.review",
          title: "고품질 검수",
          position: 30,
          status: "succeeded",
          progress: 100,
          events: [],
          result: {
            kind: "quality.review",
            mode: "analyze_only",
            inspected_revision: 11,
            reviewed_count: 10,
            issue_slide_count: 2,
            remaining_issue_count: 2,
            message: "읽기 전용 품질 검토가 완료되었습니다.",
            remaining_issues: [
              { slide_number: 3, message: "본문 대비를 확인하세요." },
            ],
          },
        }),
        operation({
          id: "operation-export",
          operation_id: "operation-export",
          operation_type: "export.pptx",
          title: "PPTX 내보내기",
          position: 40,
          status: "succeeded",
          progress: 100,
          events: [],
          result: {
            artifact_id: "artifact-1",
            artifact_kind: "pptx",
            artifact_sha256: "abc123",
            artifact_size_bytes: 1234,
            format: "pptx",
            source_revision: 11,
            expires_at: "2099-08-03T00:00:00Z",
          },
        }),
      ],
      event_high_watermark: "cursor-artifacts",
      revision: 12,
      has_more: false,
    });

    const commandBodies: unknown[] = [];
    cy.intercept("GET", `${ROOT}/artifacts/artifact-1/download`, {
      statusCode: 410,
      body: { code: "workspace_export_expired", message: "expired" },
    }).as("artifactDownload");
    cy.intercept("POST", `${ROOT}/presentations/${PRESENTATION_ID}/commands`, (request) => {
      commandBodies.push(request.body);
      request.reply({
        statusCode: 200,
        body: { items: [], event_high_watermark: "cursor-artifacts", revision: 12 },
      });
    }).as("workspaceCommand");

    cy.mount(<WorkspaceChatPanel presentationId={PRESENTATION_ID} />);
    cy.wait("@timeline");

    cy.get('[data-testid="quality-review-result"]')
      .should("contain.text", "검토 10장")
      .and("contain.text", "이슈 슬라이드 2장")
      .and("contain.text", "검토 리비전 11")
      .and("contain.text", "3번 슬라이드 · 본문 대비를 확인하세요.");
    cy.get('[data-testid="export-artifact-result"]')
      .should("contain.text", "presentation.pptx")
      .and("contain.text", "소스 리비전 11")
      .within(() => {
        cy.get('[data-testid="artifact-download"]').should("be.enabled").click();
        cy.get('[data-testid="artifact-stale"]').should("be.visible");
      });
    cy.wait("@artifactDownload");
    cy.get('[data-testid="artifact-expired"]').should("be.visible");
    cy.get('[data-testid="artifact-reexport"]').click();
    cy.wait("@workspaceCommand").then(() => {
      expect(commandBodies[0]).to.include({
        command_type: "export.pptx",
        expected_revision: 12,
      });
      expect(commandBodies[0]).to.have.nested.property("context.format", "pptx");
    });
  });

  it("remains read-only when command capability is disabled", () => {
    installWorkspaceApi({
      items: [
        firstUserMessage,
        operation({ id: "operation-approval", operation_id: "operation-approval", status: "awaiting_approval" }),
        operation({ id: "operation-failure", operation_id: "operation-failure", status: "failed" }),
      ],
      event_high_watermark: "cursor-5",
    });

    cy.mount(<WorkspaceChatPanel presentationId={PRESENTATION_ID} commandsEnabled={false} />);
    cy.wait("@timeline");

    cy.get("#workspace-command").should("be.disabled");
    cy.get('[data-operation-id="operation-approval"]').contains("button", "승인").should("be.disabled");
    cy.get('[data-operation-id="operation-approval"]').contains("button", "취소").should("be.disabled");
    cy.get('[data-operation-id="operation-failure"]').contains("button", "다시 시도").should("be.disabled");
    cy.get('[aria-label="프레젠테이션 작업 대화"]').should("exist");
  });

  it("rebases the timeline after an expired stream cursor and displays the recovered operation card", () => {
    let timelineCalls = 0;
    let streamCalls = 0;
    cy.intercept("GET", `${ROOT}/presentations/${PRESENTATION_ID}/timeline*`, (request) => {
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
    cy.intercept("GET", `${ROOT}/presentations/${PRESENTATION_ID}/events*`, (request) => {
      const url = new URL(request.url);
      if (url.searchParams.get("stream") !== "1") {
        request.reply({ statusCode: 200, body: { events: [], event_high_watermark: "cursor-old" } });
        return;
      }
      streamCalls += 1;
      if (streamCalls === 1) {
        request.reply({
          statusCode: 410,
          body: { code: "event_cursor_expired", message: "cursor expired" },
        });
        return;
      }
      request.destroy();
    }).as("reconnectEvents");

    cy.mount(<WorkspaceChatPanel presentationId={PRESENTATION_ID} />);
    cy.wait("@rebaseTimeline");
    cy.wait("@reconnectEvents");
    cy.get('[data-operation-id="operation-generate"]', { timeout: 10_000 })
      .find('[role="progressbar"]')
      .should("have.attr", "aria-valuenow", "100");
    cy.contains("복구된 완료 결과").should("be.visible");
    cy.wrap(null).then(() => expect(timelineCalls).to.be.greaterThan(1));
  });

  it("reuses the same idempotency key when an ambiguous command is explicitly retried", () => {
    const commandBodies: Array<Record<string, unknown>> = [];
    const timeline = {
      items: [firstUserMessage],
      event_high_watermark: "cursor-ambiguous",
      revision: 7,
      has_more: false,
    };
    installWorkspaceApi(timeline);
    cy.intercept("POST", `${ROOT}/presentations/${PRESENTATION_ID}/commands`, (request) => {
      commandBodies.push(request.body as Record<string, unknown>);
      if (commandBodies.length === 1) {
        // A 5xx response is ambiguous from the workbench's perspective: the
        // durable command may have committed before the gateway failed.
        request.reply({
          statusCode: 503,
          body: { error: { code: "gateway_unavailable", safe_message: "Try again." } },
        });
        return;
      }
      request.reply({ statusCode: 200, body: timeline });
    }).as("workspaceCommand");

    cy.mount(<WorkspaceChatPanel presentationId={PRESENTATION_ID} />);
    cy.wait("@timeline");
    cy.get("#workspace-command").type("Make the title shorter{enter}");
    cy.wait("@workspaceCommand");
    cy.get('[data-testid="workspace-retry-intent"]').should("be.visible").click();
    cy.wait("@workspaceCommand").then(() => {
      expect(commandBodies).to.have.length(2);
      expect(commandBodies[1].idempotency_key).to.equal(commandBodies[0].idempotency_key);
      expect(commandBodies[1].command).to.equal(commandBodies[0].command);
      expect(commandBodies[1].expected_revision).to.equal(commandBodies[0].expected_revision);
    });
    cy.get('[data-testid="workspace-retry-intent"]').should("not.exist");
  });

  it("rebases a stale revision but never replays until the user explicitly retries", () => {
    const commandBodies: Array<Record<string, unknown>> = [];
    let timelineCalls = 0;
    cy.intercept("GET", `${ROOT}/presentations/${PRESENTATION_ID}/timeline*`, (request) => {
      timelineCalls += 1;
      request.reply({
        statusCode: 200,
        body: {
          items: [firstUserMessage],
          event_high_watermark: `cursor-revision-${timelineCalls}`,
          revision: timelineCalls === 1 ? 7 : 8,
          has_more: false,
        },
      });
    }).as("conflictTimeline");
    cy.intercept("GET", `${ROOT}/presentations/${PRESENTATION_ID}/events*`, (request) => {
      const url = new URL(request.url);
      if (url.searchParams.get("stream") === "1") {
        request.destroy();
        return;
      }
      request.reply({ statusCode: 200, body: { events: [], event_high_watermark: "cursor-revision-1" } });
    });
    cy.intercept("POST", `${ROOT}/presentations/${PRESENTATION_ID}/commands`, (request) => {
      commandBodies.push(request.body as Record<string, unknown>);
      if (commandBodies.length === 1) {
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
    }).as("conflictingCommand");

    cy.mount(<WorkspaceChatPanel presentationId={PRESENTATION_ID} />);
    cy.wait("@conflictTimeline");
    cy.get("#workspace-command").type("Use the latest revenue numbers{enter}");
    cy.wait("@conflictingCommand");
    cy.wait("@conflictTimeline");
    cy.wrap(null).then(() => expect(commandBodies).to.have.length(1));
    cy.contains("Use the latest revenue numbers").should("be.visible");
    cy.get('[data-testid="workspace-retry-intent"]').click();
    cy.wait("@conflictingCommand").then(() => {
      expect(commandBodies).to.have.length(2);
      expect(commandBodies[1].idempotency_key).to.equal(commandBodies[0].idempotency_key);
      expect(commandBodies[0].expected_revision).to.equal(7);
      expect(commandBodies[1].expected_revision).to.equal(8);
    });
  });

  it("disables an expired approval without disabling cancellation", () => {
    installWorkspaceApi({
      items: [
        firstUserMessage,
        operation({
          id: "operation-expired-approval",
          operation_id: "operation-expired-approval",
          status: "approval_required",
          approval_expires_at: "2020-01-01T00:00:00Z",
        }),
      ],
      event_high_watermark: "cursor-expired-approval",
      revision: 9,
      has_more: false,
    });

    cy.mount(<WorkspaceChatPanel presentationId={PRESENTATION_ID} />);
    cy.wait("@timeline");
    cy.get('[data-operation-id="operation-expired-approval"]').within(() => {
      cy.get('[data-testid="operation-approve"]').should("be.disabled");
      cy.contains("승인 기한이 만료되었습니다").should("be.visible");
      cy.get('[data-testid="operation-cancel"]').should("not.be.disabled");
    });
  });
});
