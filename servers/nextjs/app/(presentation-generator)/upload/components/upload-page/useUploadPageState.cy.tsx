import React, { useRef, useState } from "react";
import { mount } from "cypress/react";
import { Provider } from "react-redux";
import { AppRouterContext } from "next/dist/shared/lib/app-router-context.shared-runtime";
import { PathnameContext } from "next/dist/shared/lib/hooks-client-context.shared-runtime";

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
 * Commit 6da74cf8a shipped that flow with one give-up path (a rejected
 * `ensureSourceContentAuthoredStyle`) resetting `startInFlightRef` but never
 * the overlay, stranding the screen on "원본 콘텐츠를 분석하는 중…" forever.
 *
 * This mounts the hook directly (not through `UploadPage`) so the test does
 * not have to drive the master-catalog dialog or template gallery just to
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

  const { loadingState, handleGeneratePresentation } = useUploadPageState({
    files,
    filesRef,
    selectedThemeId: null,
    masterFeatureEnabled: false,
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
    </>
  );
};

const mountHarness = () => {
  const reduxStore = makeStore();
  reduxStore.dispatch(setSourceContentMode(true));
  // "brand-report" is never part of the mocked style catalog below, so
  // `ensureSourceContentAuthoredStyle` fails deterministically after a
  // successful analysis -- the exact shape of the shipped regression.
  reduxStore.dispatch(setPptGenUploadState({ authoredStyle: "brand-report" }));

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
      <PathnameContext.Provider value="/upload">
        <Provider store={reduxStore}>
          <UploadPageStateHarness />
        </Provider>
      </PathnameContext.Provider>
    </AppRouterContext.Provider>,
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
    // Zero tables/charts/rasterFallbackBlocks means `sourceContentLosses`
    // reports nothing -- `confirmLosses` resolves without ever opening the
    // loss dialog, so the run reaches `ensureSourceContentAuthoredStyle`
    // inside the same click.
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
    // The style catalog never contains "brand-report" (only the client-side
    // default gets injected for an empty server list), so
    // `ensureSourceContentAuthoredStyle` returns false every time this fires.
    cy.intercept("GET", "**/api/v1/ppt/authored/styles", {
      statusCode: 200,
      body: [],
    }).as("getStyles");
  });

  it("clears the overlay and releases the run when the authored style check fails after analysis", () => {
    mountHarness();

    cy.get('[data-testid="harness-generate"]').click();
    cy.wait("@analyzeSourceContent");
    cy.wait("@getStyles");

    // The bug: the overlay raised by analysis ("원본 콘텐츠를 분석하는 중…")
    // never came back down because the give-up path only cleared
    // `startInFlightRef`, not `loadingState`.
    cy.get('[data-testid="harness-is-loading"]').should("have.text", "false");
    cy.get('[data-testid="harness-message"]').should("have.text", "");

    // The other half of the bug: `startInFlightRef` must actually be
    // released. A second click reuses the cached admission (same file
    // identity, so `analyze` does not run again) but still re-checks the
    // authored style -- if the first run were still "in flight", this click
    // would be swallowed by the early `if (startInFlightRef.current) return;`
    // guard and `getStyles` would never fire a second time.
    cy.get('[data-testid="harness-generate"]').click();
    cy.get("@getStyles.all").should("have.length", 2);
    cy.get("@analyzeSourceContent.all").should("have.length", 1);

    cy.get('[data-testid="harness-is-loading"]').should("have.text", "false");
  });
});
