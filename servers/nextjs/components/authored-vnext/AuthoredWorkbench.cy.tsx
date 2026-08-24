import React from "react";
import { mount } from "cypress/react";
import { NextIntlClientProvider } from "next-intl";

import koMessages from "@/i18n/messages/ko.json";
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
  backgroundColor: "#E5E7EB",
  bodyElements: [
    {
      bounds: { height: 0.2, width: 0.8, x: 0.1, y: 0.2 },
      id: "body-title",
      kind: "text",
      label: "본문 제목",
      editability: { text: true, geometry: true, asset: false, data: false, style: true },
      style: { color: "#172554", fontFamily: "Noto Sans KR", fontSize: 26, fontWeight: 700 },
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
    {
      bounds: { height: 0.02, width: 0.8, x: 0.1, y: 0.14 },
      id: "master-divider",
      kind: "line",
      label: "구분선",
      style: { borderColor: "#2563eb" },
    },
    {
      bounds: { height: 0.08, width: 0.1, x: 0.82, y: 0.04 },
      id: "master-accent",
      kind: "shape",
      label: "강조 영역",
      style: { backgroundColor: "#dbeafe", borderColor: "#2563eb" },
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
      style: { color: "#172554", fontFamily: "Noto Sans KR", fontSize: 26, fontWeight: 700, textAlign: "left" },
      styleEditable: true,
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

function mountWorkbench(component: React.ReactNode) {
  mount(
    <NextIntlClientProvider locale="ko" messages={koMessages} timeZone="Asia/Seoul">
      {component}
    </NextIntlClientProvider>,
  );
}

function ControlledRailWorkbench({
  onReorderSlide,
}: {
  onReorderSlide: AuthoredWorkbenchCallbacks["onReorderSlide"];
}) {
  const [startIndex, setStartIndex] = React.useState(0);
  return (
    <AuthoredWorkbench
      {...createProps({ onReorderSlide })}
      onRailWindowChange={setStartIndex}
      railWindow={{ rowHeight: 88, startIndex, visibleCount: 3 }}
    />
  );
}

describe("AuthoredWorkbench", () => {
  it("renders only the virtual rail slice and emits keyboard reorder", () => {
    const onReorderSlide = cy.stub().as("reorder");
    mountWorkbench(<ControlledRailWorkbench onReorderSlide={onReorderSlide} />);

    cy.get('[aria-label="가상화된 슬라이드 레일"] > li').should("have.length", 5);
    cy.get('button[aria-label^="2번 슬라이드"]').focus().type("{alt}{downarrow}");
    cy.get("@reorder").should("have.been.calledWith", "slide-2", 2);
    cy.get('[aria-label="가상화된 슬라이드 레일"]').parent().scrollTo(0, 44 * 88);
    cy.get('button[aria-label^="45번 슬라이드"]').should("be.visible");
    cy.get('[aria-label="가상화된 슬라이드 레일"] > li').should("have.length.at.most", 7);
  });

  it("keeps master furniture locked and emits composition-safe typed text patches", () => {
    const onCommand = cy.stub().as("command");
    mountWorkbench(<AuthoredWorkbench {...createProps({ onCommand })} />);

    cy.get('[data-layer="master"] [data-locked="true"]').should("contain.text", "ACME");
    cy.get('[data-master-kind="line"] > span').should("have.css", "border-top-style", "solid");
    cy.get('[data-master-kind="shape"] > span').should("have.css", "background-color", "rgb(219, 234, 254)");
    cy.get('[data-layer="master"]').should("have.class", "pointer-events-none");
    cy.get('[data-testid="authored-layered-canvas"]').should("have.css", "background-color", "rgb(229, 231, 235)");
    cy.get('textarea[aria-label="본문 제목 텍스트 편집"]').should("have.css", "font-family").and("contain", "Noto Sans KR");
    cy.get('textarea[aria-label="본문 제목 텍스트 편집"]').should("have.css", "word-break", "keep-all");
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

  it("renders a Scene shape as a selectable geometry object and exposes unsupported Scene capability boundaries", () => {
    const onCommand = cy.stub().as("scene-command");
    const sceneWithObjects: NonNullable<AuthoredWorkbenchProps["scene"]> = {
      ...scene,
      bodyElements: [
        {
          bounds: { height: 0.15, width: 0.2, x: 0.1, y: 0.2 },
          editability: { text: false, geometry: true, asset: false, data: false },
          id: "element:shape",
          kind: "shape",
          label: "강조 패널",
          shape: "roundRect",
          style: { backgroundColor: "#2563eb" },
        },
        {
          bounds: { height: 0.2, width: 0.4, x: 0.4, y: 0.2 },
          capabilityMessage: "Scene table preview is data-pinned; only sealed data and geometry mutations are available.",
          dataId: "data:table",
          dataDigest: `sha256:${"a".repeat(64)}`,
          dataPayload: { cells: [["항목", "값"], ["도입", "42"]] },
          editability: { text: false, geometry: true, asset: false, data: true },
          id: "element:table",
          kind: "table",
          label: "지표 표",
          rows: 2,
          columns: 2,
        },
        {
          assetDigest: `sha256:${"b".repeat(64)}`,
          assetId: "asset:hero",
          alt: "제품 화면",
          bounds: { height: 0.2, width: 0.2, x: 0.1, y: 0.5 },
          crop: "cover",
          editability: { text: false, geometry: true, asset: true, data: false },
          fit: "cover",
          id: "element:image",
          kind: "asset",
          label: "제품 화면",
          previewUrl: "data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='20' height='20'%3E%3Crect width='20' height='20' fill='%232563eb'/%3E%3C/svg%3E",
        },
        {
          accessibilitySummary: "분기별 매출 추이",
          bounds: { height: 0.2, width: 0.3, x: 0.35, y: 0.5 },
          capabilityMessage: "Scene chart preview is data-pinned; only sealed data and geometry mutations are available.",
          chartType: "line",
          dataDigest: `sha256:${"c".repeat(64)}`,
          dataId: "data:chart",
          dataPayload: { categories: ["1분기", "2분기"], series: [{ name: "매출", values: [42, 67] }] },
          editability: { text: false, geometry: true, asset: false, data: true },
          id: "element:chart",
          kind: "chart",
          label: "매출 추이",
        },
      ],
    };
    mountWorkbench(<AuthoredWorkbench {...createProps({ onCommand })} inspector={{ context: { elementId: "element:shape", expectedRevision: 3, slideId: "slide-1" }, kind: "geometry", bounds: sceneWithObjects.bodyElements[0].bounds }} scene={sceneWithObjects} selectedElementId="element:shape" />);

    cy.get('[data-element-id="element:shape"]').should("be.visible");
    cy.get('input[type="number"]').first().clear().type("0.2").blur();
    cy.get("@scene-command").its("firstCall.args.0").should("deep.include", {
      elementId: "element:shape",
      kind: "geometry.patch",
      slideId: "slide-1",
    });
    cy.contains("도입").should("be.visible");
    cy.get('img[alt="제품 화면"]').should("have.attr", "src").and("include", "data:image/svg+xml");
    cy.get('[data-element-id="element:chart"] [role="img"]').should("have.attr", "aria-label", "분기별 매출 추이");
  });

  it("offers explicit conflict rebase and body-only regeneration decisions", () => {
    const onRebaseConflict = cy.stub().as("rebase");
    const onAcceptRegeneration = cy.stub().as("accept");
    mountWorkbench(
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
    cy.contains("잠긴 Master 레이어는 후보 비교와 적용 범위에 포함되지 않습니다.").should("exist");
  });

  it("does not mount the workbench when the feature flag is off", () => {
    mountWorkbench(<AuthoredWorkbench {...createProps()} fallback={<p>기존 편집기</p>} featureEnabled={false} />);
    cy.contains("기존 편집기").should("exist");
    cy.get('[data-testid="authored-workbench"]').should("not.exist");
  });

  it("commits multiline native text explicitly with Ctrl+Enter", () => {
    const onCommand = cy.stub().as("command");
    mountWorkbench(<AuthoredWorkbench {...createProps({ onCommand })} />);

    cy.get('textarea[aria-label="본문 제목 텍스트 편집"]')
      .clear()
      .type("명시적으로 저장한 제목{ctrl}{enter}");

    cy.get("@command").should("have.been.calledOnce");
    cy.get("@command").its("firstCall.args.0").should("deep.include", {
      clientOperationId: "operation-1",
      elementId: "body-title",
      expectedRevision: 3,
      kind: "text.patch",
      patch: { text: "명시적으로 저장한 제목" },
      slideId: "slide-1",
    });
  });

  it("emits a separate strict native text-style command without replacing text", () => {
    const onCommand = cy.stub().as("text-style-command");
    mountWorkbench(<AuthoredWorkbench {...createProps({ onCommand })} />);
    cy.get('input[aria-label="Text color"]').clear().type("#2563eb");
    cy.contains("button", "Apply native text style").click();
    cy.get("@text-style-command").should("have.been.calledWithMatch", {
      kind: "text.style.patch",
      elementId: "body-title",
      patch: { color: "#2563eb" },
    });
  });

  it("displays the server-issued v4 editable-Hybrid state without promoting it locally", () => {
    mountWorkbench(
      <AuthoredWorkbench
        {...createProps()}
        editableHybridMvpReadiness={{
          profileId: "authored-editable-hybrid-ko-en-basic-v1",
          releaseScope: "ko-en-text-solid-shape-image-crop-basic-table",
          status: "blocked",
          editableHybridMvpReady: false,
          nativeEditableReady: false,
          reasonCode: "editable_hybrid_mvp_not_evaluated",
        }}
      />,
    );
    cy.get('[data-testid="editable-hybrid-mvp-readiness"]')
      .should("contain.text", "편집 가능한 Hybrid MVP · 차단됨")
      .and("have.attr", "title")
      .and("include", "editable_hybrid_mvp_not_evaluated");
  });
});
