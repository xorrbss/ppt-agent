import React from "react";
import { mount } from "cypress/react";
import { Provider } from "react-redux";
import { AppRouterContext } from "next/dist/shared/lib/app-router-context.shared-runtime";
import { NextIntlClientProvider } from "next-intl";

import koMessages from "@/i18n/messages/ko.json";
import { Toaster } from "@/components/ui/sonner";
import { makeStore } from "@/store/store";
import { setPptGenUploadState } from "@/store/slices/presentationGenUpload";
import UploadPage from "./UploadPage";
import { useSupportingFiles } from "./upload-page/useSupportingFiles";

import "@/app/globals.css";

const router = {
  push: () => undefined,
  back: () => undefined,
  forward: () => undefined,
  refresh: () => undefined,
  replace: () => undefined,
  prefetch: () => Promise.resolve(),
};

const ImmediateFileSelectionHarness = () => {
  const { files, filesRef, replaceFiles } = useSupportingFiles();
  const [submittedCount, setSubmittedCount] = React.useState(0);

  return (
    <>
      <button
        type="button"
        data-testid="select-and-submit"
        onClick={() => {
          replaceFiles([
            new File(["pptx"], "source.pptx", {
              type: "application/vnd.openxmlformats-officedocument.presentationml.presentation",
            }),
          ]);
          setSubmittedCount(filesRef.current.length);
        }}
      >
        즉시 제출
      </button>
      <span data-testid="rendered-file-count">{files.length}</span>
      <span data-testid="submitted-file-count">{submittedCount}</span>
    </>
  );
};

describe("<UploadPage /> authored-only creation", () => {
  beforeEach(() => {
    cy.clearLocalStorage();
    cy.intercept("GET", "**/api/v1/ppt/authored/styles", {
      statusCode: 200,
      body: [],
    });
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
  });

  it("shows only authored style selection for new generation", () => {
    mount(
      <NextIntlClientProvider locale="ko" messages={koMessages} timeZone="Asia/Seoul">
        <AppRouterContext.Provider value={router as any}>
          <Provider store={makeStore()}>
            <UploadPage masterFeatureEnabled={false} />
            <Toaster />
          </Provider>
        </AppRouterContext.Provider>
      </NextIntlClientProvider>,
    );

    cy.get('[data-testid="authored-style-section"]').should("be.visible");
    cy.contains("Template V2").should("not.exist");
    cy.contains("레이아웃 템플릿").should("not.exist");
    cy.get('[data-testid="generate-presentation-button"]').should("be.visible");
  });

  it("keeps a newly accepted attachment available to an immediate submit", () => {
    mount(<ImmediateFileSelectionHarness />);

    cy.get('[data-testid="select-and-submit"]').click();
    cy.get('[data-testid="submitted-file-count"]').should("have.text", "1");
    cy.get('[data-testid="rendered-file-count"]').should("have.text", "1");
  });

  it("enables the supported source-preserving landscape SVG flow", () => {
    mount(
      <NextIntlClientProvider locale="ko" messages={koMessages} timeZone="Asia/Seoul">
        <AppRouterContext.Provider value={router as any}>
          <Provider store={makeStore()}>
            <UploadPage masterFeatureEnabled />
            <Toaster />
          </Provider>
        </AppRouterContext.Provider>
      </NextIntlClientProvider>,
    );

    cy.get('[data-testid="file-upload-input"]').selectFile({
      contents: Cypress.Buffer.from("pptx"),
      fileName: "source.pptx",
      mimeType: "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    });
    cy.get('[data-testid="generation-strategy-svg"]').should("be.enabled").click();

    cy.get('[data-testid="source-content-selection-summary"]')
      .should("contain", "SVG 원본 크기 재배치")
      .and("contain", "원문·표·이미지 유지")
      .and("contain", "요약 및 페이지 추가 안 함");
    cy.get('[data-testid="source-svg-landscape-canvas"]').should("contain", "원본 크기 · 분석 후 유지");
    cy.get('[data-testid="upload-master-selection"]').should("not.exist");
  });

  it("starts without a company standard and discards a stale browser preference", () => {
    cy.window().then((browserWindow) => {
      browserWindow.localStorage.setItem(
        "presenton:authored-master:last-used-id:v1",
        "master:sample-data-insight",
      );
    });

    // The company-standard master picker is an authored-only feature; svg is
    // now the landing default, so this test selects authored explicitly to
    // exercise it.
    const store = makeStore();
    store.dispatch(setPptGenUploadState({ generationStrategy: "authored" }));

    mount(
      <NextIntlClientProvider locale="ko" messages={koMessages} timeZone="Asia/Seoul">
        <AppRouterContext.Provider value={router as any}>
          <Provider store={store}>
            <UploadPage masterFeatureEnabled />
            <Toaster />
          </Provider>
        </AppRouterContext.Provider>
      </NextIntlClientProvider>,
    );

    cy.get('[data-testid="upload-master-selection"]')
      .should("contain", koMessages.upload.simplify.companyTemplateDescription)
      .and("not.contain", "데이터 인사이트");
    cy.window().then((browserWindow) => {
      expect(
        browserWindow.localStorage.getItem(
          "presenton:authored-master:last-used-id:v1",
        ),
      ).to.equal(null);
    });
  });
});
