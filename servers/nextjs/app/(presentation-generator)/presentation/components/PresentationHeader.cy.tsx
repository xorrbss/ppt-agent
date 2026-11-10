import React from "react";
import { Provider } from "react-redux";
import { AppRouterContext } from "next/dist/shared/lib/app-router-context.shared-runtime";
import { PathnameContext } from "next/dist/shared/lib/hooks-client-context.shared-runtime";
import { NextIntlClientProvider } from "next-intl";

import PresentationHeader from "./PresentationHeader";
import { notify } from "@/components/ui/sonner";
import { PresentationGenerationApi } from "../../services/api/presentation-generation";
import { store } from "@/store/store";
import {
  setPresentationData,
  setStreaming,
} from "@/store/slices/presentationGeneration";
import { isAuthoredPresentation } from "../utils/isAuthoredPresentation";
import koMessages from "@/i18n/messages/ko.json";
import type {
  WorkspaceCommandDispatcher,
  WorkspacePageRemakeRequester,
  WorkspaceQualityReviewRequester,
} from "../workspace";

const mountHeader = (
  isSvgDeck: boolean,
  mutationsEnabled = true,
  workspace?: {
    enabled: boolean;
    command: WorkspaceCommandDispatcher;
    qualityReview?: WorkspaceQualityReviewRequester;
    pageRemake?: WorkspacePageRemakeRequester;
  },
  svgTheme?: Record<string, unknown>,
  activeExportOperation?: "export.pptx" | "export.pdf" | null,
  sourcePreservingSvg = false,
) => {
  store.dispatch(
    setPresentationData({
      id: "deck-1",
      language: "en",
      layout: isSvgDeck ? null : { name: "template-v2", ordered: false, slides: [] },
      n_slides: 1,
      title: "deck",
      slides: [
        isSvgDeck
          ? { id: "slide-1", source_format: "svg" }
          : { id: "slide-1", layout: "template-v2" },
      ],
      theme: sourcePreservingSvg
        ? { svg: { sourcePreservation: { version: 1 } } }
        : svgTheme
          ? { svg: svgTheme }
          : null,
      mode: isSvgDeck || sourcePreservingSvg ? "svg" : "template",
      ...(isSvgDeck
        ? { capabilities: { editor: { slide_image_regenerate: true } } }
        : {}),
      version: "v2-standard",
    } as any)
  );
  store.dispatch(setStreaming(false));

  cy.mount(
    <NextIntlClientProvider locale="ko" messages={koMessages} timeZone="UTC">
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
              mutationsEnabled={mutationsEnabled}
              workspaceCommandsEnabled={workspace?.enabled ?? false}
              onWorkspaceCommand={workspace?.command}
              onWorkspaceQualityReviewRequest={workspace?.qualityReview}
              onWorkspacePageRemakeRequest={workspace?.pageRemake}
              activeExportOperation={activeExportOperation}
            />
          </Provider>
        </PathnameContext.Provider>
      </AppRouterContext.Provider>
    </NextIntlClientProvider>
  );
};

const openExportOptions = () => cy.get('[data-testid="export-trigger"]').click();
const openMoreOptions = () =>
  cy.get('[data-testid="presentation-more-trigger"]').click();

/**
 * Every export leaves the header as a durable workspace command. The direct
 * `POST /api/export-presentation` browser download was removed from the header
 * in 3a96901 ("feat(public-service): add durable generation and export path"),
 * so a request on that route now means the header regressed to the legacy path.
 */
const stubLegacyDirectExportRoute = () =>
  cy
    .intercept("POST", "/api/export-presentation", {
      statusCode: 500,
      body: { success: false, error: "legacy direct export must not be used" },
    })
    .as("legacyDirectExport");

const expectNoLegacyDirectExport = () => {
  cy.get("@legacyDirectExport.all").should("have.length", 0);
  cy.get("@notifyLoading").should("not.have.been.called");
  cy.get("@notifySuccess").should("not.have.been.called");
  cy.get("@notifyError").should("not.have.been.called");
};

describe("PresentationHeader workspace controls", () => {
  beforeEach(() => {
    cy.stub(notify, "loading").returns("direct-export").as("notifyLoading");
    cy.stub(notify, "success").as("notifySuccess");
    cy.stub(notify, "error").as("notifyError");
    cy.stub(PresentationGenerationApi, "updatePresentationContent")
      .as("updatePresentationContent")
      .resolves({});
  });

  it("routes page remake and quality into chat-first UI without a legacy dialog", () => {
    const workspaceCommand = cy.stub().as("workspaceCommand").resolves();
    const qualityReview = cy.stub().as("qualityReview");
    const pageRemake = cy.stub().as("pageRemake");
    stubLegacyDirectExportRoute();
    mountHeader(true, false, {
      enabled: true,
      command: workspaceCommand,
      qualityReview,
      pageRemake,
    });

    openMoreOptions();
    cy.get('[data-testid="page-remake-trigger"]').click();
    cy.get("@pageRemake").should("have.been.calledOnceWith", 0);
    // The More popover itself has role=dialog; only the legacy remake form is forbidden.
    cy.get('[data-testid="page-remake-preview-create"]').should("not.exist");

    cy.get('[data-testid="workspace-quality-review"]').click();
    cy.wrap(null).then(() => {
      expect(qualityReview).to.have.been.calledOnceWith(0);
      expect(workspaceCommand).not.to.have.been.called;
    });

    openExportOptions();
    cy.get('[data-testid="export-pdf"]').click({ force: true });
    openExportOptions();
    cy.get('[data-testid="export-pptx"]').click({ force: true });

    cy.wrap(null).then(() => {
      expect(workspaceCommand.getCall(0).args).to.deep.equal([
        "PDF로 내보내기",
        { commandType: "export.pdf", context: { format: "pdf" } },
      ]);
      expect(workspaceCommand.getCall(1).args).to.deep.equal([
        "PPTX로 내보내기",
        { commandType: "export.pptx", context: { format: "pptx" } },
      ]);
      expect(workspaceCommand).to.have.been.calledTwice;
    });
    expectNoLegacyDirectExport();
    cy.get("@updatePresentationContent").should("not.have.been.called");
  });

  it("asks before exporting a deck that still has pages flagged for review", () => {
    const workspaceCommand = cy.stub().as("workspaceCommand").resolves();
    stubLegacyDirectExportRoute();
    mountHeader(
      true,
      false,
      { enabled: true, command: workspaceCommand },
      {
        flaggedSlides: [
          { index: 0, slideId: "slide-1", reason: "author:placeholder", classification: "review_required" },
        ],
      },
    );

    openExportOptions();
    cy.get('[data-testid="export-pdf"]').click({ force: true });
    cy.get('[data-testid="export-review-confirm"]')
      .should("contain.text", "검토가 필요한 쪽 1개가 포함되어 있습니다. 그대로 내보낼까요?");
    cy.wrap(null).then(() => expect(workspaceCommand).not.to.have.been.called);

    cy.get('[data-testid="export-review-confirm-proceed"]').click();
    cy.wrap(null).then(() => {
      expect(workspaceCommand).to.have.been.calledOnce;
      expect(workspaceCommand.getCall(0).args[1].commandType).to.equal("export.pdf");
    });
  });

  it("leaves a failed export to the durable workspace operation", () => {
    const workspaceCommand = cy
      .stub()
      .as("workspaceCommand")
      .rejects(new Error("export admission failed"));
    stubLegacyDirectExportRoute();
    mountHeader(true, false, { enabled: true, command: workspaceCommand });

    openExportOptions();
    cy.get('[data-testid="export-pptx"]').click({ force: true });

    cy.wrap(null).then(() => {
      expect(workspaceCommand.getCall(0).args).to.deep.equal([
        "PPTX로 내보내기",
        { commandType: "export.pptx", context: { format: "pptx" } },
      ]);
      expect(workspaceCommand).to.have.been.calledOnce;
    });
    // The timeline owns the failure and its retry affordance, so a rejected
    // dispatch must not raise a header toast or fall back to a direct download.
    expectNoLegacyDirectExport();
    cy.get("@updatePresentationContent").should("not.have.been.called");
  });

  it("blocks unsupported source-preserving SVG PDF but keeps PPTX available", () => {
    const command = cy.stub().as("svgCommand").resolves();
    mountHeader(false, true, { enabled: true, command }, undefined, null, true);
    openExportOptions();
    cy.get('[data-testid="export-pdf"]').should("be.disabled");
    cy.get('[data-testid="export-pptx"]').should("be.enabled");
    cy.get("@svgCommand").should("not.have.been.called");
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

  it("keeps the header read-only until workspace commands are available", () => {
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
    cy.get('[data-testid="presentation-undo"]').should("not.exist");
    cy.get('[data-testid="presentation-redo"]').should("not.exist");
    openMoreOptions();
    cy.get('[data-testid="presentation-regenerate"]').should("not.exist");
    cy.get('[data-testid="version-history-trigger"]').click();
    cy.get("@getPresentationVersions").should("have.been.calledOnce");
    cy.get('[data-testid="version-restore"]').should("have.class", "hidden");

    cy.get('[data-testid="export-trigger"]').should("be.disabled");
    cy.get("@updatePresentationContent").should("not.have.been.called");
  });

  it("disables the export trigger with a Korean reason while an export operation is in flight", () => {
    const workspaceCommand = cy.stub().as("workspaceCommand").resolves();
    mountHeader(
      false,
      true,
      { enabled: true, command: workspaceCommand },
      undefined,
      "export.pptx",
    );

    cy.get('[data-testid="export-trigger"]')
      .should("be.disabled")
      .and("have.attr", "aria-label", koMessages.exportMenu.triggerDisabledExportInFlightAria)
      .and("have.attr", "title", koMessages.exportMenu.triggerDisabledExportInFlightAria);
    cy.get("@workspaceCommand").should("not.have.been.called");
  });

  it("re-enables the export trigger once no export operation is in flight", () => {
    const workspaceCommand = cy.stub().as("workspaceCommand").resolves();
    mountHeader(
      false,
      true,
      { enabled: true, command: workspaceCommand },
      undefined,
      null,
    );

    cy.get('[data-testid="export-trigger"]')
      .should("not.be.disabled")
      .and("have.attr", "aria-label", koMessages.exportMenu.triggerAria)
      .and("not.have.attr", "title");
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
