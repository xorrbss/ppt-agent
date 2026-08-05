import React from "react";
import { Provider } from "react-redux";
import { AppRouterContext } from "next/dist/shared/lib/app-router-context.shared-runtime";
import { PathnameContext } from "next/dist/shared/lib/hooks-client-context.shared-runtime";

import PresentationHeader from "./PresentationHeader";
import { notify } from "@/components/ui/sonner";
import { PresentationGenerationApi } from "../../services/api/presentation-generation";
import ThemeApi from "../../services/api/theme";
import { store } from "@/store/store";
import {
  setPresentationData,
  setStreaming,
} from "@/store/slices/presentationGeneration";
import { isAuthoredPresentation } from "../utils/isAuthoredPresentation";
import type {
  WorkspaceCommandDispatcher,
  WorkspacePageRemakeRequester,
  WorkspaceQualityReviewRequester,
} from "../workspace";

const mountHeader = (
  isAuthoredDeck: boolean,
  mutationsEnabled = true,
  workspace?: {
    enabled: boolean;
    command: WorkspaceCommandDispatcher;
    qualityReview?: WorkspaceQualityReviewRequester;
    pageRemake?: WorkspacePageRemakeRequester;
  },
) => {
  store.dispatch(
    setPresentationData({
      id: "deck-1",
      language: "en",
      layout: isAuthoredDeck ? null : { name: "adaptive", ordered: false, slides: [] },
      n_slides: 1,
      title: "deck",
      slides: [{ id: "slide-1", layout: "standard" }],
      theme: null,
      mode: isAuthoredDeck ? "authored" : "adaptive",
    } as any)
  );
  store.dispatch(setStreaming(false));

  cy.mount(
    <AppRouterContext.Provider
      value={{
        back: cy.stub(),
        forward: cy.stub(),
        refresh: cy.stub(),
        push: cy.stub(),
        replace: cy.stub(),
        prefetch: cy.stub(),
        hmrRefresh: cy.stub(),
      } as any}
    >
      <PathnameContext.Provider value="/presentation/deck-1">
        <Provider store={store}>
          <PresentationHeader
            presentation_id="deck-1"
            isPresentationSaving={false}
            currentSlide={workspace?.pageRemake ? 0 : undefined}
            isAuthoredDeck={isAuthoredDeck}
            mutationsEnabled={mutationsEnabled}
            chatFirstEnabled={workspace !== undefined}
            workspaceCommandsEnabled={workspace?.enabled ?? false}
            onWorkspaceCommand={workspace?.command}
            onWorkspaceQualityReviewRequest={workspace?.qualityReview}
            onWorkspacePageRemakeRequest={workspace?.pageRemake}
          />
        </Provider>
      </PathnameContext.Provider>
    </AppRouterContext.Provider>
  );
};

const openExportOptions = () => cy.get('[data-testid="export-trigger"]').click();

const expectExportBody = (expected: Record<string, string>) =>
  cy
    .wait("@exportPresentation")
    .its("request.body")
    .then((body) => expect(JSON.parse(body)).to.deep.equal(expected));

describe("PresentationHeader authored export", () => {
  beforeEach(() => {
    cy.stub(ThemeApi, "getThemes").as("getThemes").resolves([]);
    cy.stub(PresentationGenerationApi, "updatePresentationContent")
      .as("updatePresentationContent")
      .resolves({});
    cy.intercept("POST", "**/api/export-presentation", (request) => {
      request.reply({
        statusCode: 200,
        headers: {
          "content-type": "application/octet-stream",
          "content-disposition": 'attachment; filename="deck.pptx"',
        },
        body: { path: "/exports/deck.pptx" },
      });
    }).as("exportPresentation");
  });

  it("shows template conversion instead of an ineffective theme control for authored decks", () => {
    mountHeader(true);

    cy.get('[data-testid="template-change-trigger"]')
      .should("be.visible")
      .and("contain.text", "템플릿 변경");
    cy.get('[data-testid="quality-review-trigger"]')
      .should("be.visible")
      .and("contain.text", "고품질 검수");
    cy.contains("button", "테마").should("not.exist");
    cy.get("@getThemes").should("not.have.been.called");
  });

  it("keeps the theme control for regular editable decks", () => {
    mountHeader(false);

    cy.get('[data-testid="template-change-trigger"]').should("not.exist");
    cy.contains("button", "테마").should("be.visible");
    cy.get("@getThemes").should("have.been.calledOnce");
  });

  it("sends hybrid by default for the authored separated-elements option", () => {
    mountHeader(true);

    openExportOptions();
    cy.get('[data-testid="authored-export-hybrid"]').click();
    expectExportBody({
      format: "pptx",
      id: "deck-1",
      title: "deck",
      pptxMode: "hybrid",
      fontEmbedding: false,
    });
    cy.get("@updatePresentationContent").should("have.been.calledOnce");
  });

  it("allows wrapped export cards to grow with their content", () => {
    mountHeader(true);

    openExportOptions();
    cy.get('[data-testid="authored-export-hybrid"], [data-testid="authored-export-hybrid-embedded"]')
      .each(($button) => {
        expect($button).to.have.class("h-auto");
        expect($button).to.have.class("whitespace-normal");
        expect($button).to.have.class("items-start");
      });
    cy.get('[data-testid="export-pdf"]')
      .should("have.class", "h-auto")
      .and("have.class", "justify-between");
  });

  it("preserves the PDF request shape after a separated-elements export", () => {
    mountHeader(true);

    openExportOptions();
    cy.get('[data-testid="authored-export-hybrid"]').click({ force: true });
    expectExportBody({
      format: "pptx",
      id: "deck-1",
      title: "deck",
      pptxMode: "hybrid",
      fontEmbedding: false,
    });

    cy.get('[data-testid="export-trigger"]').should("not.be.disabled");
    openExportOptions();
    cy.get('[data-testid="export-pdf"]').click({ force: true });
    expectExportBody({
      format: "pdf",
      id: "deck-1",
      title: "deck",
    });
    cy.get("@updatePresentationContent").should("have.been.calledTwice");
  });

  it("shows an actionable message for an expired export session", () => {
    cy.intercept("POST", "**/api/export-presentation", {
      statusCode: 401,
      body: { error: "Authentication required" },
    }).as("expiredExport");
    cy.stub(notify, "error").as("notifyError");
    mountHeader(true);

    openExportOptions();
    cy.get('[data-testid="export-pdf"]').click({ force: true });
    cy.wait("@expiredExport");
    cy.get("@notifyError").should(
      "have.been.calledWith",
      "로그인이 필요합니다",
      "로그인 세션이 만료되었습니다. 페이지를 새로고침한 뒤 다시 로그인해 주세요."
    );
    cy.get("@updatePresentationContent").should("have.been.calledOnce");
  });

  it("keeps embedding off by default and sends an explicit opt-in with a warning", () => {
    mountHeader(true);

    openExportOptions();
    cy.get('[data-testid="authored-export-hybrid-embedded"]')
      .should("contain.text", "글꼴 포함")
      .and("contain.text", "파일 크기가 크게 늘 수 있습니다")
      .click({ force: true });
    expectExportBody({
      format: "pptx",
      id: "deck-1",
      title: "deck",
      pptxMode: "hybrid",
      fontEmbedding: true,
    });
  });
  it("shows optional hybrid editability metadata and warns on image fallback", () => {
    cy.intercept("POST", "**/api/export-presentation", {
      statusCode: 200,
      body: {
        path: "/exports/deck.pptx",
        quality: {
          schemaVersion: "presenton.export-quality/v1",
          mode: "hybrid",
          // A stale/cached producer may contradict its detailed counts. The UI
          // must still warn from the independent fallback evidence.
          status: "fully-editable",
          totalSlides: 2,
          editableSlides: 1,
          imageFallbackSlides: 1,
          nativeTextElements: 3,
          nativeShapeElements: 1,
          nativeGroupElements: 1,
          nativeImageElements: 0,
          rasterFallbackElements: 1,
          fallbackReasonCounts: { "clip-path": 1 },
          fontEmbedding: false,
          fontEmbeddingStatus: {
            policy: "opt-in",
            requested: false,
            applied: false,
            embeddedFontFiles: 0,
            reason: "not-requested",
          },
          fontRendering: {
            browserFontFilesCollected: 2,
            browserCollectionFailures: 0,
            powerpointTypefacePolicy: "central-compatible-fallbacks",
          },
          slides: [
            {
              slideNumber: 1,
              editable: true,
              imageFallback: false,
              nativeTextElements: 3,
              nativeShapeElements: 1,
              nativeGroupElements: 1,
              nativeImageElements: 0,
              rasterFallbackElements: 0,
              fallbackReasons: [],
              fallbackElements: [],
            },
            {
              slideNumber: 2,
              editable: false,
              imageFallback: true,
              nativeTextElements: 0,
              nativeShapeElements: 0,
              nativeGroupElements: 0,
              nativeImageElements: 0,
              rasterFallbackElements: 1,
              fallbackReasons: ["clip-path"],
              fallbackElements: [
                {
                  elementId: "hero::before",
                  candidateKind: "complex",
                  reasons: ["clip-path"],
                },
              ],
            },
          ],
        },
      },
    }).as("qualityExport");
    mountHeader(true);

    openExportOptions();
    cy.get('[data-testid="authored-export-hybrid"]').click({ force: true });
    cy.wait("@qualityExport");
    cy.get('[data-testid="export-quality-dialog"]')
      .should("contain.text", "전체 2장")
      .and("contain.text", "이미지 fallback 1장")
      .and("contain.text", "완전 편집형이 아닙니다")
      .and("contain.text", "그룹 1개")
      .and("contain.text", "잔여 raster 1개")
      .and("contain.text", "hero::before")
      .and("contain.text", "이 내보내기에는 글꼴이 포함되지 않아");
  });

  it("does not expose authored choices or alter the adaptive PPTX body", () => {
    mountHeader(false);

    openExportOptions();
    cy.get('[data-testid="authored-export-hybrid"]').should("not.exist");
    cy.get('[data-testid="authored-export-hybrid-embedded"]').should("not.exist");
    cy.get('[data-testid="export-pptx"]').click();
    expectExportBody({
      format: "pptx",
      id: "deck-1",
      title: "deck",
    });
    cy.get("@updatePresentationContent").should("have.been.calledOnce");
  });

  it("keeps the authored export trigger focusable and restores focus after export", () => {
    cy.viewport(360, 640);
    mountHeader(true);

    cy.get('[data-testid="export-trigger"]').should(
      "have.prop",
      "tagName",
      "BUTTON",
    );
    cy.get('[data-testid="export-trigger"]')
      .focus()
      .should("be.focused");
    cy.get('[data-testid="export-trigger"]').click();
    cy.get('[data-testid="authored-export-hybrid"]')
      .should("have.attr", "aria-label")
      .and("not.be.empty");
    cy.get('[data-testid="authored-export-hybrid"]').click();
    cy.wait("@exportPresentation");
    cy.get('[data-testid="export-trigger"]').should("be.focused");
  });

  it("disables a second request while the export is loading", () => {
    cy.intercept("POST", "**/api/export-presentation", {
      statusCode: 200,
      delay: 500,
      body: { path: "/exports/deck.pptx" },
    }).as("slowExport");
    mountHeader(true);

    openExportOptions();
    cy.get('[data-testid="authored-export-hybrid"]').click();
    cy.get('[data-testid="export-trigger"]').should("be.disabled");
    cy.get('[role="status"]').should("exist");
    cy.wait("@slowExport");
    cy.get("@slowExport.all").should("have.length", 1);
  });

  it("recovers from an export error without leaving the trigger disabled", () => {
    cy.intercept("POST", "**/api/export-presentation", { statusCode: 500 }).as(
      "failedExport"
    );
    mountHeader(true);

    openExportOptions();
    cy.get('[data-testid="authored-export-hybrid"]').click();
    cy.wait("@failedExport");
    cy.get('[data-testid="export-trigger"]').should("not.be.disabled");
  });

  it("routes page remake and quality into chat-first UI without a legacy dialog", () => {
    const workspaceCommand = cy.stub().as("workspaceCommand").resolves();
    const qualityReview = cy.stub().as("qualityReview");
    const pageRemake = cy.stub().as("pageRemake");
    mountHeader(true, false, {
      enabled: true,
      command: workspaceCommand,
      qualityReview,
      pageRemake,
    });

    cy.get('[data-testid="page-remake-trigger"]').click();
    cy.get("@pageRemake").should("have.been.calledOnceWith", 0);
    cy.get('[role="dialog"]').should("not.exist");

    cy.get('[data-testid="workspace-quality-review"]').click();
    cy.wrap(null).then(() => {
      expect(qualityReview).to.have.been.calledOnceWith(0);
      expect(workspaceCommand).not.to.have.been.called;
    });

    openExportOptions();
    cy.get('[data-testid="export-pdf"]').click({ force: true });
    openExportOptions();
    cy.get('[data-testid="authored-export-hybrid"]').click({ force: true });

    cy.wrap(null).then(() => {
      expect(workspaceCommand.getCall(0).args).to.deep.equal([
        "PDF로 내보내기",
        { commandType: "export.pdf", context: { format: "pdf" } },
      ]);
      expect(workspaceCommand.getCall(1).args).to.deep.equal([
        "편집 가능한 요소를 분리해 PPTX로 내보내기",
        {
          commandType: "export.pptx",
          context: { format: "pptx", pptx_mode: "hybrid", font_embedding: false },
        },
      ]);
    });
    cy.get("@updatePresentationContent").should("not.have.been.called");
  });

  it("dispatches durable version restore commands for chat-first undo and redo", () => {
    const workspaceCommand = cy.stub().as("workspaceCommand").resolves();
    mountHeader(false, false, { enabled: true, command: workspaceCommand });

    cy.get('[data-testid="presentation-undo"]')
      .should("be.visible")
      .and("not.be.disabled")
      .click();
    cy.get('[data-testid="presentation-redo"]')
      .should("be.visible")
      .and("not.be.disabled")
      .click();

    cy.wrap(null).then(() => {
      expect(workspaceCommand.getCall(0).args).to.deep.equal([
        "이전 서버 버전으로 복원해 줘",
        {
          commandType: "version.restore",
          context: { strategy: "previous", source: "undo" },
        },
      ]);
      expect(workspaceCommand.getCall(1).args).to.deep.equal([
        "복원 직전 서버 버전으로 다시 실행해 줘",
        {
          commandType: "version.restore",
          context: { strategy: "previous", source: "redo" },
        },
      ]);
    });
    cy.get("@updatePresentationContent").should("not.have.been.called");
  });

  it("makes the header read-only and skips snapshot writes when chat-first mutations are disabled", () => {
    cy.stub(PresentationGenerationApi, "getPresentationVersions")
      .as("getPresentationVersions")
      .resolves([
        {
          id: "version-1",
          created_at: "2026-08-03T00:00:00Z",
          slide_count: 1,
        },
      ]);
    mountHeader(false, false);

    cy.get('[data-testid="presentation-title-edit"]').should("be.disabled");
    cy.get('[data-testid="presentation-regenerate"]').should("have.class", "hidden");
    cy.get('[data-testid="presentation-undo"]').should("have.class", "hidden");
    cy.get('[data-testid="presentation-redo"]').should("have.class", "hidden");
    cy.get("@getThemes").should("not.have.been.called");
    cy.get('[data-testid="version-history-trigger"]').click();
    cy.get("@getPresentationVersions").should("have.been.calledOnce");
    cy.get('[data-testid="version-restore"]').should("have.class", "hidden");

    openExportOptions();
    cy.get('[data-testid="export-pdf"]').click({ force: true });
    cy.wait("@exportPresentation");
    cy.get("@updatePresentationContent").should("not.have.been.called");
  });

  it("restores legacy header controls when the chat-first flag rolls back", () => {
    mountHeader(false, false);
    cy.get('[data-testid="presentation-title-edit"]').should("be.disabled");
    cy.get("@getThemes").should("not.have.been.called");

    mountHeader(false, true);
    cy.get('[data-testid="presentation-title-edit"]').should("not.be.disabled");
    cy.get('[data-testid="presentation-regenerate"]').should("not.have.class", "hidden");
    cy.get("@getThemes").should("have.been.calledOnce");
  });
});

describe("legacy authored presentation detection", () => {
  it("recognizes a saved authored deck from its slide sentinel", () => {
    expect(
      isAuthoredPresentation({
        mode: "template",
        theme: null,
        layout: { name: "legacy-layout" },
        slides: [
          {
            layout_group: "authored",
            layout: "authored:content",
            content: { __authored__: true },
          },
        ],
      }),
    ).to.equal(true);
  });

  it("does not classify a regular saved template as authored", () => {
    expect(
      isAuthoredPresentation({
        mode: "template",
        theme: null,
        slides: [
          {
            layout_group: "business-template",
            layout: "business-template:content",
            content: {},
          },
        ],
      }),
    ).to.equal(false);
  });
});
