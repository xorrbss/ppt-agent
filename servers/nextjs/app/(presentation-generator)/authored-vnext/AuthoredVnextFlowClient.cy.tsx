import React from "react";
import { mount } from "cypress/react";

import { DEFAULT_MASTER_CONTRACT } from "@/lib/authored-vnext/master/fixtures";

import AuthoredVnextFlowClient from "./AuthoredVnextFlowClient";

const digestA = `sha256:${"a".repeat(64)}`;
const digestB = `sha256:${"b".repeat(64)}`;

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
  it("publishes a Master, generates ninety slides, edits, resolves regenerate conflict, and queues export", () => {
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
    cy.intercept("POST", "**/api/v1/ppt/authored-vnext/master/decks", {
      statusCode: 201,
      body: {
        schemaVersion: "deck-document/v1",
        deckId: "deck:test",
        revision: 1,
        digest: digestA,
        masterId: "master:test",
        masterRevision: 2,
        masterDigest: digestB,
        slides: Array.from({ length: 90 }, (_, index) => generatedSlide(index)),
      },
    }).as("deck");

    mount(<AuthoredVnextFlowClient />);
    cy.get('textarea[aria-label="발표 자료"]').type("접근 가능한 Authored 흐름");
    cy.contains("button", "자료 확인 · Master 설정으로").click();
    cy.contains("button", "1. 새 Draft 생성").click();
    cy.wait("@draft");
    cy.contains("button", "2. Stress 검증").click();
    cy.wait("@validate");
    cy.contains("button", "3. Master 게시").click();
    cy.wait("@publish");
    cy.get('[data-testid="continue-after-publish"]').click();
    cy.contains("button", "Visual Direction 확정 · 개요 만들기").click();
    cy.contains("90장 개요").should("be.visible");
    cy.contains("button", "90장 생성 시작").click();
    cy.wait("@deck");
    cy.get('[data-testid="authored-workbench"]').should("be.visible");
    cy.get('textarea[aria-label="슬라이드 제목 텍스트 편집"]').first().clear().type("수정된 제목").blur();
    cy.contains("whole-deck autosave 없이 patch outbox에 대기").should("exist");
    cy.get('[data-testid="regenerate-conflict"]').click();
    cy.contains("미전송 본문 patch와 body-only 재생성이 같은 revision을 기준으로 합니다.").should("exist");
    cy.contains("button", "최신 버전에 재적용").click();
    cy.contains("button", "내보내기 요청").click();
    cy.contains("PPTX export request가 queued 상태로 등록되었습니다.").should("be.visible");
  });
});
