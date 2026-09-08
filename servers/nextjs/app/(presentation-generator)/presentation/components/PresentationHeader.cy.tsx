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
  editableHybridProjection?: Record<string, unknown>,
  authoredQualityGate?: Record<string, unknown>,
) => {
  store.dispatch(
    setPresentationData({
      id: "deck-1",
      language: "en",
      layout: isAuthoredDeck ? null : { name: "template-v2", ordered: false, slides: [] },
      n_slides: 1,
      title: "deck",
      slides: [{ id: "slide-1", layout: "template-v2" }],
      theme:
        editableHybridProjection || authoredQualityGate
          ? {
              ...(editableHybridProjection
                ? { authored_vnext: editableHybridProjection }
                : {}),
              ...(authoredQualityGate
                ? { authoredQualityGate }
                : {}),
            }
          : null,
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

const CAPABILITY_IDS = [
  "shape.gradient",
  "shape.shadow",
  "layout.transform",
  "layout.flex",
  "layout.grid",
  "shape.borderRadius",
  "shape.opacity",
  "shape.clip",
];
const RECEIPT_IDS = ["cjk", "chart", "zBand", "degradation"];
const FIXTURE_DIGEST = `sha256:${"a".repeat(64)}`;

/**
 * A hybrid-ready capability projection on the retained v1 compatibility route.
 * The header only renders the Hybrid options when the server publishes a
 * hybrid-ready projection. Pinning the projection keeps this spec about the
 * export labels instead of about which readiness endpoint happens to answer;
 * the bounded v3 route production actually serves has its own case below.
 */
const hybridReadyCapabilityProjection = {
  schemaVersion: "workbench-capability-projection/v1",
  presentationId: "deck-1",
  presentationRevision: 3,
  canonicalMode: "bundleGraph",
  canonicalDigest: FIXTURE_DIGEST,
  manifestDigest: FIXTURE_DIGEST,
  degradedFidelityOnly: false,
  fidelity: { state: "ready", receiptDigest: FIXTURE_DIGEST },
  hybrid: {
    text: { state: "ready", receiptDigest: FIXTURE_DIGEST },
    image: { state: "ready", receiptDigest: FIXTURE_DIGEST },
    shape: { state: "ready", receiptDigest: FIXTURE_DIGEST },
  },
  capabilityMatrix: CAPABILITY_IDS.map((capabilityId) => ({
    capabilityId,
    state: "ready",
    decision: "native",
    reasonCode: "capability.ready",
    receiptDigest: FIXTURE_DIGEST,
  })),
  receipts: RECEIPT_IDS.map((receiptId) => ({
    receiptId,
    state: "ready",
    decision: "native",
    reasonCode: "receipt.ready",
    receiptDigest: FIXTURE_DIGEST,
  })),
  readiness: {
    authoredCreationReady: true,
    fidelityExportReady: true,
    fullNativeParityReady: false,
    independentVnextGenerationReady: false,
    promotionReady: false,
  },
  killSwitches: [],
  elements: [],
  projectionDigest: FIXTURE_DIGEST,
};

const stubHybridReadyCapabilityProjection = () => {
  // 501 is the "explicitly unsupported" answer that lets the client fall back
  // one route at a time; anything else fails closed instead.
  cy.intercept("GET", "**/authored-emission/capability-projection-v3", {
    statusCode: 501,
    body: {},
  }).as("capabilityProjectionV3");
  cy.intercept("GET", "**/authored-emission/capability-projection-v2", {
    statusCode: 501,
    body: {},
  }).as("capabilityProjectionV2");
  cy.intercept("GET", "**/authored-emission/capability-projection", {
    statusCode: 200,
    body: hybridReadyCapabilityProjection,
  }).as("capabilityProjection");
};

/**
 * The bounded release readiness the server actually publishes today: every
 * receipt still missing, the renderer unattested, and `nativeSubsetStatus`
 * pinned to "notApplicable" because this profile never claims the native
 * subset. None of that withholds the diagnostic Hybrid export, so the header
 * must still offer it. Freshness is checked against the wall clock, so the
 * timestamps are relative rather than pinned.
 */
const boundedReleaseV3Projection = () => {
  const capabilityIds = [
    "ci.requiredChecks",
    "evidence.visual",
    "receipt.degradation",
    "renderer.attestation",
  ];
  return {
    schemaVersion: "workbench-capability-projection/v3",
    presentationId: "deck-1",
    presentationRevision: 7,
    canonicalMode: "bundleGraph",
    canonicalDigest: FIXTURE_DIGEST,
    manifestDigest: FIXTURE_DIGEST,
    degradedFidelityOnly: false,
    releaseProfile: {
      schemaVersion: "authored-bounded-hybrid-release-readiness/v3",
      profileId: "profile:bounded-hybrid",
      platformScope: { platform: "windows", renderer: "external-worker", archetypes: ["title"] },
      evaluatedAt: new Date(Date.now() - 60 * 60_000).toISOString(),
      expiresAt: new Date(Date.now() + 24 * 60 * 60_000).toISOString(),
      deckEvidenceReady: false,
      serializerProfileReady: false,
      nativeSubsetReady: false,
      releaseCommitReady: false,
      promotionReady: false,
      deploymentAuthorized: false,
      fullNativeParityStatus: "notApplicable",
      independentVnextGenerationStatus: "notApplicable",
      deckEvidenceCapabilityIds: ["evidence.visual"],
      serializerProfileCapabilityIds: ["receipt.degradation", "renderer.attestation"],
      nativeSubsetStatus: "notApplicable",
      nativeSubsetCapabilityIds: [],
      unsupportedNativeCapabilityIds: [
        "receipt.chart",
        "receipt.cjk",
        "receipt.imageCrop",
        "receipt.table",
        "receipt.zBand",
      ],
      releaseCommitCapabilityIds: ["ci.requiredChecks"],
      allRequiredCapabilityIds: [...capabilityIds].sort(),
      profileDefinitionDigest: FIXTURE_DIGEST,
      deckEvidenceReceiptDigests: [],
      serializerProfileReceiptDigests: [],
      nativeSubsetReceiptDigests: [],
      releaseCommitReceiptDigests: [],
      promotionReceiptDigests: [],
      reasonCode: "release_evidence_incomplete",
    },
    capabilities: capabilityIds.map((capabilityId, index) => ({
      capabilityId,
      state: "ready",
      reasonCode: "receipt_verified",
      receiptDigest: `sha256:${String(index + 1).repeat(64)}`,
      scope: { platform: "windows", renderer: "external-worker", archetypes: ["title"] },
      serializerId: "presenton-authored-hybrid",
      serializerVersion: "v3",
      expiresAt: new Date(Date.now() + 24 * 60 * 60_000).toISOString(),
    })),
    rendererAttestation: {
      state: "unattested",
      reasonCode: "renderer_attestation_missing",
      attestationDigest: null,
      environment: { os: "windows", boundary: "external-worker" },
      expiresAt: null,
    },
    killSwitches: [],
    releaseAuthorityReceiptDigests: [],
  };
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

  it("links only an exact non-blocked editable-Hybrid Scene bridge", () => {
    mountHeader(true, true, undefined, {
      schemaVersion: "authored-editable-hybrid-scene-projection/v1",
      profileId: "authored-editable-hybrid-ko-en-basic-v1",
      deckId: "deck:normal-ai-publication",
      status: "shadow",
    });
    cy.get('[data-testid="editable-hybrid-workbench-bridge"]').should("contain.text", "편집 가능한 Hybrid MVP");
    cy.get('[data-testid="editable-hybrid-workbench-link"]').should("have.attr", "href", "/authored-vnext/workbench?authoredDeckId=deck%3Anormal-ai-publication");
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

    cy.wrap(null).then(() => {
      expect(workspaceCommand.getCall(0).args).to.deep.equal([
        "PDF로 내보내기",
        { commandType: "export.pdf", context: { format: "pdf" } },
      ]);
      // Fidelity carries its mode and an explicit font-embedding decision into
      // the durable operation rather than into a direct browser download.
      expect(workspaceCommand.getCall(1).args).to.deep.equal([
        "PPTX로 내보내기",
        {
          commandType: "export.pptx",
          context: {
            format: "pptx",
            pptx_mode: "fidelity",
            font_embedding: false,
          },
        },
      ]);
      expect(workspaceCommand).to.have.been.calledTwice;
    });
    expectNoLegacyDirectExport();
    cy.get("@updatePresentationContent").should("not.have.been.called");
  });

  it("leaves a failed Authored export to the durable workspace operation", () => {
    const workspaceCommand = cy
      .stub()
      .as("workspaceCommand")
      .rejects(new Error("Hybrid export quality evidence is incomplete"));
    stubLegacyDirectExportRoute();
    mountHeader(true, false, { enabled: true, command: workspaceCommand });

    openExportOptions();
    cy.get('[data-testid="export-pptx"]').click({ force: true });

    cy.wrap(null).then(() => {
      expect(workspaceCommand.getCall(0).args).to.deep.equal([
        "PPTX로 내보내기",
        {
          commandType: "export.pptx",
          context: {
            format: "pptx",
            pptx_mode: "fidelity",
            font_embedding: false,
          },
        },
      ]);
      expect(workspaceCommand).to.have.been.calledOnce;
    });
    // The timeline owns the failure and its retry affordance, so a rejected
    // dispatch must not raise a header toast or fall back to a direct download.
    expectNoLegacyDirectExport();
    cy.get("@updatePresentationContent").should("not.have.been.called");
  });

  it("labels Hybrid fallback and font-free compatibility explicitly", () => {
    const workspaceCommand = cy.stub().as("workspaceCommand").resolves();
    stubHybridReadyCapabilityProjection();
    mountHeader(true, false, { enabled: true, command: workspaceCommand });
    cy.wait("@capabilityProjection");

    openExportOptions();
    cy.get('[data-testid="export-pptx"]')
      .should("contain.text", "Fidelity PPTX")
      .and("contain.text", "1280×720 raster")
      .and("contain.text", "접근 가능한 텍스트·개체 편집은 제공하지 않으며");
    // The visible headline and the aria-label are the durable contract here.
    // The explanatory note under each option is marketing prose that has been
    // reworded three times (5218198f3, 2800b3cf1, 8e597e2a9), and asserting on
    // it made this test fail on wording rather than on behaviour.
    cy.get('[data-testid="authored-export-hybrid"]')
      .should("contain.text", "raster fallback (기본)")
      .and("have.attr", "aria-label")
      .and("contain", "글꼴 포함 기본값");
    cy.get('[data-testid="authored-export-hybrid-compatibility"]')
      .should("contain.text", "raster fallback 호환 모드")
      .and("have.attr", "aria-label")
      .and("contain", "글꼴 미포함");
  });

  it("offers Hybrid on the bounded v3 readiness route the server publishes", () => {
    const workspaceCommand = cy.stub().as("workspaceCommand").resolves();
    cy.intercept("GET", "**/authored-emission/capability-projection-v3", {
      statusCode: 200,
      body: boundedReleaseV3Projection(),
    }).as("capabilityProjectionV3");
    mountHeader(true, false, { enabled: true, command: workspaceCommand });
    cy.wait("@capabilityProjectionV3");

    openExportOptions();
    cy.get('[data-testid="authored-export-hybrid"]').should("be.visible");
    cy.get('[data-testid="authored-export-hybrid-compatibility"]').should("be.visible");
    cy.get('[data-testid="authored-export-hybrid-unavailable"]').should("not.exist");
    // The uncertified scope is disclosed in the status line, not by withholding
    // the export.
    cy.get('[data-testid="authored-capability-status"]').should(
      "contain.text",
      "native subset은 지원하지 않으며",
    );
  });

  it("names the vision-QA pass when the deck's own receipt withholds Hybrid", () => {
    const workspaceCommand = cy.stub().as("workspaceCommand").resolves();
    cy.intercept("GET", "**/authored-emission/capability-projection-v3", {
      statusCode: 200,
      body: boundedReleaseV3Projection(),
    }).as("capabilityProjectionV3");
    // A deck generated without vision-QA: the renderer admits Hybrid, but the
    // export gate waives a pending visual review for Fidelity only.
    mountHeader(true, false, { enabled: true, command: workspaceCommand }, undefined, {
      qualityReceiptV3: {
        gatePassed: false,
        blockers: ["visualReview.required_failed"],
      },
    });
    cy.wait("@capabilityProjectionV3");

    openExportOptions();
    cy.get('[data-testid="export-pptx"]').should("contain.text", "Fidelity PPTX");
    cy.get('[data-testid="authored-export-hybrid"]').should("not.exist");
    cy.get('[data-testid="authored-export-hybrid-unavailable"]')
      .should("contain.text", "시각 검수")
      .and("contain.text", "vision-QA");
  });

  it("offers Hybrid once the deck's receipt has passed", () => {
    const workspaceCommand = cy.stub().as("workspaceCommand").resolves();
    cy.intercept("GET", "**/authored-emission/capability-projection-v3", {
      statusCode: 200,
      body: boundedReleaseV3Projection(),
    }).as("capabilityProjectionV3");
    mountHeader(true, false, { enabled: true, command: workspaceCommand }, undefined, {
      qualityReceiptV3: { gatePassed: true, blockers: [] },
    });
    cy.wait("@capabilityProjectionV3");

    openExportOptions();
    cy.get('[data-testid="authored-export-hybrid"]').should("be.visible");
    cy.get('[data-testid="authored-export-hybrid-unavailable"]').should("not.exist");
  });

  it("withholds Hybrid and keeps Fidelity when the capability projection is unavailable", () => {
    const workspaceCommand = cy.stub().as("workspaceCommand").resolves();
    cy.intercept("GET", "**/authored-emission/capability-projection*", {
      statusCode: 503,
      body: { code: "capability_projection_unavailable" },
    }).as("capabilityProjectionUnavailable");
    mountHeader(true, false, { enabled: true, command: workspaceCommand });
    cy.wait("@capabilityProjectionUnavailable");

    openExportOptions();
    cy.get('[data-testid="export-pptx"]').should("contain.text", "Fidelity PPTX");
    cy.get('[data-testid="authored-export-hybrid-unavailable"]').should(
      "contain.text",
      "Hybrid 비활성",
    );
    cy.get('[data-testid="authored-export-hybrid"]').should("not.exist");
    cy.get('[data-testid="authored-export-hybrid-compatibility"]').should(
      "not.exist",
    );
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
