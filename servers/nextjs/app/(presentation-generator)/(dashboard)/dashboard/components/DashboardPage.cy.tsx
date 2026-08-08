import React from "react";
import { AppRouterContext } from "next/dist/shared/lib/app-router-context.shared-runtime";

import { createMockRouter, ApiMockManager, testViewports } from "@/cypress/support/testHelpers";
import DashboardPage from "./DashboardPage";

describe("DashboardPage", () => {
  let apiMocks: ApiMockManager;

  beforeEach(() => {
    apiMocks = new ApiMockManager();
    cy.viewport(testViewports.desktop.width, testViewports.desktop.height);
  });

  afterEach(() => {
    apiMocks.cleanup();
  });

  it("shows a recoverable error (not the empty state) when loading fails", () => {
    // A real fetch failure must NOT be rendered as "no presentations yet" —
    // that reads as account-wide data loss. The grid's retry UI should show.
    apiMocks.setupErrorIntercept(
      "**/api/v1/ppt/presentation/all",
      500,
      "Internal Server Error",
      "presentationsFail"
    );

    cy.mount(
      <AppRouterContext.Provider value={createMockRouter() as never}>
        <DashboardPage />
      </AppRouterContext.Provider>
    );

    // Wait for async chart rendering to complete if present
    cy.waitForChartsToRender();

    cy.contains("발표자료를 불러오지 못했습니다").should("be.visible");
    cy.contains("button", "재시도").should("be.visible");
    cy.contains("아직 발표자료가 없습니다").should("not.exist");
  });

  it("shows the empty state when there are genuinely no presentations", () => {
    apiMocks.setupGetIntercept(
      "**/api/v1/ppt/presentation/all",
      [],
      "presentationsEmpty"
    );

    cy.mount(
      <AppRouterContext.Provider value={createMockRouter() as never}>
        <DashboardPage />
      </AppRouterContext.Provider>
    );

    // Wait for async chart rendering to complete if present
    cy.waitForChartsToRender();

    cy.contains("아직 발표자료가 없습니다").should("be.visible");
    cy.contains("button", "재시도").should("not.exist");
  });
});
