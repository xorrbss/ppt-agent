import React from "react";
import { mount } from "cypress/react";
import { NextIntlClientProvider } from "next-intl";
import koMessages from "@/i18n/messages/ko.json";

import { DEFAULT_MASTER_CONTRACT } from "@/lib/authored-vnext/master/fixtures";

import AuthoredVnextFlowClient from "./AuthoredVnextFlowClient";

const digestA = `sha256:${"a".repeat(64)}`;
const digestB = `sha256:${"b".repeat(64)}`;
const masterPin = { masterId: "master:test", masterRevision: 2, masterDigest: digestB, layoutRegistryDigest: digestA, fontManifestDigest: digestB };

function generatedSlide(index: number) {
  const layoutId = index === 0
    ? "layout:cover"
    : index === 1 || index % 15 === 0
      ? "layout:section"
      : index % 10 === 5
        ? "layout:long-title"
        : "layout:standard";
  const text = index === 0 ? "접근 가능한 Authored 흐름" : `슬라이드 ${index + 1}`;
  return {
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
  };
}

describe("AuthoredVnextFlowClient", () => {
  it("publishes a Master, generates ninety slides, and exposes editable workbench actions", () => {
    cy.intercept("GET", "**/api/v1/ppt/authored-vnext/master/status", {
      enabled: true,
      contractVersion: "deckmaster-contract/v1",
      slideSpecVersion: "slidespec/v2",
      compilerPort: "OoxmlMasterCompilerPort",
    });
    cy.intercept("POST", "**/api/v1/ppt/authored-vnext/master/drafts", {
      statusCode: 201,
      body: { masterId: "master:test", revision: 1, digest: digestA, status: "draft", contract: DEFAULT_MASTER_CONTRACT, validation: { valid: true, errors: [] } },
    }).as("draft");
    cy.intercept("POST", "**/api/v1/ppt/authored-vnext/master/masters/register", {
      statusCode: 201,
      body: { masterId: "master:test", revision: 1, digest: digestA, status: "draft", contract: DEFAULT_MASTER_CONTRACT, validation: { valid: true, errors: [] } },
    }).as("register");
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
    cy.intercept("GET", "**/api/v1/ppt/authored-vnext/generation/master-pins/*/*", masterPin).as("masterPin");
    cy.intercept("POST", "**/api/v1/ppt/authored-vnext/generation/decks", {
      statusCode: 201,
      body: {
        schemaVersion: "generation-result/v1",
        operationId: "operation:test-generate",
        checkpoint: { checkpointDigest: digestA, master: masterPin },
        deck: {
          schemaVersion: "deck-generation-snapshot/v1",
          deckId: "deck:test",
          revision: 1,
          digest: digestA,
          sourceDigest: digestA,
          master: masterPin,
          slides: Array.from({ length: 90 }, (_, index) => ({
            schemaVersion: "validated-slidespec/v2",
            slide: generatedSlide(index),
            checkpoint: { masterDigest: digestB, sourceDigest: digestA, bodySlotId: "slot:body" },
          })),
          createdAt: "2026-09-23T00:00:00Z",
        },
        quality: { passed: true },
        exportArtifactDigest: digestA,
      },
    }).as("deck");

    mount(<NextIntlClientProvider locale="ko" messages={koMessages} timeZone="Asia/Seoul"><AuthoredVnextFlowClient /></NextIntlClientProvider>);
    cy.get('[data-testid="authored-prompt"]').type("접근 가능한 Authored 흐름");
    cy.get('[data-testid="authored-materials-continue"]').click();
    cy.get('[data-testid="authored-master-publish"]').click();
    cy.wait("@register");
    cy.wait("@validate");
    cy.wait("@publish");
    cy.get('[data-testid="authored-direction-continue"]').click();
    cy.get('[data-testid="authored-outline"]').children().should("have.length", 90);
    cy.get('[data-testid="authored-generate"]').click();
    cy.wait("@deck");
    cy.get('[data-testid="authored-workbench"]').should("be.visible");
    cy.get('textarea[aria-label="제목 텍스트 편집"]').first().should("be.visible");
    cy.contains("본문 패치만 저장 · deck autosave 없음").should("be.visible");
    cy.get('[data-testid="authored-regeneration-body"]').should("be.visible");
    cy.get('[data-testid="authored-export"]').should("be.enabled");
  });
});
