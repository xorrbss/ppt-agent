import React from "react";

import AuthoredPageRemake, {
  WorkspacePageRemakePrompt,
} from "./AuthoredPageRemake";
import { PresentationGenerationApi } from "../../services/api/presentation-generation";
import { notify } from "@/components/ui/sonner";

const previewData = {
  kind: "authored_page_remake_preview" as const,
  phase: "preview_ready" as const,
  slide_index: 4,
  candidate_image: "/app_data/images/remake.png",
  candidate_digest: "sha256:candidate",
  source_digest: "sha256:source",
  candidate_issues: [],
  validation: { candidate_dom: "passed" },
  generation: { elapsed_ms: 8123, llm_scope: "single_page" },
};

// The backend keeps the task `completed` and downgrades DOM findings to an
// advisory warning: the page exists, it only needs a human look.
const warnedPreviewData = {
  ...previewData,
  baseline_issues: [{ category: "overlap", severity: "low" }],
  candidate_issues: [
    {
      category: "overlap",
      severity: "high",
      detail: "Element #headline overlaps #chart by 12px",
      qids: ["q-headline"],
    },
    {
      category: "overlap",
      severity: "medium",
      detail: "Element #legend overlaps #chart by 3px",
    },
    { category: "clipping", severity: "low", detail: "Footer glyph clipped" },
  ],
  validation: {
    source_render: "passed",
    candidate_render: "passed",
    candidate_dom: "warning",
    candidate_dom_telemetry: "available",
    reference_mode: "exact_source",
  },
};

// `needs_attention` is also true when the inspector could not measure the page,
// so a warned candidate can arrive with no issue rows at all.
const telemetryOnlyWarningData = {
  ...previewData,
  candidate_issues: [],
  validation: {
    source_render: "passed",
    candidate_render: "passed",
    candidate_dom: "warning",
    candidate_dom_telemetry: "unavailable",
    reference_mode: "exact_source",
  },
};

/** Mirrors WorkspaceChatPanel, which unmounts the prompt when it is dismissed. */
function DismissableWorkspacePrompt(
  props: Omit<
    React.ComponentProps<typeof WorkspacePageRemakePrompt>,
    "onDismiss"
  >,
) {
  const [visible, setVisible] = React.useState(true);
  return (
    <div>
      <button data-testid="host-dismiss" onClick={() => setVisible(false)}>
        닫기
      </button>
      {visible && (
        <WorkspacePageRemakePrompt {...props} onDismiss={() => setVisible(false)} />
      )}
    </div>
  );
}

function stubToasts() {
  cy.stub(notify, "error").as("notifyError");
  cy.stub(notify, "warning").as("notifyWarning");
  cy.stub(notify, "success").as("notifySuccess");
}

function preparePreview(data: Record<string, unknown> = previewData) {
  cy.clock();
  cy.stub(PresentationGenerationApi, "previewAuthoredPageRemake")
    .as("previewRequest")
    .resolves({ id: "task-remake-preview" });
  cy.stub(PresentationGenerationApi, "getGenerationStatus")
    .as("previewStatus")
    .resolves({
      status: "completed",
      message: "5페이지의 새 버전 미리보기가 준비되었습니다.",
      data,
    });
  stubToasts();
}

function prepareFailedPreview(detail: string) {
  cy.clock();
  cy.stub(PresentationGenerationApi, "previewAuthoredPageRemake")
    .as("previewRequest")
    .resolves({ id: "task-remake-preview" });
  cy.stub(PresentationGenerationApi, "getGenerationStatus")
    .as("previewStatus")
    .resolves({
      status: "error",
      error: { detail, stage: "generate", retryable: true },
    });
  stubToasts();
}

describe("AuthoredPageRemake", () => {
  it("uses the exact label and generates only the current page preview", () => {
    preparePreview();
    cy.mount(
      <AuthoredPageRemake presentationId="deck-1" currentSlide={4} />,
    );

    cy.get('[data-testid="page-remake-trigger"]')
      .should("contain.text", "페이지 다시 만들기")
      .click();
    cy.contains("5페이지만 새 레이아웃으로 다시 만듭니다.").should("be.visible");
    cy.get('[data-testid="page-remake-preview-create"]').click();
    // The poll loop registers its timer only after the start request settles.
    // Ticking before that boundary fires nothing and the run stalls forever.
    cy.get("@previewRequest").should("have.been.called");
    cy.tick(2_000);

    cy.get("@previewRequest").should("have.been.calledOnceWith", "deck-1", 4);
    cy.get('[data-testid="page-remake-preview"]').should("be.visible");
    cy.get('[data-testid="page-remake-publish"]').should(
      "contain.text",
      "이 버전 적용",
    );
  });

  // The backend persists candidate images as bare, app-data-relative refs. A raw
  // resolve leaves them relative to the document, so the preview 404s.
  it("anchors a bare candidate image reference to the backend image root", () => {
    preparePreview({
      ...previewData,
      candidate_image:
        "users/d74ac154/authored/8a836947/slide_4_47af1196.png",
    });
    cy.mount(<AuthoredPageRemake presentationId="deck-1" currentSlide={4} />);

    cy.get('[data-testid="page-remake-trigger"]').click();
    cy.get('[data-testid="page-remake-preview-create"]').click();
    cy.get("@previewRequest").should("have.been.called");
    cy.tick(2_000);

    cy.get('[data-testid="page-remake-preview"] img')
      .should("have.attr", "src")
      .and(
        "match",
        /^\/app_data\/images\/users\/d74ac154\/authored\/8a836947\/slide_4_47af1196\.png$/,
      );
  });

  // Re-authoring an already-remade page can legitimately return the same HTML.
  // That is a result to report, not a failure to hide.
  it("reports an unchanged candidate as a result with a retry action", () => {
    preparePreview({
      ...previewData,
      validation: { candidate_dom: "passed", candidate_change: "unchanged" },
    });
    cy.mount(<AuthoredPageRemake presentationId="deck-1" currentSlide={4} />);

    cy.get('[data-testid="page-remake-trigger"]').click();
    cy.get('[data-testid="page-remake-preview-create"]').click();
    cy.get("@previewRequest").should("have.been.called");
    cy.tick(2_000);

    cy.get('[data-testid="page-remake-preview"]').should("be.visible");
    cy.get('[data-testid="page-remake-unchanged"]')
      .should("be.visible")
      .and("contain.text", "새 페이지가 기존 페이지와 동일합니다");
    cy.get('[data-testid="page-remake-regenerate"]').should(
      "contain.text",
      "다시 만들기",
    );
    cy.get('[data-testid="page-remake-publish"]').should("be.visible");
    cy.get('[data-testid="page-remake-error"]').should("not.exist");
    cy.get("@notifyError").should("not.have.been.called");
  });

  it("enlarges the preview on click and restores it on a second click", () => {
    preparePreview();
    cy.mount(<AuthoredPageRemake presentationId="deck-1" currentSlide={4} />);

    cy.get('[data-testid="page-remake-trigger"]').click();
    cy.get('[data-testid="page-remake-preview-create"]').click();
    cy.get("@previewRequest").should("have.been.called");
    cy.tick(2_000);

    cy.get('[data-testid="page-remake-preview-zoom"]').should("not.exist");
    cy.get('[data-testid="page-remake-preview-zoom-in"]').click();
    cy.get('[data-testid="page-remake-preview-zoom"]').should("be.visible");
    cy.get('[data-testid="page-remake-preview-zoom"]').click();
    cy.get('[data-testid="page-remake-preview-zoom"]').should("not.exist");
  });

  it("keeps the passing preview UX free of warning affordances", () => {
    preparePreview();
    cy.mount(
      <AuthoredPageRemake presentationId="deck-1" currentSlide={4} />,
    );

    cy.get('[data-testid="page-remake-trigger"]').click();
    cy.get('[data-testid="page-remake-preview-create"]').click();
    // The poll loop registers its timer only after the start request settles.
    // Ticking before that boundary fires nothing and the run stalls forever.
    cy.get("@previewRequest").should("have.been.called");
    cy.tick(2_000);

    cy.get('[data-testid="page-remake-preview"]').should(
      "contain.text",
      "미리보기 검증 완료",
    );
    cy.get('[data-testid="page-remake-warning"]').should("not.exist");
    cy.get('[data-testid="page-remake-regenerate"]').should("not.exist");
    cy.get('[data-testid="page-remake-error"]').should("not.exist");
    cy.get("@notifyError").should("not.have.been.called");
  });

  it("publishes only after explicit approval in the legacy write path", () => {
    preparePreview();
    const publish = cy
      .stub(PresentationGenerationApi, "publishAuthoredPageRemake")
      .as("publishRequest")
      .resolves({ status: "published" });
    const onReload = cy.stub().as("reload");
    cy.mount(
      <AuthoredPageRemake
        presentationId="deck-1"
        currentSlide={4}
        onReload={onReload}
      />,
    );

    cy.get('[data-testid="page-remake-trigger"]').click();
    cy.get('[data-testid="page-remake-preview-create"]').click();
    // The poll loop registers its timer only after the start request settles.
    // Ticking before that boundary fires nothing and the run stalls forever.
    cy.get("@previewRequest").should("have.been.called");
    cy.tick(2_000);
    cy.then(() => expect(publish).not.to.have.been.called);
    cy.get('[data-testid="page-remake-publish"]').click();

    cy.get("@publishRequest").should(
      "have.been.calledOnceWith",
      "deck-1",
      4,
      "task-remake-preview",
    );
    cy.get("@reload").should("have.been.calledOnce");
  });

  it("routes chat-first mode into the workspace without opening a legacy dialog", () => {
    const requestWorkspacePrompt = cy.stub().as("workspacePrompt");
    cy.mount(
      <AuthoredPageRemake
        presentationId="deck-1"
        currentSlide={4}
        chatFirstEnabled
        workspaceCommandsEnabled
        onWorkspacePageRemakeRequest={requestWorkspacePrompt}
      />,
    );

    cy.get('[data-testid="page-remake-trigger"]').click();
    cy.get("@workspacePrompt").should("have.been.calledOnceWith", 4);
    cy.get('[role="dialog"]').should("not.exist");
    cy.get('[data-testid="page-remake-preview-create"]').should("not.exist");
  });

  it("publishes an inline workspace preview through the fenced operation", () => {
    preparePreview();
    const workspaceCommand = cy.stub().as("workspaceCommand").resolves();
    const directPublish = cy
      .stub(PresentationGenerationApi, "publishAuthoredPageRemake")
      .as("directPublish")
      .resolves({ status: "published" });
    cy.mount(
      <WorkspacePageRemakePrompt
        presentationId="deck-1"
        currentSlide={4}
        onWorkspaceCommand={workspaceCommand}
        onDismiss={cy.stub().as("dismiss")}
      />,
    );

    cy.get('[data-testid="page-remake-preview-create"]').click();
    // The poll loop registers its timer only after the start request settles.
    // Ticking before that boundary fires nothing and the run stalls forever.
    cy.get("@previewRequest").should("have.been.called");
    cy.tick(2_000);
    cy.get('[data-testid="page-remake-publish"]').click();

    cy.then(() => {
      expect(directPublish).not.to.have.been.called;
      expect(workspaceCommand).to.have.been.calledOnce;
      expect(workspaceCommand.firstCall.args).to.deep.equal([
        "5페이지 미리보기를 적용해 줘",
        {
          commandType: "slide.regenerate",
          context: {
            slide_index: 4,
            preview_task_id: "task-remake-preview",
            source: "page_remake_button",
          },
        },
      ]);
    });
    cy.get("@dismiss").should("have.been.calledOnce");
  });

  it("shows a warned candidate as a normal preview with a Korean issue summary", () => {
    preparePreview(warnedPreviewData);
    cy.mount(
      <AuthoredPageRemake presentationId="deck-1" currentSlide={4} />,
    );

    cy.get('[data-testid="page-remake-trigger"]').click();
    cy.get('[data-testid="page-remake-preview-create"]').click();
    // The poll loop registers its timer only after the start request settles.
    // Ticking before that boundary fires nothing and the run stalls forever.
    cy.get("@previewRequest").should("have.been.called");
    cy.tick(2_000);

    cy.get('[data-testid="page-remake-preview"]').should("be.visible");
    cy.get('[data-testid="page-remake-preview"] img')
      .should("have.attr", "src")
      .and("contain", "/app_data/images/remake.png");
    cy.get('[data-testid="page-remake-warning"]')
      .should("be.visible")
      .and(
        "contain.text",
        "새 페이지는 생성되었지만 자동 검사에서 확인할 항목이 발견되었습니다.",
      )
      .and("contain.text", "원본 페이지는 아직 변경되지 않았습니다.");
    cy.get('[data-testid="page-remake-warning-issues"]')
      .should("contain.text", "겹침 2건")
      .and("contain.text", "중요도 높음")
      .and("contain.text", "잘림 1건");
    // Each finding explains what it means, so the count is not the whole answer.
    cy.get('[data-testid="page-remake-issue-help-overlap"]').should(
      "contain.text",
      "요소끼리 겹쳐서",
    );
    cy.get('[data-testid="page-remake-warning-issues"]').should(
      "not.contain.text",
      "overlaps",
    );
    cy.get('[data-testid="page-remake-warning"]').should(
      "not.contain.text",
      "q-headline",
    );
    cy.get('[data-testid="page-remake-error"]').should("not.exist");
    cy.get("@notifyError").should("not.have.been.called");
    cy.get("@notifyWarning").should("have.been.calledOnce");
    cy.get('[data-testid="page-remake-publish"]').should(
      "contain.text",
      "이 버전 적용",
    );
    cy.get('[data-testid="page-remake-regenerate"]').should(
      "contain.text",
      "다시 만들기",
    );
  });

  it("publishes a warned candidate when the user approves it", () => {
    preparePreview(warnedPreviewData);
    cy.stub(PresentationGenerationApi, "publishAuthoredPageRemake")
      .as("publishRequest")
      .resolves({ status: "published" });
    cy.mount(
      <AuthoredPageRemake
        presentationId="deck-1"
        currentSlide={4}
        onReload={cy.stub().as("reload")}
      />,
    );

    cy.get('[data-testid="page-remake-trigger"]').click();
    cy.get('[data-testid="page-remake-preview-create"]').click();
    // The poll loop registers its timer only after the start request settles.
    // Ticking before that boundary fires nothing and the run stalls forever.
    cy.get("@previewRequest").should("have.been.called");
    cy.tick(2_000);
    cy.get('[data-testid="page-remake-publish"]').click();

    cy.get("@publishRequest").should(
      "have.been.calledOnceWith",
      "deck-1",
      4,
      "task-remake-preview",
    );
    cy.get("@reload").should("have.been.calledOnce");
    cy.get("@notifyError").should("not.have.been.called");
  });

  it("summarizes a telemetry-only warning without an empty issue list", () => {
    preparePreview(telemetryOnlyWarningData);
    cy.mount(
      <AuthoredPageRemake presentationId="deck-1" currentSlide={4} />,
    );

    cy.get('[data-testid="page-remake-trigger"]').click();
    cy.get('[data-testid="page-remake-preview-create"]').click();
    // The poll loop registers its timer only after the start request settles.
    // Ticking before that boundary fires nothing and the run stalls forever.
    cy.get("@previewRequest").should("have.been.called");
    cy.tick(2_000);

    cy.get('[data-testid="page-remake-preview"]').should("be.visible");
    cy.get('[data-testid="page-remake-warning"]').should(
      "contain.text",
      "새 페이지는 생성되었지만 자동 검사에서 확인할 항목이 발견되었습니다.",
    );
    cy.get('[data-testid="page-remake-warning-issues"]')
      .should("contain.text", "자동 검사를 끝까지 수행하지 못했습니다.")
      .and("match", "p");
    cy.get('[data-testid="page-remake-regenerate"]').should("be.visible");
    cy.get('[data-testid="page-remake-publish"]').should("be.visible");
    cy.get("@notifyError").should("not.have.been.called");
  });

  it("starts a fresh preview when the user regenerates a warned candidate", () => {
    preparePreview(warnedPreviewData);
    // The regenerated run polls once before completing, so a superseded run
    // would have a window to write stale state.
    cy.get("@previewStatus").then((status: any) => {
      status.onCall(1).resolves({ status: "pending", message: "다시 만드는 중" });
    });
    cy.mount(
      <AuthoredPageRemake presentationId="deck-1" currentSlide={4} />,
    );

    cy.get('[data-testid="page-remake-trigger"]').click();
    cy.get('[data-testid="page-remake-preview-create"]').click();
    // The poll loop registers its timer only after the start request settles.
    // Ticking before that boundary fires nothing and the run stalls forever.
    cy.get("@previewRequest").should("have.been.called");
    cy.tick(2_000);
    cy.get('[data-testid="page-remake-regenerate"]').click();

    // The stale preview is dropped immediately so no publish can target it.
    cy.get('[data-testid="page-remake-preview"]').should("not.exist");
    cy.get('[data-testid="page-remake-publish"]').should("not.exist");
    cy.get('[data-testid="page-remake-preview-create"]').should(
      "contain.text",
      "미리보기 만드는 중",
    );

    cy.tick(2_000);
    // The superseded candidate must not reappear while the new run polls.
    cy.get('[data-testid="page-remake-status"]').should(
      "contain.text",
      "다시 만드는 중",
    );
    cy.get('[data-testid="page-remake-preview"]').should("not.exist");
    cy.tick(2_000);
    cy.get('[data-testid="page-remake-preview"]').should("be.visible");
    cy.get("@previewRequest").should("have.been.calledTwice");
    // The regenerated preview must target the same zero-based slide index.
    cy.get("@previewRequest").then((request: any) => {
      expect(request.secondCall.args).to.deep.equal(["deck-1", 4]);
    });
    cy.get("@notifyWarning").should("have.been.calledTwice");
    cy.get("@notifyError").should("not.have.been.called");
  });

  it("stops polling and stays silent when the flow is dismissed mid-generation", () => {
    preparePreview();
    cy.mount(
      <DismissableWorkspacePrompt
        presentationId="deck-1"
        currentSlide={4}
        onWorkspaceCommand={cy.stub().as("workspaceCommand").resolves()}
      />,
    );

    cy.get('[data-testid="page-remake-preview-create"]').click();
    cy.get('[data-testid="host-dismiss"]').click();
    cy.get('[data-testid="page-remake-workspace-prompt"]').should("not.exist");

    cy.tick(2_000);
    cy.tick(2_000);
    cy.get('[data-testid="page-remake-preview"]').should("not.exist");
    cy.get("@previewStatus").should("not.have.been.called");
    cy.get("@notifySuccess").should("not.have.been.called");
    cy.get("@notifyWarning").should("not.have.been.called");
    cy.get("@notifyError").should("not.have.been.called");
  });

  it("explains a genuine failure in Korean instead of the internal code", () => {
    prepareFailedPreview("authored_page_remake_generation_failed");
    cy.mount(
      <AuthoredPageRemake presentationId="deck-1" currentSlide={4} />,
    );

    cy.get('[data-testid="page-remake-trigger"]').click();
    cy.get('[data-testid="page-remake-preview-create"]').click();
    // The poll loop registers its timer only after the start request settles.
    // Ticking before that boundary fires nothing and the run stalls forever.
    cy.get("@previewRequest").should("have.been.called");
    cy.tick(2_000);

    cy.get('[data-testid="page-remake-error"] p')
      .first()
      .should("contain.text", "새 페이지를 만들지 못했습니다.")
      .and("not.contain.text", "authored_page_remake");
    cy.get('[data-testid="page-remake-error-code"]').should(
      "contain.text",
      "authored_page_remake_generation_failed",
    );
    cy.get('[data-testid="page-remake-preview"]').should("not.exist");
    cy.get("@notifyError").should(
      "have.been.calledWith",
      "페이지 다시 만들기 실패",
      "새 페이지를 만들지 못했습니다. 잠시 후 다시 시도해 주세요.",
    );
  });

  it("falls back to a readable message for an unmapped internal code", () => {
    prepareFailedPreview("authored_page_remake_unknown_future_code");
    cy.mount(
      <AuthoredPageRemake presentationId="deck-1" currentSlide={4} />,
    );

    cy.get('[data-testid="page-remake-trigger"]').click();
    cy.get('[data-testid="page-remake-preview-create"]').click();
    // The poll loop registers its timer only after the start request settles.
    // Ticking before that boundary fires nothing and the run stalls forever.
    cy.get("@previewRequest").should("have.been.called");
    cy.tick(2_000);

    cy.get('[data-testid="page-remake-error"] p')
      .first()
      .should("contain.text", "페이지를 다시 만들지 못했습니다.")
      .and("not.contain.text", "authored_page_remake");
    cy.get('[data-testid="page-remake-error-code"]').should(
      "contain.text",
      "authored_page_remake_unknown_future_code",
    );
  });
});
