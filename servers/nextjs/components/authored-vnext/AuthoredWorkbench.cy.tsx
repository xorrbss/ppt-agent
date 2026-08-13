import React from "react";
import { mount } from "cypress/react";

import { AuthoredWorkbench, type AuthoredWorkbenchProps } from "./AuthoredWorkbench";
import type { AuthoredWorkbenchCallbacks } from "./types";

const slides: AuthoredWorkbenchProps["slides"] = Array.from({ length: 90 }, (_, index) => ({
  id: `slide-${index + 1}`,
  position: index,
  qualityScore: 94,
  revision: 3,
  status: index === 89 ? "generating" : "ready",
  title: `${index + 1}장 제목`,
}));

const scene: NonNullable<AuthoredWorkbenchProps["scene"]> = {
  bodyElements: [
    {
      bounds: { height: 0.2, width: 0.8, x: 0.1, y: 0.2 },
      id: "body-title",
      kind: "text",
      label: "본문 제목",
      text: "초기 제목",
    },
  ],
  masterElements: [
    {
      bounds: { height: 0.08, width: 0.8, x: 0.1, y: 0.05 },
      id: "master-header",
      kind: "text",
      label: "회사 머리글",
      text: "ACME",
    },
  ],
  masterRevisionLabel: "master r7",
  revision: 3,
  slideId: "slide-1",
  title: "1장 제목",
};

function createProps(callbacks: Partial<AuthoredWorkbenchCallbacks> = {}): AuthoredWorkbenchProps {
  const noop = () => undefined;
  return {
    activePanel: "operations",
    activeSlideId: "slide-1",
    callbacks: {
      onAcceptRegeneration: noop,
      onCommand: noop,
      onDismissConflict: noop,
      onRedo: noop,
      onRebaseConflict: noop,
      onRejectRegeneration: noop,
      onReorderSlide: noop,
      onSelectElement: noop,
      onSelectSlide: noop,
      onUndo: noop,
      ...callbacks,
    },
    canRedo: true,
    canUndo: true,
    createOperationId: () => "operation-1",
    featureEnabled: true,
    inspector: {
      context: { elementId: "body-title", expectedRevision: 3, slideId: "slide-1" },
      kind: "text",
      text: "초기 제목",
    },
    onPanelChange: noop,
    panels: {
      history: [],
      operations: [{ id: "operation-old", kind: "text.patch", label: "제목 변경", status: "queued" }],
      quality: [],
      sources: [],
    },
    railWindow: { rowHeight: 88, startIndex: 0, visibleCount: 3 },
    scene,
    selectedElementId: "body-title",
    slides,
    state: { state: "ready" },
  };
}

describe("AuthoredWorkbench", () => {
  it("renders only the virtual rail slice and emits keyboard reorder", () => {
    const onReorderSlide = cy.stub().as("reorder");
    mount(<AuthoredWorkbench {...createProps({ onReorderSlide })} />);

    cy.get('[aria-label="가상화된 슬라이드 레일"] > li').should("have.length", 5);
    cy.get('button[aria-label^="2번 슬라이드"]').focus().type("{alt}{downarrow}");
    cy.get("@reorder").should("have.been.calledWith", "slide-2", 2);
    cy.get('[aria-label="가상화된 슬라이드 레일"]').parent().scrollTo(0, 44 * 88);
    cy.get('button[aria-label^="45번 슬라이드"]').should("be.visible");
    cy.get('[aria-label="가상화된 슬라이드 레일"] > li').should("have.length.at.most", 7);
  });

  it("keeps master furniture locked and emits composition-safe typed text patches", () => {
    const onCommand = cy.stub().as("command");
    mount(<AuthoredWorkbench {...createProps({ onCommand })} />);

    cy.get('[data-layer="master"] [data-locked="true"]').should("contain.text", "ACME");
    cy.get('[data-layer="master"]').should("have.class", "pointer-events-none");
    cy.get('textarea[aria-label="본문 제목 텍스트 편집"]').then(([editor]) => {
      editor.dispatchEvent(new CompositionEvent("compositionstart", { bubbles: true }));
      (editor as HTMLTextAreaElement).value = "한글 제목";
      editor.dispatchEvent(new Event("input", { bubbles: true }));
    });
    cy.get("@command").should("not.have.been.called");
    cy.get('textarea[aria-label="본문 제목 텍스트 편집"]').then(([editor]) => {
      editor.dispatchEvent(new CompositionEvent("compositionend", { bubbles: true, data: "목" }));
    });
    cy.get("@command").should("have.been.calledOnce");
    cy.get("@command").its("firstCall.args.0").should("deep.include", {
      clientOperationId: "operation-1",
      elementId: "body-title",
      expectedRevision: 3,
      kind: "text.patch",
      slideId: "slide-1",
    });
  });

  it("offers explicit conflict rebase and body-only regeneration decisions", () => {
    const onRebaseConflict = cy.stub().as("rebase");
    const onAcceptRegeneration = cy.stub().as("accept");
    mount(
      <AuthoredWorkbench
        {...createProps({ onAcceptRegeneration, onRebaseConflict })}
        conflict={{ actualRevision: 4, canRebase: true, expectedRevision: 3, message: "다른 편집이 먼저 반영되었습니다.", operationId: "operation-1" }}
        regenerationCandidate={{ basedOnRevision: 3, before: <p>현재</p>, candidate: <p>후보</p>, id: "candidate-1", label: "대안 A", slideId: "slide-1", summary: "본문 밀도 개선" }}
      />,
    );

    cy.contains("button", "최신 버전에 재적용").click();
    cy.get("@rebase").should("have.been.calledWith", "operation-1");
    cy.contains("button", "본문만 적용").click();
    cy.get("@accept").should("have.been.calledWith", "candidate-1");
    cy.contains("잠긴 마스터 레이어는 후보 비교와 적용 범위에 포함되지 않습니다.").should("exist");
  });

  it("does not mount the workbench when the feature flag is off", () => {
    mount(<AuthoredWorkbench {...createProps()} fallback={<p>기존 편집기</p>} featureEnabled={false} />);
    cy.contains("기존 편집기").should("exist");
    cy.get('[data-testid="authored-workbench"]').should("not.exist");
  });
});
