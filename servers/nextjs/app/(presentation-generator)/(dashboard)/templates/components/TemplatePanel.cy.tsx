import React from "react";
import { AppRouterContext } from "next/dist/shared/lib/app-router-context.shared-runtime";
import { NextIntlClientProvider } from "next-intl";

import { createMockRouter } from "@/cypress/support/testHelpers";
import koMessages from "@/i18n/messages/ko.json";
import TemplatePanel from "./TemplatePanel";

type AxeRuntime = typeof import("axe-core");

function mountPanel() {
  const router = createMockRouter();
  router.push = cy.stub().as("router.push");
  cy.mount(
    <NextIntlClientProvider
      locale="ko"
      messages={koMessages}
      timeZone="Asia/Seoul"
    >
      <AppRouterContext.Provider value={router as never}>
        <TemplatePanel />
      </AppRouterContext.Provider>
    </NextIntlClientProvider>,
  );
}

function assertNoSevereAxeViolations() {
  cy.readFile("node_modules/axe-core/axe.min.js", "utf8")
    .then((source: string) =>
      cy.window().then((window) => {
        window.Function(source)();
        const pageAxe = (window as typeof window & { axe?: AxeRuntime }).axe;
        if (!pageAxe) throw new Error("axe-core did not attach to the component window");
        return pageAxe.run(window.document.body);
      }),
    )
    .then((results) => {
      const severe = results.violations.filter(
        ({ impact }) => impact === "serious" || impact === "critical",
      );
      const summary = severe
        .map((violation) => `${violation.id}: ${violation.nodes.map((node) => node.target.join(" > ")).join(", ")}`)
        .join("\n");
      expect(severe, summary).to.have.length(0);
    });
}

describe("TemplatePanel localized empty states", () => {
  it("shows a localized create action when the catalog is empty", () => {
    cy.intercept("GET", "**/api/v1/ppt/structured-templates*", []).as(
      "templates",
    );
    mountPanel();
    cy.wait("@templates");

    cy.contains("h3", "템플릿").should("be.visible");
    cy.contains("아직 사용할 수 있는 템플릿이 없습니다").should("be.visible");
    cy.contains("button", "템플릿 만들기").focus().should("have.focus").click();
    cy.get("@router.push").should("have.been.calledWith", "/custom-template");
    assertNoSevereAxeViolations();
  });

  it("offers a localized retry action after a catalog failure", () => {
    cy.intercept("GET", "**/api/v1/ppt/structured-templates*", {
      statusCode: 500,
      body: { detail: "catalog unavailable" },
    }).as("templatesFailure");
    mountPanel();
    cy.wait("@templatesFailure");

    cy.contains("Template V2 카탈로그를 불러오지 못했습니다.").should(
      "be.visible",
    );
    cy.contains("button", "다시 시도").should("be.visible");
  });
});
