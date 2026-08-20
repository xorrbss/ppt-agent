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
import enMessages from "@/i18n/messages/en.json";
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
      layout: isAuthoredDeck ? null : { name: "template-v2", ordered: false, slides: [] },
      n_slides: 1,
      title: "deck",
      slides: [{ id: "slide-1", layout: "template-v2" }],
      theme: null,
      mode: isAuthoredDeck ? "authored" : "template",
      version: "v2-standard",
    } as any)
  );
  store.dispatch(setStreaming(false));

  cy.mount(
    <NextIntlClientProvider locale="en" messages={enMessages} timeZone="UTC">
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
              workspaceCommandsEnabled={workspace?.enabled ?? false}
              onWorkspaceCommand={workspace?.command}
              onWorkspaceQualityReviewRequest={workspace?.qualityReview}
              onWorkspacePageRemakeRequest={workspace?.pageRemake}
            />
          </Provider>
        </PathnameContext.Provider>
      </AppRouterContext.Provider>
    </NextIntlClientProvider>
  );
};

const openExportOptions = () => cy.get('[data-testid="export-trigger"]').click();

describe("PresentationHeader workspace controls", () => {
  beforeEach(() => {
    cy.stub(PresentationGenerationApi, "updatePresentationContent")
      .as("updatePresentationContent")
      .resolves({});
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
    cy.get('[data-testid="export-pptx"]').click({ force: true });
    openExportOptions();
    cy.get('[data-testid="authored-export-hybrid"]').click({ force: true });

    cy.wrap(null).then(() => {
      expect(workspaceCommand.getCall(0).args).to.deep.equal([
        "PDF로 내보내기",
        { commandType: "export.pdf", context: { format: "pdf" } },
      ]);
      expect(workspaceCommand.getCall(1).args).to.deep.equal([
        "PPTX로 내보내기",
        { commandType: "export.pptx", context: { format: "pptx" } },
      ]);
      expect(workspaceCommand.getCall(2).args).to.deep.equal([
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
    cy.get('[data-testid="presentation-regenerate"]').should("have.class", "hidden");
    cy.get('[data-testid="presentation-undo"]').should("have.class", "hidden");
    cy.get('[data-testid="presentation-redo"]').should("have.class", "hidden");
    cy.get('[data-testid="version-history-trigger"]').click();
    cy.get("@getPresentationVersions").should("have.been.calledOnce");
    cy.get('[data-testid="version-restore"]').should("have.class", "hidden");

    cy.get('[data-testid="export-trigger"]').should("be.disabled");
    cy.get("@updatePresentationContent").should("not.have.been.called");
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
