import React from "react";

import { WorkspaceApiError } from "../../services/api/workspace";
import WorkspaceChatPanel, {
  parseQualityReviewSlideRange,
} from "./WorkspaceChatPanel";
import { WorkspaceMessage } from "./WorkspaceMessage";
import { WorkspaceOperationCard } from "./WorkspaceOperationCard";

const PRESENTATION_ID = "workspace-deck";
const ROOT = "**/api/v1/ppt/workspace";

type TimelineBody = {
  items: unknown[];
  schema_version?: number;
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
      : (status === "failed" || status === "dead_letter") &&
          overrides.retryable !== false
        ? [
            {
              id: "retry",
              label: "다시 시도",
              variant: "secondary",
              enabled: true,
            },
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
    allowed_actions: allowedActions,
    ...overrides,
  };
};

const installWorkspaceApi = (
  timeline: TimelineBody,
  options: { omitSchemaVersion?: boolean } = {},
) => {
  const timelineBody = options.omitSchemaVersion
    ? timeline
    : { schema_version: 1, ...timeline };
  cy.intercept("GET", `${ROOT}/presentations/${PRESENTATION_ID}/timeline*`, {
    statusCode: 200,
    body: timelineBody,
  }).as("timeline");
  cy.intercept(
    "GET",
    `${ROOT}/presentations/${PRESENTATION_ID}/events*`,
    (request) => {
      const url = new URL(request.url);
      if (url.searchParams.get("stream") === "1") {
        // A transport failure drives the hook's reconnect path without a finished
        // fixture SSE response causing an immediate reconnect loop.
        request.destroy();
        return;
      }
      request.reply({
        statusCode: 200,
        body: {
          schema_version: 1,
          events: [],
          event_high_watermark: timeline.event_high_watermark,
        },
      });
    },
  ).as("workspaceEvents");
};

describe("WorkspaceChatPanel", () => {
  it("normalizes specified review pages and rejects invalid, duplicate, reversed, or out-of-range input", () => {
    expect(parseQualityReviewSlideRange("3, 5-8, 12", 12)).to.deep.equal({
      indices: [2, 4, 5, 6, 7, 11],
      normalized: "3, 5-8, 12",
    });
    expect(() => parseQualityReviewSlideRange("3, 3", 12)).to.throw();
    expect(() => parseQualityReviewSlideRange("8-5", 12)).to.throw();
    expect(() => parseQualityReviewSlideRange("13", 12)).to.throw();
    expect(() => parseQualityReviewSlideRange("abc", 12)).to.throw();
  });

  it("shows a safe history failure and lets the user explicitly retry", () => {
    let timelineCalls = 0;
    cy.intercept(
      "GET",
      `${ROOT}/presentations/${PRESENTATION_ID}/timeline*`,
      (request) => {
        timelineCalls += 1;
        if (timelineCalls === 1) {
          request.reply({
            statusCode: 503,
            body: {
              error: {
                code: "timeline_unavailable",
                safe_message: "대화 기록을 잠시 불러올 수 없습니다.",
                retryable: true,
              },
              message: "database connection details must stay hidden",
            },
          });
          return;
        }
        request.reply({
          statusCode: 200,
          body: {
            schema_version: 1,
            items: [firstUserMessage],
            event_high_watermark: "cursor-history-recovered",
            revision: 7,
            has_more: false,
          },
        });
      },
    ).as("historyTimeline");
    cy.intercept(
      "GET",
      `${ROOT}/presentations/${PRESENTATION_ID}/events*`,
      (request) => request.destroy(),
    );

    cy.mount(<WorkspaceChatPanel presentationId={PRESENTATION_ID} />);
    cy.wait("@historyTimeline");
    cy.get('[data-testid="workspace-history-error"]')
      .should("be.visible")
      .and("contain.text", "대화 기록을 잠시 불러올 수 없습니다.")
      .and("not.contain.text", "database connection details");
    cy.get("#workspace-command").should("be.disabled");
    cy.contains("기록 다시 불러오기").click();
    cy.wait("@historyTimeline");
    cy.get('[data-testid="workspace-history-error"]').should("not.exist");
    cy.contains(firstUserMessage.content).should("be.visible");
  });

  it("renders immutable attachment snapshots, live lifecycle state, and only safe ready links", () => {
    const onReattach = cy.stub().as("onReattach");
    cy.mount(
      <WorkspaceMessage
        onReattach={onReattach}
        message={{
          anchorType: "message",
          id: "message-assets",
          position: 1,
          role: "user",
          content: "첨부 파일 상태를 확인해 줘",
          attachments: [
            {
              id: "ready",
              name: "ready.pdf",
              status: "ready",
              stateAtTime: "ready",
              currentState: "ready",
              mimeType: "application/pdf",
              size: 2_048,
              previewUrl: "https://files.example.test/ready",
              downloadUrl: "/api/v1/ppt/workspace/assets/ready/download",
            },
            {
              id: "failed",
              name: "failed.csv",
              status: "failed",
              stateAtTime: "processing",
              currentState: "failed",
              error: "파일 내용을 읽지 못했습니다.",
            },
            {
              id: "quarantined",
              name: "unsafe.zip",
              status: "quarantined",
              stateAtTime: "uploaded",
              currentState: "quarantined",
            },
            {
              id: "expired",
              name: "old.pptx",
              status: "expired",
              stateAtTime: "ready",
              currentState: "expired",
            },
            {
              id: "deleted",
              name: "gone.docx",
              status: "deleted",
              stateAtTime: "deleted",
              currentState: "deleted",
            },
            {
              id: "unsafe-link",
              name: "blocked.pdf",
              status: "ready",
              stateAtTime: "ready",
              currentState: "ready",
              previewUrl: "javascript:alert(document.cookie)",
              downloadUrl: "data:text/html,unsafe",
            },
          ],
          generationSettings: { custom_mode: "careful" },
        }}
      />,
    );

    cy.get('[data-testid="attachment-ready"]')
      .should("contain.text", "application/pdf")
      .and("contain.text", "2.0 KB")
      .and("contain.text", "당시 준비됨")
      .within(() => {
        cy.contains("미리보기").should(
          "have.attr",
          "href",
          "https://files.example.test/ready",
        );
        cy.contains("다운로드").should(
          "have.attr",
          "href",
          "/api/v1/ppt/workspace/assets/ready/download",
        );
      });
    cy.get('[data-testid="attachment-failed"]')
      .should("contain.text", "당시 처리 중")
      .and("contain.text", "현재 처리 실패")
      .and("contain.text", "파일 내용을 읽지 못했습니다.")
      .and("contain.text", "다시 첨부해 주세요");
    cy.get('[data-testid="attachment-failed"]')
      .contains("button", "파일 다시 첨부")
      .click();
    cy.get("@onReattach").should("have.been.calledWith", "failed");
    cy.get('[data-testid="attachment-quarantined"]')
      .should("contain.text", "현재 격리됨")
      .and("contain.text", "다른 안전한 파일을 첨부해 주세요");
    cy.get('[data-testid="attachment-expired"]')
      .should("contain.text", "당시 준비됨")
      .and("contain.text", "현재 만료됨")
      .and("contain.text", "파일을 다시 첨부해 주세요");
    cy.get('[data-testid="attachment-deleted"]')
      .should("contain.text", "당시 삭제됨")
      .and("contain.text", "삭제된 파일입니다");
    cy.get('[data-testid="attachment-unsafe-link"]').within(() => {
      cy.contains("미리보기").should("not.exist");
      cy.contains("다운로드").should("not.exist");
    });
    cy.contains("생성 설정").click();
    cy.contains("Custom mode").should("be.visible");
    cy.contains("custom_mode").should("not.exist");
  });

  it("renders operation actor, time, target, revisions, activity, and disabled action reason", () => {
    cy.mount(
      <WorkspaceOperationCard
        operation={{
          anchorType: "operation",
          id: "operation-audit",
          operationId: "operation-audit",
          position: 1,
          operationType: "slide.edit",
          title: "슬라이드 수정",
          status: "succeeded",
          actor: { type: "worker", id: "worker-7", label: "렌더 작업자" },
          target: { slideIds: ["slide-1"], elementIds: ["element-2"] },
          expectedRevision: 4,
          resultRevision: 5,
          activitySummary: "슬라이드 1장을 수정했습니다.",
          createdAt: "2026-08-03T01:00:00Z",
          updatedAt: "2026-08-03T01:01:00Z",
          events: [],
          allowedActions: [
            {
              id: "undo",
              label: "되돌리기",
              variant: "secondary",
              enabled: false,
              reason: "이 작업은 되돌리기를 지원하지 않습니다.",
            },
          ],
        }}
        currentRevision={5}
        onAction={() => undefined}
      />,
    );

    cy.get('[data-testid="operation-metadata"]')
      .should("contain.text", "렌더 작업자 · worker-7")
      .and("contain.text", "슬라이드 ID slide-1")
      .and("contain.text", "요소 ID element-2")
      .and("contain.text", "4 → 5")
      .and("contain.text", "슬라이드 1장을 수정했습니다.")
      .and("contain.text", "2026");
    cy.get('[data-testid="operation-undo"]').should("be.disabled");
    cy.get('[data-testid="operation-undo-reason"]')
      .should("be.visible")
      .and("contain.text", "되돌리기를 지원하지 않습니다");
  });

  it("does not interpret result fields from an unknown operation schema", () => {
    cy.mount(
      <WorkspaceOperationCard
        operation={{
          anchorType: "operation",
          id: "operation-future",
          operationId: "operation-future",
          position: 1,
          operationType: "future.contract.v9",
          title: "보존된 미래 작업",
          status: "succeeded",
          result: { secret_future_sentinel: "must-not-be-rendered" },
          events: [],
          allowedActions: [],
        }}
        onAction={() => undefined}
      />,
    );

    cy.contains("지원되지 않는 작업 상태").should("be.visible");
    cy.contains("must-not-be-rendered").should("not.exist");
    cy.contains("Secret future sentinel").should("not.exist");
  });

  it("reports canvas refresh success or failure only from the client refresh state", () => {
    const qualityOperation = operation({
      id: "operation-quality-refresh",
      operation_id: "operation-quality-refresh",
      operation_type: "quality.review",
      title: "고품질 검수",
      status: "succeeded",
      progress: 100,
      events: [],
      result: {
        kind: "quality.review",
        fixed_count: 1,
        fixed_slide_indices: [1],
        revision: 12,
      },
    });

    cy.mount(
      <WorkspaceOperationCard
        operation={qualityOperation}
        onAction={cy.stub()}
        presentationRefreshState="refreshed"
      />,
    );
    cy.get('[data-testid="quality-review-result"]').should(
      "contain.text",
      "PowerPoint 화면에 자동으로 반영했습니다.",
    );

    cy.mount(
      <WorkspaceOperationCard
        operation={qualityOperation}
        onAction={cy.stub()}
        presentationRefreshState="failed"
      />,
    );
    cy.get('[data-testid="quality-review-result"]').should(
      "contain.text",
      "PowerPoint 화면을 자동 갱신하지 못했습니다.",
    );
  });

  it("keeps quality review below complete while the durable result is saving", () => {
    cy.mount(
      <WorkspaceOperationCard
        operation={operation({
          id: "operation-quality-saving",
          operation_id: "operation-quality-saving",
          operation_type: "quality.review",
          title: "고품질 검수",
          status: "running",
          progress: 96,
          events: [],
          result: { kind: "quality.review" },
        })}
        onAction={cy.stub()}
      />,
    );

    cy.get('[role="progressbar"]')
      .should("have.attr", "aria-valuenow", "99")
      .and("have.attr", "aria-valuetext", "결과와 수정본 리비전을 저장 중");
    cy.contains("결과 저장 중").should("be.visible");
  });

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
          allowedActions: [
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
          ],
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

  it("renders template generation progress and keeps the new deck behind an explicit link", () => {
    cy.mount(
      <WorkspaceOperationCard
        operation={{
          anchorType: "operation",
          id: "operation-template-apply",
          operationId: "operation-template-apply",
          position: 1,
          operationType: "template.apply",
          title: "AI 저작 템플릿 변경",
          status: "running",
          progress: 48,
          events: [],
          allowedActions: [
            {
              id: "cancel",
              label: "취소",
              variant: "secondary",
              enabled: true,
            },
          ],
        }}
        currentRevision={11}
        onAction={() => undefined}
      />,
    );

    cy.get('[data-testid="template-apply-progress"]')
      .should("be.visible")
      .and("contain.text", "새 자료를 생성하고 있습니다")
      .and("contain.text", "원본 자료는 그대로 유지됩니다");

    cy.mount(
      <WorkspaceOperationCard
        operation={{
          anchorType: "operation",
          id: "operation-template-result",
          operationId: "operation-template-result",
          position: 2,
          operationType: "template.apply",
          title: "AI 저작 템플릿 변경",
          status: "succeeded",
          progress: 100,
          result: {
            kind: "template.apply",
            message: "새 AI 저작 자료가 준비되었습니다.",
            presentation_id: "new-deck-7",
          },
          events: [],
          allowedActions: [],
        }}
        currentRevision={11}
        onAction={() => undefined}
      />,
    );

    cy.get('[data-testid="template-apply-result"]')
      .should("contain.text", "새 AI 저작 자료가 준비되었습니다")
      .and("contain.text", "원본 자료는 변경되지 않았습니다");
    cy.get('[data-testid="template-apply-open-result"]')
      .should("have.attr", "href", "/presentation?id=new-deck-7&type=standard")
      .and("contain.text", "새 자료 열기");
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
          allowedActions: [
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
          ],
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

  it("contains rejected card actions and renders only their safe public message", () => {
    const retry = cy
      .stub()
      .rejects(
        new WorkspaceApiError(
          "이 작업은 현재 다시 시도할 수 없습니다.",
          409,
          "operation_state_conflict",
          false,
        ),
      )
      .as("retry");
    cy.mount(
      <WorkspaceOperationCard
        operation={{
          anchorType: "operation",
          id: "operation-rejected-retry",
          operationId: "operation-rejected-retry",
          position: 1,
          operationType: "slide.edit",
          title: "슬라이드 수정",
          status: "failed",
          events: [],
          allowedActions: [
            {
              id: "retry",
              label: "서버의 신뢰하지 않는 레이블",
              variant: "primary",
              enabled: true,
            },
          ],
        }}
        onAction={retry}
      />,
    );

    cy.get('[data-testid="operation-retry"]').click();
    cy.get("@retry").should(
      "have.been.calledOnceWith",
      "operation-rejected-retry",
      "retry",
    );
    cy.get('[data-testid="operation-action-error"]')
      .should("be.visible")
      .and("contain.text", "이 작업은 현재 다시 시도할 수 없습니다.");
  });

  it("contains rejected re-exports without exposing an internal error message", () => {
    const reExport = cy
      .stub()
      .rejects(new Error("provider credential and stack must stay hidden"))
      .as("reExport");
    cy.mount(
      <WorkspaceOperationCard
        operation={{
          anchorType: "operation",
          id: "operation-expired-export",
          operationId: "operation-expired-export",
          position: 1,
          operationType: "export.pdf",
          title: "PDF 내보내기",
          status: "succeeded",
          result: {
            artifact_id: "artifact-expired",
            format: "pdf",
            source_revision: 4,
            expires_at: "2020-01-01T00:00:00Z",
          },
          events: [],
          allowedActions: [
            {
              id: "retry",
              label: "다시 내보내기",
              variant: "secondary",
              enabled: true,
            },
          ],
        }}
        currentRevision={4}
        onAction={() => undefined}
        onReExport={reExport}
      />,
    );

    cy.get('[data-testid="artifact-reexport"]').click();
    cy.get("@reExport").should("have.been.calledOnceWith", "export.pdf");
    cy.get('[data-testid="operation-action-error"]')
      .should("be.visible")
      .and("contain.text", "다시 내보내기를 시작할 수 없습니다.")
      .and("not.contain.text", "provider credential");
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

    cy.get('[aria-label="사용자 메시지"]')
      .contains(firstUserMessage.content)
      .should("be.visible");
    cy.get('[aria-label="첨부 파일"]').within(() => {
      cy.contains("현황 분석.pdf").should("be.visible");
      cy.contains("준비됨").should("be.visible");
      cy.contains("추진 계획.xlsx").should("be.visible");
      cy.contains("처리 중").should("be.visible");
    });
    cy.contains("생성 설정").click();
    cy.contains("슬라이드 수").should("be.visible");
    cy.contains("n_slides").should("not.exist");
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
      message(
        `message-recent-${index}`,
        100 + index,
        index === 0 ? "recent-anchor" : `recent-${index}`,
      ),
    );
    const olderMessages = Array.from({ length: 10 }, (_, index) =>
      message(`message-older-${index}`, 90 + index, `older-${index}`),
    );

    cy.intercept(
      "GET",
      `${ROOT}/presentations/${PRESENTATION_ID}/timeline*`,
      (request) => {
        const url = new URL(request.url);
        if (url.searchParams.has("before_position")) {
          request.reply({
            statusCode: 200,
            body: {
              schema_version: 1,
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
            schema_version: 1,
            items: recentMessages,
            event_high_watermark: "cursor-history-1",
            next_before_position: 100,
            revision: 7,
            has_more: true,
          },
        });
      },
    ).as("paginatedTimeline");
    cy.intercept(
      "GET",
      `${ROOT}/presentations/${PRESENTATION_ID}/events*`,
      (request) => {
        request.destroy();
      },
    );

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
    cy.contains('[aria-label="사용자 메시지"]', "recent-anchor").then(
      ($anchor) => {
        cy.get('[data-testid="workspace-timeline-scroll"]').then(($scroll) => {
          anchorOffset =
            $anchor[0].getBoundingClientRect().top -
            $scroll[0].getBoundingClientRect().top;
        });
      },
    );
    cy.contains("button", "이전 기록 불러오기").click();
    cy.wait("@paginatedTimeline");

    cy.contains('[aria-label="사용자 메시지"]', "recent-anchor").should(
      ($anchor) => {
        const scroll = Cypress.$(
          '[data-testid="workspace-timeline-scroll"]',
        )[0];
        const nextOffset =
          $anchor[0].getBoundingClientRect().top -
          scroll.getBoundingClientRect().top;
        expect(nextOffset).to.be.closeTo(anchorOffset, 2);
        expect(scroll.scrollTop).to.be.greaterThan(0);
        expect(scroll.scrollTop).to.be.lessThan(
          scroll.scrollHeight - scroll.clientHeight,
        );
      },
    );
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
      schema_version: 1,
      id: "event-card-growth-1",
      operation_id: "operation-generate",
      operation_version: 4,
      sequence: 3,
      type: "progress",
      status: "running",
      message: Array.from(
        { length: 8 },
        (_, index) =>
          `Detailed progress update ${index + 1} remains inside the existing operation card.`,
      ).join(" "),
      progress: 60,
      cursor: "101",
    };
    const secondProgressEvent = {
      ...firstProgressEvent,
      id: "event-card-growth-2",
      operation_version: 5,
      sequence: 4,
      cursor: "102",
      message: Array.from(
        { length: 12 },
        (_, index) =>
          `Later progress update ${index + 1} must not move a reader who scrolled upward.`,
      ).join(" "),
      progress: 80,
    };
    let eventPollCalls = 0;
    let eventStreamCalls = 0;

    cy.intercept("GET", `${ROOT}/presentations/${PRESENTATION_ID}/timeline*`, {
      statusCode: 200,
      body: {
        schema_version: 1,
        items: [...messages, operation({ position: 20 })],
        event_high_watermark: "100",
        revision: 7,
        has_more: false,
      },
    }).as("cardGrowthTimeline");
    cy.intercept(
      "GET",
      `${ROOT}/presentations/${PRESENTATION_ID}/events*`,
      (request) => {
        const url = new URL(request.url);
        if (url.searchParams.get("stream") === "1") {
          eventStreamCalls += 1;
          if (eventStreamCalls === 1) {
            request.reply({
              delay: 1_200,
              statusCode: 200,
              headers: { "content-type": "text/event-stream" },
              body: `event: workspace_event\nid: 102\ndata: ${JSON.stringify(secondProgressEvent)}\n\n`,
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
              schema_version: 1,
              events: [firstProgressEvent],
              event_high_watermark: "101",
            },
          });
          return;
        }
        request.reply({
          statusCode: 200,
          body: {
            schema_version: 1,
            events: [],
            event_high_watermark: "102",
          },
        });
      },
    );

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
    cy.get(
      '[data-operation-id="operation-generate"] [role="progressbar"]',
    ).should("have.attr", "aria-valuenow", "60");
    cy.get('[data-testid="workspace-timeline-scroll"]').should(($scroll) => {
      const scroll = $scroll[0];
      expect(
        scroll.scrollHeight - scroll.scrollTop - scroll.clientHeight,
      ).to.be.closeTo(0, 2);
    });

    cy.get('[data-testid="workspace-timeline-scroll"]').then(($scroll) => {
      const scroll = $scroll[0];
      scroll.scrollTop = 0;
      scroll.dispatchEvent(new Event("scroll"));
    });
    cy.get(
      '[data-operation-id="operation-generate"] [role="progressbar"]',
    ).should("have.attr", "aria-valuenow", "80");
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
        result: {
          summary: "10개 슬라이드를 게시했습니다.",
          revision: 8,
          slide_count: 10,
        },
      }),
      operation({
        id: "operation-failed",
        operation_id: "operation-failed",
        position: 40,
        status: "failed",
        safe_error: "안전하게 다시 시도할 수 있습니다.",
      }),
      operation({
        id: "operation-approval",
        operation_id: "operation-approval",
        position: 50,
        status: "approval_required",
      }),
    ];
    const timeline = {
      schema_version: 1,
      items: [firstUserMessage, ...cards],
      event_high_watermark: "cursor-4",
      revision: 8,
    };
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
    cy.contains("리비전: 8").should("be.visible");
    cy.contains("안전하게 다시 시도할 수 있습니다.").should("be.visible");

    cy.get('[data-operation-id="operation-approval"]')
      .contains("button", "승인")
      .click();
    cy.wait("@operationAction");
    cy.get('[data-operation-id="operation-approval"]')
      .contains("button", "취소")
      .click();
    cy.wait("@operationAction");
    cy.get('[data-operation-id="operation-failed"]')
      .contains("button", "다시 시도")
      .click();
    cy.wait("@operationAction").then(() => {
      expect(
        actionBodies.map((body) => (body as { action: string }).action),
      ).to.deep.equal(["approve", "cancel", "retry"]);
    });
    cy.get(
      '[data-operation-id="operation-approval"] [data-testid="operation-approval-alert"]',
    )
      .should("have.attr", "role", "alert")
      .and("have.attr", "aria-live", "assertive");
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
          safe_message: "생성 요청을 실행할 수 없습니다.",
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
      cy.contains("generation_admission_rejected").should("not.exist");
    });
  });

  it("opens the composer picker from a failed attachment card", () => {
    installWorkspaceApi({
      items: [
        {
          ...firstUserMessage,
          attachments: [
            {
              asset_id: "asset-failed-picker",
              file_name: "failed.pdf",
              state_at_time: "processing",
              current_state: "failed",
            },
          ],
        },
      ],
      event_high_watermark: "cursor-picker",
      revision: 1,
      has_more: false,
    });
    cy.mount(<WorkspaceChatPanel presentationId={PRESENTATION_ID} />);
    cy.wait("@timeline");
    cy.get('input[aria-label="첨부할 파일 선택"]').then(($input) => {
      cy.stub($input[0], "click").as("filePickerClick");
    });
    cy.get('[data-testid="attachment-asset-failed-picker"]')
      .contains("button", "파일 다시 첨부")
      .click();
    cy.get("@filePickerClick").should("have.been.calledOnce");
  });

  it("projects a deleted live asset row from the immutable backend snapshot", () => {
    installWorkspaceApi({
      items: [
        {
          ...firstUserMessage,
          attachments: [
            {
              asset_id: "asset-deleted-row",
              filename: "removed.pdf",
              mime_type: "application/pdf",
              size_bytes: 4_096,
              status: "ready",
              current_state: null,
              unavailable_reason: "workspace_asset_deleted",
              safe_failure: {
                failure_code: "workspace_asset_deleted",
                safe_message: "이 첨부 파일은 삭제되어 다시 첨부해야 합니다.",
                retryable: false,
              },
              download_url: null,
              preview_url: null,
            },
          ],
        },
      ],
      event_high_watermark: "cursor-deleted-row",
      revision: 1,
      has_more: false,
    });
    cy.mount(<WorkspaceChatPanel presentationId={PRESENTATION_ID} />);
    cy.wait("@timeline");

    cy.get('[data-testid="attachment-asset-deleted-row"]')
      .should("contain.text", "당시 준비됨")
      .and("contain.text", "현재 삭제됨")
      .and("contain.text", "이 첨부 파일은 삭제되어 다시 첨부해야 합니다.")
      .within(() => {
        cy.contains("button", "파일 다시 첨부").should("be.enabled");
        cy.contains("다운로드").should("not.exist");
        cy.contains("미리보기").should("not.exist");
      });
  });

  it("uploads selected files and sends their asset IDs with the command", () => {
    const commandBodies: Array<Record<string, unknown>> = [];
    const timeline = {
      schema_version: 1,
      items: [firstUserMessage],
      event_high_watermark: "cursor-upload",
      revision: 7,
      has_more: false,
    };
    installWorkspaceApi(timeline);
    cy.intercept("POST", `${ROOT}/assets`, {
      statusCode: 200,
      body: {
        schema_version: 1,
        asset_id: "12345678-1234-1234-1234-123456789abc",
        file_name: "evidence.pdf",
        media_type: "application/pdf",
        size_bytes: 8,
        status: "uploaded",
        version: 0,
      },
    }).as("assetUpload");
    cy.intercept(
      "POST",
      `${ROOT}/presentations/${PRESENTATION_ID}/commands`,
      (request) => {
        commandBodies.push(request.body as Record<string, unknown>);
        request.reply({ statusCode: 200, body: timeline });
      },
    ).as("workspaceCommandWithAsset");

    cy.mount(<WorkspaceChatPanel presentationId={PRESENTATION_ID} />);
    cy.wait("@timeline");
    cy.get('input[aria-label="첨부할 파일 선택"]').selectFile(
      {
        contents: Cypress.Buffer.from("evidence"),
        fileName: "evidence.pdf",
        mimeType: "application/pdf",
      },
      { force: true },
    );
    cy.wait("@assetUpload");
    cy.get('[aria-label="전송할 첨부 파일"]').should(
      "contain.text",
      "evidence.pdf",
    );
    cy.get("#workspace-command").type("이 자료를 반영해 줘{enter}");
    cy.wait("@workspaceCommandWithAsset").then(() => {
      expect(commandBodies[0].asset_ids).to.deep.equal([
        "12345678-1234-1234-1234-123456789abc",
      ]);
    });
    cy.get('[aria-label="전송할 첨부 파일"]').should("not.exist");
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
            inspected_revision: 11,
            reviewed_count: 10,
            issue_slide_count: 2,
            fixed_count: 2,
            fixed_pages: [3, 5],
            remaining_issue_count: 1,
            revision: 12,
            message: "고품질 검수와 자동 수정이 완료되었습니다.",
            remaining_issues: [
              { slide_number: 3, message: "본문 대비를 확인하세요." },
            ],
            issues: [
              {
                slide_number: 3,
                type: "contrast",
                severity: "high",
                description: "본문 대비를 확인하세요.",
                action: "본문 색상을 조정했습니다.",
                applied: true,
              },
            ],
            slide_results: [
              {
                slide_number: 3,
                action: "본문 색상을 조정했습니다.",
                applied: true,
              },
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
    cy.intercept(
      "POST",
      `${ROOT}/presentations/${PRESENTATION_ID}/commands`,
      (request) => {
        commandBodies.push(request.body);
        request.reply({
          statusCode: 200,
          body: {
            schema_version: 1,
            items: [],
            event_high_watermark: "cursor-artifacts",
            revision: 12,
          },
        });
      },
    ).as("workspaceCommand");

    const focusSlide = cy.stub().as("focusSlide");
    cy.mount(
      <WorkspaceChatPanel
        presentationId={PRESENTATION_ID}
        onSlideFocus={focusSlide}
      />,
    );
    cy.wait("@timeline");

    // The persisted card must stay useful after a reload, including its safe page history.
    cy.get('[data-testid="quality-review-result"]')
      .should("contain.text", "검토 페이지")
      .and("contain.text", "10장")
      .and("contain.text", "수정된 슬라이드: 3, 5번")
      .and("contain.text", "저장 리비전")
      .and("contain.text", "PowerPoint 화면 자동 갱신을 요청")
      .and("contain.text", "페이지별 검수 이력")
      .and("contain.text", "이슈 유형")
      .and("contain.text", "수정 조치")
      .and("contain.text", "적용됨");
    cy.get('[data-testid="quality-review-focus-slide-3"]').click();
    cy.get("@focusSlide").should("have.been.calledWith", 2);
    cy.get('[data-testid="export-artifact-result"]')
      .should("contain.text", "presentation.pptx")
      .and("contain.text", "소스 리비전 11")
      .within(() => {
        cy.get('[data-testid="artifact-download"]')
          .should("be.enabled")
          .click();
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
      expect(commandBodies[0]).to.have.nested.property(
        "context.format",
        "pptx",
      );
    });
  });

  it("remains read-only when command capability is disabled", () => {
    installWorkspaceApi({
      items: [
        firstUserMessage,
        operation({
          id: "operation-approval",
          operation_id: "operation-approval",
          status: "awaiting_approval",
        }),
        operation({
          id: "operation-failure",
          operation_id: "operation-failure",
          status: "failed",
        }),
        operation({
          id: "operation-read-only-export",
          operation_id: "operation-read-only-export",
          operation_type: "export.pptx",
          status: "succeeded",
          progress: 100,
          result: {
            artifact_id: "artifact-read-only",
            format: "pptx",
            source_revision: 7,
            expires_at: "2099-08-03T00:00:00Z",
          },
        }),
      ],
      event_high_watermark: "cursor-5",
    });

    cy.mount(
      <WorkspaceChatPanel
        presentationId={PRESENTATION_ID}
        commandsEnabled={false}
      />,
    );
    cy.wait("@timeline");

    cy.get('[data-testid="workspace-command-form"]').should("not.exist");
    cy.get('[data-operation-id="operation-approval"]')
      .contains("button", "승인")
      .should("be.disabled");
    cy.get('[data-operation-id="operation-approval"]')
      .contains("button", "취소")
      .should("be.disabled");
    cy.get('[data-operation-id="operation-failure"]')
      .contains("button", "다시 시도")
      .should("be.disabled");
    cy.get('[data-operation-id="operation-read-only-export"]')
      .find('[data-testid="artifact-download"]')
      .should("be.enabled");
    cy.get('[data-operation-id="operation-read-only-export"]')
      .find('[data-testid="artifact-reexport"]')
      .should("not.exist");
    cy.get('[aria-label="프레젠테이션 작업 대화"]').should("exist");
  });

  it("fails closed for writes when the timeline envelope has no schema version", () => {
    installWorkspaceApi(
      {
        items: [
          firstUserMessage,
          operation({
            id: "operation-schema-less",
            operation_id: "operation-schema-less",
            status: "awaiting_approval",
          }),
        ],
        event_high_watermark: "cursor-schema-less",
      },
      { omitSchemaVersion: true },
    );

    cy.mount(
      <WorkspaceChatPanel presentationId={PRESENTATION_ID} commandsEnabled />,
    );
    cy.wait("@timeline");

    cy.get('[data-testid="workspace-schema-read-only"]').should("be.visible");
    cy.get("#workspace-command").should("be.disabled");
    cy.get('[data-operation-id="operation-schema-less"]')
      .should("be.visible")
      .find("button")
      .should("not.exist");
  });

  it("rebases the timeline after an expired stream cursor and displays the recovered operation card", () => {
    let timelineCalls = 0;
    let streamCalls = 0;
    cy.intercept(
      "GET",
      `${ROOT}/presentations/${PRESENTATION_ID}/timeline*`,
      (request) => {
        timelineCalls += 1;
        request.reply({
          statusCode: 200,
          body:
            timelineCalls === 1
              ? {
                  schema_version: 1,
                  items: [firstUserMessage, operation({ progress: 10 })],
                  event_high_watermark: "10",
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
                  event_high_watermark: "20",
                  revision: 8,
                },
        });
      },
    ).as("rebaseTimeline");
    cy.intercept(
      "GET",
      `${ROOT}/presentations/${PRESENTATION_ID}/events*`,
      (request) => {
        const url = new URL(request.url);
        if (url.searchParams.get("stream") !== "1") {
          request.reply({
            statusCode: 200,
            body: {
              schema_version: 1,
              events: [],
              event_high_watermark: "10",
            },
          });
          return;
        }
        streamCalls += 1;
        if (streamCalls === 1) {
          request.reply({
            statusCode: 410,
            body: {
              error: {
                code: "event_cursor_expired",
                safe_message: "이벤트 기록을 다시 불러옵니다.",
                retryable: true,
              },
            },
          });
          return;
        }
        request.destroy();
      },
    ).as("reconnectEvents");

    cy.mount(<WorkspaceChatPanel presentationId={PRESENTATION_ID} />);
    cy.wait("@rebaseTimeline");
    cy.wait("@reconnectEvents");
    cy.get('[data-operation-id="operation-generate"]', { timeout: 10_000 })
      .find('[role="progressbar"]')
      .should("have.attr", "aria-valuenow", "100");
    cy.contains("복구된 완료 결과").should("be.visible");
    cy.wrap(null).then(() => expect(timelineCalls).to.be.greaterThan(1));
  });

  it("reconnects and backfills from the unchanged cursor after an unknown SSE frame", () => {
    const pollAfter: Array<string | null> = [];
    let streamCalls = 0;
    installWorkspaceApi({
      items: [firstUserMessage, operation({ progress: 10 })],
      event_high_watermark: "30",
      revision: 7,
    });
    cy.intercept(
      "GET",
      `${ROOT}/presentations/${PRESENTATION_ID}/events*`,
      (request) => {
        const url = new URL(request.url);
        if (url.searchParams.get("stream") === "1") {
          streamCalls += 1;
          if (streamCalls === 1) {
            request.reply({
              statusCode: 200,
              headers: { "content-type": "text/event-stream" },
              body: 'event: future_event\nid: cursor-bad\ndata: {"cursor":"cursor-bad"}\n\n',
            });
          } else {
            request.destroy();
          }
          return;
        }
        pollAfter.push(url.searchParams.get("after"));
        request.reply({
          statusCode: 200,
          body:
            pollAfter.length === 1
              ? {
                  schema_version: 1,
                  events: [],
                  event_high_watermark: "30",
                }
              : {
                  schema_version: 1,
                  events: [
                    {
                      schema_version: 1,
                      id: "event-recovered",
                      operation_id: "operation-generate",
                      operation_version: 4,
                      sequence: 3,
                      cursor: "31",
                      type: "completed",
                      status: "succeeded",
                      progress: 100,
                      result: { summary: "백필 복구 완료" },
                    },
                  ],
                  event_high_watermark: "31",
                },
        });
      },
    ).as("protocolRecoveryEvents");

    cy.mount(<WorkspaceChatPanel presentationId={PRESENTATION_ID} />);
    cy.wait("@timeline");
    cy.contains("백필 복구 완료", { timeout: 5_000 })
      .should("be.visible")
      .then(() => {
        expect(pollAfter.slice(0, 2)).to.deep.equal(["30", "30"]);
      });
  });

  it("reuses the same idempotency key when an ambiguous command is explicitly retried", () => {
    const commandBodies: Array<Record<string, unknown>> = [];
    const timeline = {
      schema_version: 1,
      items: [firstUserMessage],
      event_high_watermark: "cursor-ambiguous",
      revision: 7,
      has_more: false,
    };
    installWorkspaceApi(timeline);
    cy.intercept(
      "POST",
      `${ROOT}/presentations/${PRESENTATION_ID}/commands`,
      (request) => {
        commandBodies.push(request.body as Record<string, unknown>);
        if (commandBodies.length === 1) {
          // A 5xx response is ambiguous from the workbench's perspective: the
          // durable command may have committed before the gateway failed.
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
        request.reply({ statusCode: 200, body: timeline });
      },
    ).as("workspaceCommand");

    cy.mount(<WorkspaceChatPanel presentationId={PRESENTATION_ID} />);
    cy.wait("@timeline");
    cy.get("#workspace-command").type("Make the title shorter{enter}");
    cy.wait("@workspaceCommand");
    cy.get('[data-testid="workspace-retry-intent"]')
      .should("be.visible")
      .click();
    cy.wait("@workspaceCommand").then(() => {
      expect(commandBodies).to.have.length(2);
      expect(commandBodies[1].idempotency_key).to.equal(
        commandBodies[0].idempotency_key,
      );
      expect(commandBodies[1].command).to.equal(commandBodies[0].command);
      expect(commandBodies[1].expected_revision).to.equal(
        commandBodies[0].expected_revision,
      );
    });
    cy.get('[data-testid="workspace-retry-intent"]').should("not.exist");
  });

  it("uses the safe typed error envelope and does not retry a deterministic write failure", () => {
    installWorkspaceApi({
      items: [firstUserMessage],
      event_high_watermark: "cursor-deterministic",
      revision: 7,
      has_more: false,
    });
    cy.intercept("POST", `${ROOT}/presentations/${PRESENTATION_ID}/commands`, {
      statusCode: 503,
      body: {
        detail: "workspace_policy_blocked",
        request_id: "request-safe",
        error: {
          status_code: 503,
          detail: "workspace_policy_blocked",
          code: "workspace_policy_blocked",
          message: "internal details must not be shown",
          request_id: "request-safe",
          safe_message: "이 요청은 현재 정책상 실행할 수 없습니다.",
          retryable: false,
        },
      },
    }).as("deterministicCommand");

    cy.mount(<WorkspaceChatPanel presentationId={PRESENTATION_ID} />);
    cy.wait("@timeline");
    cy.get("#workspace-command").type("Delete the deck{enter}");
    cy.wait("@deterministicCommand");
    cy.contains("이 요청은 현재 정책상 실행할 수 없습니다.").should(
      "be.visible",
    );
    cy.contains("internal details must not be shown").should("not.exist");
    cy.get('[data-testid="workspace-retry-intent"]').should("not.exist");
  });

  it("rebases a stale revision but never replays until the user explicitly retries", () => {
    const commandBodies: Array<Record<string, unknown>> = [];
    let timelineCalls = 0;
    cy.intercept(
      "GET",
      `${ROOT}/presentations/${PRESENTATION_ID}/timeline*`,
      (request) => {
        timelineCalls += 1;
        request.reply({
          statusCode: 200,
          body: {
            schema_version: 1,
            items: [firstUserMessage],
            event_high_watermark: `cursor-revision-${timelineCalls}`,
            revision: timelineCalls === 1 ? 7 : 8,
            has_more: false,
          },
        });
      },
    ).as("conflictTimeline");
    cy.intercept(
      "GET",
      `${ROOT}/presentations/${PRESENTATION_ID}/events*`,
      (request) => {
        const url = new URL(request.url);
        if (url.searchParams.get("stream") === "1") {
          request.destroy();
          return;
        }
        request.reply({
          statusCode: 200,
          body: {
            schema_version: 1,
            events: [],
            event_high_watermark: "cursor-revision-1",
          },
        });
      },
    );
    cy.intercept(
      "POST",
      `${ROOT}/presentations/${PRESENTATION_ID}/commands`,
      (request) => {
        commandBodies.push(request.body as Record<string, unknown>);
        if (commandBodies.length === 1) {
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
    ).as("conflictingCommand");

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
      expect(commandBodies[1].idempotency_key).to.equal(
        commandBodies[0].idempotency_key,
      );
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

  it("collects high-quality review options in chat before dispatching", () => {
    const timeline = {
      items: [firstUserMessage],
      event_high_watermark: "cursor-quality-options",
      revision: 7,
      has_more: false,
    };
    const commandBodies: Record<string, any>[] = [];
    let openQualityReview: ((currentSlide?: number) => void) | null = null;
    installWorkspaceApi(timeline);
    cy.intercept(
      "POST",
      `${ROOT}/presentations/${PRESENTATION_ID}/commands`,
      (request) => {
        commandBodies.push(request.body);
        request.reply({ statusCode: 200, body: timeline });
      },
    ).as("qualityReviewCommand");

    cy.mount(
      <WorkspaceChatPanel
        presentationId={PRESENTATION_ID}
        currentSlide={2}
        onQualityReviewRequesterReady={(requester) => {
          openQualityReview = requester;
        }}
      />,
    );
    cy.wait("@timeline");
    cy.then(() => {
      expect(openQualityReview).not.to.equal(null);
      openQualityReview?.(2);
    });

    cy.get('[data-testid="quality-review-options"]')
      .should("be.visible")
      .and("contain.text", "검수 범위")
      .and("contain.text", "자동으로 수정")
      .and("not.contain.text", "결과만 확인");
    cy.get('[data-testid="quality-review-scope-subset"]').click();
    cy.get("#quality-review-page-range").type("8-5");
    cy.get("#quality-review-page-range").should(
      "have.attr",
      "aria-invalid",
      "true",
    );
    cy.get("#quality-review-range-error").should("be.visible");
    cy.get("#quality-review-page-range").clear();
    cy.get("#quality-review-page-range").type("3,5-8,12");
    cy.get('[data-testid="quality-review-range-confirmation"]').should(
      "contain.text",
      "3, 5-8, 12번 페이지",
    );
    cy.get('[data-testid="quality-review-options-submit"]').click();
    cy.wait("@qualityReviewCommand");

    cy.then(() => {
      expect(commandBodies).to.have.length(1);
      expect(commandBodies[0].command_type).to.equal("quality.review");
      expect(commandBodies[0].expected_revision).to.equal(7);
      expect(commandBodies[0].context).to.deep.equal({
        scope: "subset",
        slide_indices: [2, 4, 5, 6, 7, 11],
        mode: "analyze_and_fix",
        ...(process.env.NEXT_PUBLIC_SLIDESPEC_REPAIR_CANARY_UI !== "false" &&
        process.env.NEXT_PUBLIC_SLIDESPEC_REPAIR_ALL_SLIDES !== "false"
          ? { slidespec_opt_in: true }
          : {}),
        current_slide: 2,
      });
    });
    cy.get('[data-testid="quality-review-options"]').should("not.exist");
  });
});
