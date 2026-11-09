import React from "react";
import { NextIntlClientProvider } from "next-intl";
import { Provider } from "react-redux";
import { AppRouterContext } from "next/dist/shared/lib/app-router-context.shared-runtime";

import { Toaster } from "@/components/ui/sonner";
import koMessages from "@/i18n/messages/ko.json";
import type { AiMasterAnalysis } from "@/lib/authored-vnext/master/contracts";
import { DEFAULT_MASTER_CONTRACT } from "@/lib/authored-vnext/master/fixtures";
import { setPptGenUploadState } from "@/store/slices/presentationGenUpload";
import { store } from "@/store/store";

import UploadPage from "./UploadPage";

import "@/app/globals.css";

const SOURCE_DIGEST = `sha256:${"a".repeat(64)}` as const;

function completedAnalysis(): AiMasterAnalysis {
  return {
    analysisId: "b".repeat(32),
    status: "completed",
    purpose: "cleanup",
    source: {
      filename: "customer-proposal.pptx",
      digest: SOURCE_DIGEST,
      slideCount: 4,
      widthPx: 1280,
      heightPx: 720,
      assets: [],
      assetRelationships: [],
    },
    summary: {
      designFamilies: 1,
      layoutCandidates: 1,
      fontCount: 1,
      colorCount: 2,
      repeatedElementCount: 1,
      aiZoneCandidateCount: 1,
      lowConfidenceCount: 0,
    },
    fonts: [{ family: "Malgun Gothic", count: 12, source: "slide" }],
    colors: [
      { hex: "#172033", count: 8, source: "slide" },
      { hex: "#356AE6", count: 4, source: "theme" },
    ],
    repeatedElements: [
      {
        id: "candidate:rfp-number",
        type: "text",
        role: "rfpNumber",
        text: "RFP No. 2026-0042",
        confidence: 0.96,
        differences: [],
        aiZoneCandidate: true,
        evidence: [1, 2, 3, 4].map((slideNumber) => ({
          slideNumber,
          bounds: { x: 52, y: 660, width: 280, height: 28 },
          rotation: 0,
          zIndex: 3,
          layoutPart: "ppt/slideLayouts/slideLayout1.xml",
          masterPart: "ppt/slideMasters/slideMaster1.xml",
          themePart: "ppt/theme/theme1.xml",
        })),
      },
    ],
    layoutCandidates: [
      {
        id: "layout-candidate:standard",
        name: "본문 레이아웃",
        kind: "standard",
        representativeSlide: 2,
        slideNumbers: [2, 3],
        confidence: 0.91,
        differences: [
          {
            code: "positionVariance",
            message: "본문 위치 차이 4px",
            severity: "info",
          },
        ],
        slots: [
          {
            id: "slot-evidence:title",
            role: "title",
            bounds: { x: 80, y: 68, width: 860, height: 90 },
            fontFamily: "Malgun Gothic",
            fontSize: 26,
            color: "#172033",
          },
        ],
        excluded: false,
      },
    ],
    decisions: [{ candidateId: "candidate:rfp-number", action: "pending" }],
    layoutActions: [],
    warnings: [],
    lowConfidence: [],
    tolerances: { geometryRatio: 0.0075, layoutVector: 0.18 },
    contractDraft: structuredClone(DEFAULT_MASTER_CONTRACT),
  };
}

const createRouter = () => ({
  push: cy.stub(),
  back: cy.stub(),
  forward: cy.stub(),
  refresh: cy.stub(),
  replace: cy.stub(),
  prefetch: cy.stub().resolves(),
  route: "/",
  pathname: "/",
  query: {},
  asPath: "/",
});

describe("Upload PPT AI master flow", () => {
  it("uploads a PPTX, chooses cleanup, analyzes it, and opens the evidence-backed review", () => {
    cy.clearLocalStorage();
    cy.window().then((browserWindow) => {
      browserWindow.localStorage.setItem(
        "presenton.authored-vnext.ai-master-analysis.v1",
        "c".repeat(32),
      );
    });
    let resumedAnalysisRequests = 0;
    store.dispatch(setPptGenUploadState({
      selectedTheme: null,
      authoredStyle: "default",
      companyStandard: null,
    }));
    cy.intercept("GET", "**/api/v1/ppt/structured-templates*", {
      statusCode: 200,
      body: [{
        id: "focused-template",
        name: "집중 테스트 템플릿",
        description: "편집 가능한 템플릿",
        revision: 1,
        is_default: true,
        source: "Presenton",
        assets: null,
        layouts: { layouts: [{ id: "cover" }, { id: "content" }] },
      }],
    });
    cy.intercept("GET", "**/api/v1/ppt/authored/styles", {
      statusCode: 200,
      body: [],
    });
    cy.intercept("GET", "**/api/v1/ppt/themes/all", {
      statusCode: 200,
      body: [],
    });
    cy.intercept("GET", "**/api/v1/ppt/authored-vnext/master/status", {
      statusCode: 200,
      body: {
        enabled: true,
        contractVersion: "deckmaster-contract/v1",
        slideSpecVersion: "slidespec/v2",
        compilerPort: "OoxmlMasterCompilerPort",
      },
    });
    cy.intercept(
      "POST",
      "**/api/v1/ppt/authored-vnext/master/ai-import/analyses",
      { statusCode: 201, body: completedAnalysis() },
    ).as("analyzePptx");
    cy.intercept(
      "GET",
      "**/api/v1/ppt/authored-vnext/master/ai-import/analyses/*",
      (request) => {
        resumedAnalysisRequests += 1;
        request.reply({ statusCode: 200, body: completedAnalysis() });
      },
    );

    cy.mount(
      <NextIntlClientProvider
        locale="ko"
        messages={koMessages}
        timeZone="Asia/Seoul"
      >
        <AppRouterContext.Provider value={createRouter() as any}>
          <Provider store={store}>
            <UploadPage masterFeatureEnabled />
            <Toaster />
          </Provider>
        </AppRouterContext.Provider>
      </NextIntlClientProvider>,
    );

    cy.get('[data-testid="ppt-ai-master-create"]')
      .should("be.enabled")
      .click();
    cy.get('[data-testid="ppt-analysis-objective"]').should("be.visible");
    cy.then(() => expect(resumedAnalysisRequests, "stale analysis resume requests").to.eq(0));
    cy.get('[data-testid="ppt-ai-analysis-start"]').should("be.disabled");
    cy.contains("AI 마스터를 만들려면 PPT 또는 PPTX를 업로드하세요.")
      .should("be.visible");
    cy.contains("PPT/PPTX 파일 선택").should("be.visible");
    cy.get('[data-testid="ppt-ai-source-input"]').selectFile(
      {
        contents: Cypress.Buffer.from("PK focused pptx fixture"),
        fileName: "customer-proposal.pptx",
        mimeType:
          "application/vnd.openxmlformats-officedocument.presentationml.presentation",
      },
      { force: true },
    );
    cy.contains("분석할 파일: customer-proposal.pptx").should("be.visible");
    cy.get('[data-testid="ppt-ai-analysis-start"]').should("be.enabled");

    cy.get('[data-testid="ppt-analysis-objective"]')
      .find('input[value="cleanup"]')
      .check();
    cy.get('[data-testid="ppt-ai-analysis-start"]').click();

    cy.wait("@analyzePptx").then(({ request }) => {
      expect(request.headers["content-type"]).to.contain("multipart/form-data");
      expect(String(request.body)).to.contain("customer-proposal.pptx");
      expect(String(request.body)).to.contain("cleanup");
    });
    cy.get('[data-testid="ppt-ai-analysis-review"]').should("be.visible");
    cy.get('[data-testid="ppt-analysis-layouts"]')
      .should("contain", "본문 레이아웃")
      .and("contain", "91");
    cy.get('[data-testid="ppt-analysis-inspector"]')
      .should("contain", "RFP No. 2026-0042");
  });
});
