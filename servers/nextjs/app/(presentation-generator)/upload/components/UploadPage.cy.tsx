import React from "react";
import { mount } from "cypress/react";
import { Provider } from "react-redux";
import { AppRouterContext } from "next/dist/shared/lib/app-router-context.shared-runtime";
import { NextIntlClientProvider } from "next-intl";

import koMessages from "@/i18n/messages/ko.json";
import { Toaster } from "@/components/ui/sonner";
import { makeStore } from "@/store/store";
import UploadPage from "./UploadPage";

import "@/app/globals.css";

const router = {
  push: () => undefined,
  back: () => undefined,
  forward: () => undefined,
  refresh: () => undefined,
  replace: () => undefined,
  prefetch: () => Promise.resolve(),
};

describe("<UploadPage /> authored-only creation", () => {
  beforeEach(() => {
    cy.clearLocalStorage();
    cy.intercept("GET", "**/api/v1/ppt/authored/styles", {
      statusCode: 200,
      body: [],
    });
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

  it("starts without a company standard and discards a stale browser preference", () => {
    cy.window().then((browserWindow) => {
      browserWindow.localStorage.setItem(
        "presenton:authored-master:last-used-id:v1",
        "master:sample-data-insight",
      );
    });

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

    cy.get('[data-testid="upload-master-selection"]')
      .should("contain", "선택하지 않으면 회사 표준 없이 생성합니다.")
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
