import React from "react";
import { mount } from "cypress/react";
import { NextIntlClientProvider } from "next-intl";

import koMessages from "@/i18n/messages/ko.json";
import { DEFAULT_MASTER_CONTRACT } from "@/lib/authored-vnext/master/fixtures";

import AuthoredVnextFlowClient from "./AuthoredVnextFlowClient";

// The component reads the active UI locale via useTranslations,
// exactly as it does in the app under the root provider. cy.mount
// (registered in cypress/support/component.ts) wraps nothing, so every mount
// here goes through this local helper instead of cy.mount directly.
function mountWithIntl(ui: React.ReactElement) {
  mount(
    <NextIntlClientProvider locale="ko" messages={koMessages} timeZone="Asia/Seoul">
      {ui}
    </NextIntlClientProvider>,
  );
}

const digestA = `sha256:${"a".repeat(64)}`;
const digestB = `sha256:${"b".repeat(64)}`;
const SLIDE_COUNT = 90;

// Backs `result.deck.slides[i].slide` in the generation-result envelope. Only
// used by AuthoredVnextFlowClient's post-generate progress dispatch
// (`item.slide.slideId` / `item.slide.revision`); the Workbench canvas itself
// is built from the separate Scene IR below.
function validatedSlide(index: number) {
  const layoutId = index === 0
    ? "layout:cover"
    : index === 1 || index % 15 === 0
      ? "layout:section"
      : index % 10 === 5
        ? "layout:long-title"
        : "layout:standard";
  const text = index === 0 ? "접근 가능한 Authored 흐름" : `슬라이드 ${index + 1}`;
  return {
    schemaVersion: "validated-slidespec/v2",
    slide: {
      schemaVersion: "slidespec/v2",
      slideId: `slide:${index + 1}`,
      revision: 1,
      layoutId,
      title: {
        text,
        lines: [text],
        lineBreakPolicy: "canonical-explicit-uax14-v1",
        uax14Version: "17.0.0",
        tailoring: "ko-space-first-with-id-fallback-v1",
        kinsokuPolicy: "cjk-no-leading-closing-no-trailing-opening",
      },
      body: `${index + 1}번째 근거와 실행 항목`,
      ownership: { locked: false, modelWritable: true },
      masterDigest: digestB,
      titleFit: {
        engineVersion: "authored-vnext-title-fit/1",
        layoutId,
        explicitLines: [text],
        fontFamily: "Malgun Gothic",
        fontManifestDigest: digestB,
        chosenFontPt: 34,
        margins: { leftPt: 7.2, rightPt: 7.2, topPt: 3.6, bottomPt: 3.6 },
        clipped: false,
        violations: [],
      },
      contentDigest: digestA,
      specDigest: digestB,
    },
    checkpoint: { masterDigest: digestB, sourceDigest: digestA, bodySlotId: "body" },
  };
}

// Backs `result.deck.scene.slides` — the Scene IR the Workbench canvas
// (LayeredSlideCanvas) actually renders and edits. Each slide carries only a
// title and a body text element, which is all this flow edits.
function sceneSlide(index: number, titleText = index === 0 ? "접근 가능한 Authored 흐름" : `슬라이드 ${index + 1}`) {
  return {
    schemaVersion: "slide-scene-ir/v1",
    slideId: `slide:${index + 1}`,
    revision: 1,
    slideDigest: digestB,
    elements: [
      {
        elementId: `qid-${index + 1}-title`,
        kind: "text",
        layer: "body",
        zOrder: 1,
        bounds: { x: 80, y: 70, width: 1120, height: 96 },
        rotation: 0,
        semanticRole: "title",
        editability: { text: true, geometry: true, asset: false, data: false, style: true },
        text: titleText,
      },
      {
        elementId: `qid-${index + 1}-body`,
        kind: "text",
        layer: "body",
        zOrder: 2,
        bounds: { x: 80, y: 190, width: 1120, height: 430 },
        rotation: 0,
        semanticRole: "body",
        editability: { text: true, geometry: true, asset: false, data: false, style: true },
        text: `${index + 1}번째 근거와 실행 항목`,
      },
    ],
  };
}

function generationDeckSnapshot(titleText?: string) {
  return {
    schemaVersion: "deck-generation-snapshot/v1",
    deckId: "deck:test",
    revision: 1,
    digest: digestA,
    sourceDigest: digestA,
    master: {
      masterId: "master:test",
      masterRevision: 2,
      masterDigest: digestB,
      layoutRegistryDigest: digestA,
      fontManifestDigest: digestB,
    },
    scene: {
      schemaVersion: "deck-scene-ir/v1",
      deckId: "deck:test",
      revision: 1,
      sceneDigest: digestB,
      compositionDigest: digestA,
      assetManifest: [],
      dataManifest: [],
      capabilities: [],
      slides: Array.from({ length: SLIDE_COUNT }, (_, index) =>
        index === 0 && titleText ? sceneSlide(index, titleText) : sceneSlide(index)),
    },
    slides: Array.from({ length: SLIDE_COUNT }, (_, index) => validatedSlide(index)),
    createdAt: new Date().toISOString(),
  };
}

const editableHybridReadiness = {
  editableHybridMvp: {
    profileId: "authored-editable-hybrid-ko-en-basic-v1",
    releaseScope: "ko-en-text-solid-shape-image-crop-basic-table",
    status: "not-evaluated",
    editableHybridMvpReady: false,
    nativeEditableReady: false,
    reasonCode: "authored_vnext_test_fixture",
  },
  nativeCapabilities: {
    schemaVersion: "authored-native-capability-projection/v1",
    capabilitySetVersion: "v1",
    admissionVersion: "v1",
    readinessVersion: "v1",
    sceneDigest: digestB,
    capabilities: [],
    duplicateOwnerCount: 0,
    missingOwnerCount: 0,
    fullNativeParityReady: false,
    promotionReady: false,
    deploymentAuthorized: false,
  },
  localRasterQualification: { status: "not-evaluated", localRasterReleaseReady: false },
  externalPromotion: { status: "not-evaluated", promotionReady: false },
  deployment: { deploymentAuthorized: false },
};

describe("AuthoredVnextFlowClient", () => {
  it("publishes a Master, generates ninety slides, edits, resolves regenerate conflict, and queues export", () => {
    // --- Master Setup -------------------------------------------------
    cy.intercept("GET", "**/api/v1/ppt/authored-vnext/master/status", {
      enabled: true,
      contractVersion: "deckmaster-contract/v1",
      slideSpecVersion: "slidespec/v2",
      compilerPort: "OoxmlMasterCompilerPort",
    });
    // MasterSetupGate is mounted with neither `initialMaster` nor `createNew`,
    // so it always probes for an existing head master first. 404 keeps this a
    // brand-new-master flow (register, not create-on-top-of-a-head).
    cy.intercept("GET", "**/api/v1/ppt/authored-vnext/master/masters/*/head", {
      statusCode: 404,
      body: {},
    });
    cy.intercept("POST", "**/api/v1/ppt/authored-vnext/master/masters/register", {
      statusCode: 201,
      body: { masterId: "master:test", revision: 1, digest: digestA, status: "draft", contract: DEFAULT_MASTER_CONTRACT, validation: { valid: true, errors: [] } },
    }).as("draft");
    cy.intercept("POST", "**/api/v1/ppt/authored-vnext/master/drafts/master%3Atest/validate", {
      valid: true,
      errors: [],
      furnitureBodyDuplicates: 0,
      writableOverlaps: 0,
      titleFitFailures: 0,
    }).as("validate");
    cy.intercept("POST", "**/api/v1/ppt/authored-vnext/master/drafts/master%3Atest/publish", {
      masterId: "master:test",
      revision: 2,
      digest: digestB,
      status: "published",
      contract: DEFAULT_MASTER_CONTRACT,
      validation: { valid: true, errors: [] },
    }).as("publish");

    // --- Generation -----------------------------------------------------
    cy.intercept("GET", "**/api/v1/ppt/authored-vnext/generation/master-pins/*/*", {
      masterId: "master:test",
      masterRevision: 2,
      masterDigest: digestB,
      layoutRegistryDigest: digestA,
      fontManifestDigest: digestB,
    }).as("masterPin");
    cy.intercept("POST", "**/api/v1/ppt/authored-vnext/generation/decks", {
      statusCode: 201,
      body: {
        operationId: "operation:test-generate",
        checkpoint: { checkpointDigest: digestA, master: { masterId: "master:test", masterRevision: 2, masterDigest: digestB, layoutRegistryDigest: digestA, fontManifestDigest: digestB } },
        deck: generationDeckSnapshot(),
        quality: { passed: true },
        exportArtifactDigest: digestA,
      },
    }).as("deck");
    cy.intercept("GET", "**/api/v1/ppt/authored-vnext/generation/decks/*/editable-hybrid-readiness-v4", editableHybridReadiness);
    // The Workbench re-reads the publication head on rebase and again right
    // before export; both just need the same canonical deck back.
    cy.intercept("GET", "**/api/v1/ppt/authored-vnext/generation/decks/*", {
      deck: generationDeckSnapshot(),
    }).as("getDeck");

    // A typed edit (the title patch below) is auto-dispatched to this
    // endpoint. The first attempt conflicts with a stale CAS pin — the exact
    // "resolves a regenerate conflict" scenario this spec exercises — and the
    // retry that the rebase below requeues succeeds.
    let sceneMutationAttempt = 0;
    cy.intercept("POST", "**/api/v1/ppt/authored-vnext/generation/decks/*/scene-mutations", (req) => {
      sceneMutationAttempt += 1;
      if (sceneMutationAttempt === 1) {
        req.reply({ statusCode: 409, body: { detail: "master changed" } });
        return;
      }
      req.reply({
        statusCode: 200,
        body: {
          schemaVersion: "scene-mutation-result/v1",
          operationId: "operation:test-mutation",
          mutation: "text.replace",
          slideId: "slide:1",
          elementId: "qid-1-title",
          slideDigest: digestB,
          sceneDigest: digestB,
          deck: generationDeckSnapshot("수정된 제목"),
          quality: { passed: true },
          publishedByCas: true,
        },
      });
    }).as("sceneMutation");

    // --- Export -----------------------------------------------------------
    cy.intercept("POST", "**/api/v1/ppt/authored-vnext/export/jobs", {
      schemaVersion: "authored-vnext-export-job/v1",
      jobId: "job:test",
      deckId: "deck:test",
      state: "queued",
      operationFence: 1,
    }).as("exportCreate");
    cy.intercept("POST", "**/api/v1/ppt/authored-vnext/export/jobs/*/run", {
      schemaVersion: "authored-vnext-export-job/v1",
      jobId: "job:test",
      deckId: "deck:test",
      state: "succeeded",
      operationFence: 1,
      artifact: { publicationRevision: 1, digest: digestA },
    }).as("exportRun");
    cy.intercept("GET", "**/api/v1/ppt/authored-vnext/export/decks/*/artifact", { statusCode: 200, body: "" });

    mountWithIntl(<AuthoredVnextFlowClient />);

    // 1. Materials — anchor to the stable test ids the stage already exposes
    // rather than to prose (materials.promptAria / materials.continue moved
    // under localization; the test ids did not).
    cy.get('[data-testid="authored-prompt"]').type("접근 가능한 Authored 흐름");
    cy.get('[data-testid="authored-materials-continue"]').click();

    // 2. Master Setup — the three-step "1. 새 Draft 생성 / 2. Stress 검증 /
    // 3. Master 게시" affordance from this spec's original authorship no
    // longer exists: MasterSetupPublishPanel collapsed to a single Save/
    // Publish pair, and "게시" (authored-master-publish) now runs
    // create-draft, validate, and publish as one click-triggered sequence.
    // There is also no "continue-after-publish" button any more — publishing
    // calls the client's `onPublished` directly, which is what actually
    // advances this flow to the Visual Direction step below. (Confirmed by
    // MasterSetupPublishFlow.cy.tsx's "continue-after-publish ... not.exist"
    // case for the same onPublish-style wiring.)
    cy.get('[data-testid="authored-master-publish"]').click();
    cy.wait("@draft");
    cy.wait("@validate");
    cy.wait("@publish");

    // 3. Visual Direction — "Visual Direction 확정 · 개요 만들기" was never the
    // rendered copy (direction.continue is "아웃라인으로 계속"); anchor to the
    // test id the button already carries.
    cy.get('[data-testid="authored-direction-continue"]').click();

    // 4. Outline & layout — no "90장 개요" text exists in the outline copy.
    // Assert the concrete thing that phrase meant: the outline lists all 90
    // planned slides.
    cy.get('[data-testid="authored-outline"] li').should("have.length", SLIDE_COUNT);
    cy.get('[data-testid="authored-generate"]').click();
    cy.wait("@masterPin");
    cy.wait("@deck");

    // 5. Workbench — edit the active slide's title.
    cy.get('[data-testid="authored-workbench"]').should("be.visible");
    // scene.title / textEditAria compose to "제목 텍스트 편집" today (the spec
    // previously anchored a "슬라이드 제목 텍스트 편집" string that was never
    // this component's copy); no stable test id exists on this per-element
    // editor, so this is the current message-catalog string.
    cy.get('textarea[aria-label="제목 텍스트 편집"]').first().clear().type("수정된 제목").blur();
    // Always-on outbox/no-autosave indicator (workbench.patchOnly); current
    // copy, not the spec's original "whole-deck autosave 없이 patch outbox에
    // 대기".
    cy.contains("본문 패치만 저장 · deck autosave 없음").should("exist");

    // 6. Conflict — the title edit above is auto-dispatched to the scene
    // mutation endpoint mocked above, which conflicts on the first attempt.
    // There has never been a "regenerate-conflict" test id or button in this
    // component; the CAS conflict banner appears on its own once the server
    // rejects the pending patch, without any extra click.
    cy.wait("@sceneMutation");
    // conflict.message is now "서버에서 덱이 변경되었습니다. ..." — the spec's
    // original "미전송 본문 patch와 body-only 재생성이 같은 revision을
    // 기준으로 합니다." was never this component's conflict copy.
    cy.contains("서버에서 덱이 변경되었습니다").should("exist");
    cy.get('[data-testid="authored-conflict-rebase"]').click();
    cy.wait("@getDeck");
    // The rebase requeues the patch, which the mocked endpoint now accepts.
    cy.wait("@sceneMutation");

    // 7. Export — "authored-export" is the stable id for this button.
    cy.get('[data-testid="authored-export"]').click();
    cy.wait("@exportCreate");
    cy.wait("@exportRun");
    // No UI currently confirms "export queued" to the user (exportDeck() only
    // ever calls `notice()` on failure, and AuthoredVnextFlowStage never
    // reads `flow.exportRequest`), so the spec's closing
    // "PPTX export request가 queued 상태로 등록되었습니다." assertion checked
    // copy that has never existed. The strongest available signal that the
    // request was queued and run is the pair of network calls above; also
    // assert the export-specific failure notice (errors.export) did not
    // replace them. (A generic "작업에 실패했습니다" toast from the earlier,
    // intentionally-mocked CAS conflict legitimately lingers — the operation
    // notice is never cleared — so this checks the export error copy
    // specifically rather than any occurrence of "실패했습니다".)
    cy.contains("내보내기에 실패했습니다").should("not.exist");
  });
});
