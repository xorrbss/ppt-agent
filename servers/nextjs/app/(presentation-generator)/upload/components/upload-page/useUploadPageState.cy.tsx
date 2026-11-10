import React, { useRef, useState } from "react";
import { mount } from "cypress/react";
import { Provider } from "react-redux";
import { AppRouterContext } from "next/dist/shared/lib/app-router-context.shared-runtime";
import { PathnameContext } from "next/dist/shared/lib/hooks-client-context.shared-runtime";
import { NextIntlClientProvider } from "next-intl";
import koMessages from "@/i18n/messages/ko.json";

import { makeStore } from "@/store/store";
import {
  setPptGenUploadState,
  setSourceContentMode,
} from "@/store/slices/presentationGenUpload";

import { useUploadPageState } from "./useUploadPageState";

/**
 * Regression coverage for the source-content "one click" run in
 * `startPresentation`: analysis raises the full-screen overlay and generation
 * keeps it up, so every path that gives up in between must lower it again.
 * An earlier revision stranded the screen on "원본 콘텐츠를 분석하는 중…" when
 * a give-up path reset `startInFlightRef` but never the overlay.
 *
 * This mounts the hook directly (not through `UploadPage`) so the test does
 * not have to drive the template gallery just to
 * reach `startPresentation`; that mirrors `useSupportingFiles` being tested
 * the same way inside UploadPage.cy.tsx.
 */

const SOURCE_CONTENT_FILE = new File(["pptx-bytes"], "deck.pptx", {
  type: "application/vnd.openxmlformats-officedocument.presentationml.presentation",
});

const UploadPageStateHarness = () => {
  const [files] = useState<File[]>(() => [SOURCE_CONTENT_FILE]);
  const filesRef = useRef<File[]>(files);
  filesRef.current = files;

  const { config, loadingState, handleGeneratePresentation } = useUploadPageState({
    files,
    filesRef,
    selectedThemeId: null,
  });

  return (
    <>
      <button
        type="button"
        data-testid="harness-generate"
        onClick={() => {
          void handleGeneratePresentation();
        }}
      >
        generate
      </button>
      <span data-testid="harness-is-loading">{String(loadingState.isLoading)}</span>
      <span data-testid="harness-message">{loadingState.message}</span>
      <span data-testid="harness-page-size">{config.pageSize}</span>
      <span data-testid="harness-orientation">{config.orientation}</span>
    </>
  );
};

const mountHarness = ({
  authoredStyle = "default",
}: {
  authoredStyle?: string;
} = {}) => {
  const reduxStore = makeStore();
  reduxStore.dispatch(setSourceContentMode(true));
  reduxStore.dispatch(setPptGenUploadState({ authoredStyle }));

  cy.mount(
    <NextIntlClientProvider locale="ko" messages={koMessages} timeZone="Asia/Seoul">
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
      <PathnameContext.Provider value="/upload">
        <Provider store={reduxStore}>
          <UploadPageStateHarness />
        </Provider>
      </PathnameContext.Provider>
    </AppRouterContext.Provider>
    </NextIntlClientProvider>,
  );
};

describe("useUploadPageState source-content abort paths", () => {
  const documentAsset = {
    asset_id: "asset-source-content-1",
    kind: "document",
    state: "ready",
    media_type:
      "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    size_bytes: SOURCE_CONTENT_FILE.size,
    sha256: "0".repeat(64),
    version: 1,
  };

  const analysisResponse = {
    contractVersion: "source-content-analysis/v1",
    status: "ready",
    capabilities: { pptx: true, pdf: false, docx: false, ppt: false },
    manifestPin: {
      manifestId: "manifest-source-content-1",
      digest: `sha256:${"a".repeat(64)}`,
    },
    referenceSnapshotBinding: {
      referenceSnapshotId: "snapshot-source-content-1",
      providerId: "uploaded_document",
      sourceRevision: "1",
    },
    documentCanvas: {
      contractVersion: "document-canvas/v1",
      logicalSize: { width: 1188, height: 840 },
      physicalSize: { widthEmu: 10691813, heightEmu: 7559675 },
      safeArea: { x: 60, y: 60, width: 1068, height: 720 },
    },
    summary: {
      sourceType: "pptx",
      sourceUnits: 5,
      textBlocks: 12,
      tables: 0,
      charts: 0,
      uniqueImages: 2,
      imageOccurrences: 2,
      rasterFallbackBlocks: 0,
    },
    objectInventory: {
      discoveredObjectCount: 12,
      classifiedObjectCount: 12,
      contentBearingObjectCount: 12,
      unsupportedContentObjectIds: [],
      unclassifiedObjectIds: [],
      externalRelationshipCount: 0,
    },
  };

  beforeEach(() => {
    // The generation guard asks for in-flight generations before every start.
    cy.intercept("GET", "**/workspace/generation-activity", {
      statusCode: 200,
      body: { server_time: "2026-10-22T08:35:00+00:00", items: [] },
    }).as("generationActivity");
    cy.intercept("POST", "**/api/v1/ppt/assets/documents", {
      statusCode: 200,
      body: documentAsset,
    }).as("uploadAsset");
    cy.intercept("POST", "**/api/v1/ppt/references/snapshots", {
      statusCode: 200,
      body: { snapshot_id: "snapshot-source-content-1" },
    }).as("createSnapshot");
    cy.intercept("POST", "**/api/v1/ppt/source-content/v1/analyze", {
      statusCode: 200,
      body: analysisResponse,
    }).as("analyzeSourceContent");
  });

  it("pauses on the generation guard when another generation is active and starts nothing", () => {
    cy.intercept("GET", "**/workspace/generation-activity", {
      statusCode: 200,
      body: {
        server_time: "2026-10-22T08:35:00+00:00",
        items: [{
          operation_id: "op-1", presentation_id: "p-1", state: "running",
          presentation_title: "Running deck", source_file_name: null,
          pages: { authored: 58, rendered: 57, persisted: 0, total: 100 },
          stage: "author", queue_position: null, capacity_waiting: false,
          created_at: "2026-10-22T08:00:00+00:00", started_at: "2026-10-22T08:00:01+00:00",
          last_progress_at: "2026-10-22T08:30:00+00:00",
          operation_version: 4, presentation_revision: 2,
        }],
      },
    }).as("activeGeneration");
    mountHarness();

    cy.get('[data-testid="harness-generate"]').click();
    cy.wait("@activeGeneration");
    // The harness does not render the dialog; the run must simply be held
    // before any analysis request is made.
    cy.get("@analyzeSourceContent.all").should("have.length", 0);
  });

  it("rejects an unavailable source SVG combination after analyzing the default A4 input", () => {
    cy.intercept("GET", "**/api/v1/ppt/authored/canvas-capabilities*", {
      statusCode: 200,
      body: {
        a4: { enabled: false, reason: "document_canvas_a4_not_available" },
        sourceContentSvg: {
          enabled: false,
          reason: "svg_source_content_not_supported",
          canvasSource: "source-manifest",
          inputFormats: ["pptx"],
          productVerified: false,
        },
      },
    }).as("canvasCapabilities");
    mountHarness();

    cy.get('[data-testid="harness-generate"]').click();
    cy.wait("@canvasCapabilities");

    cy.get("@uploadAsset.all").should("have.length", 1);
    cy.get("@analyzeSourceContent.all").should("have.length", 1);
    cy.get('[data-testid="harness-is-loading"]').should("have.text", "false");
  });

  it("accepts default A4, preserves the chart reason and reanalyses the owned source SVG snapshot", () => {
    cy.intercept("GET", "**/api/v1/ppt/authored/canvas-capabilities*", {
      statusCode: 200,
      body: {
        a4: { enabled: false, reason: "document_canvas_a4_not_available" },
        sourceContentSvg: {
          enabled: true,
          reason: null,
          canvasSource: "source-manifest",
          inputFormats: ["pptx"],
          productVerified: false,
        },
      },
    }).as("canvasCapabilities");
    cy.intercept("POST", "**/api/v1/ppt/source-content/v1/analyze", {
      statusCode: 200,
      body: {
        ...analysisResponse,
        summary: { ...analysisResponse.summary, charts: 1 },
      },
    }).as("freshAnalyzeSourceContent");
    mountHarness();

    cy.get('[data-testid="harness-page-size"]').should("have.text", "A4");
    cy.get('[data-testid="harness-orientation"]').should("have.text", "landscape");
    cy.get('[data-testid="harness-generate"]').click();
    cy.wait("@freshAnalyzeSourceContent");
    cy.get('[data-testid="harness-is-loading"]').should("have.text", "false");
    cy.get('[data-testid="harness-message"]').should("contain", "원본에 차트");
    cy.get('[data-testid="harness-message"]').should("not.contain", "설정을 확인한 뒤 다시 진행");

    cy.get('[data-testid="harness-generate"]').click();
    cy.wait("@freshAnalyzeSourceContent");

    cy.get("@uploadAsset.all").should("have.length", 1);
    cy.get("@freshAnalyzeSourceContent.all").should("have.length", 2);
    cy.get("@createSnapshot.all").should("have.length", 1);
  });

  it("sends selected A4 for a source SVG PPTX without an A4 source canvas", () => {
    cy.intercept("GET", "**/api/v1/ppt/authored/canvas-capabilities*", {
      statusCode: 200,
      body: {
        a4: { enabled: true, reason: null },
        sourceContentSvg: {
          enabled: true,
          reason: null,
          canvasSource: "selected-a4",
          inputFormats: ["pptx"],
          productVerified: false,
        },
      },
    }).as("canvasCapabilities");
    cy.intercept("POST", "**/api/v1/ppt/source-content/v1/analyze", {
      statusCode: 200,
      body: { ...analysisResponse, documentCanvas: null },
    }).as("freshAnalyzeSourceContent");
    cy.intercept("POST", "**/api/v1/ppt/presentation/create", {
      statusCode: 200,
      body: { id: "presentation-a4-source-svg" },
    }).as("createPresentation");
    mountHarness();

    cy.get('[data-testid="harness-generate"]').click();
    cy.wait("@freshAnalyzeSourceContent");
    cy.wait("@createPresentation")
      .its("request.body")
      .should("include", { page_size: "A4", orientation: "landscape" });
  });
});
