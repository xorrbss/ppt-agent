import React from "react";
import { AppRouterContext } from "next/dist/shared/lib/app-router-context.shared-runtime";
import { NextIntlClientProvider } from "next-intl";
import koMessages from "@/i18n/messages/ko.json";
import { createMockRouter } from "@/cypress/support/testHelpers";

import GenerationFailurePanel from "./GenerationFailurePanel";

const HASH = "a".repeat(64);

function mountPanel(failureClass: string | null, router = createMockRouter()) {
  cy.mount(
    <NextIntlClientProvider locale="ko" messages={koMessages} timeZone="Asia/Seoul">
      <AppRouterContext.Provider value={router as never}>
        <GenerationFailurePanel operationId="op-failed" failureClass={failureClass}
          fallbackText="기존 중단 문구" resumable />
      </AppRouterContext.Provider>
    </NextIntlClientProvider>,
  );
  return router;
}

describe("GenerationFailurePanel", () => {
  it("shows the class message and puts resume before starting over", () => {
    mountPanel("provider_unstable");
    cy.contains("AI 응답이 불안정해 생성이 멈췄습니다").should("be.visible");
    cy.get("[data-testid=generation-failure-panel] button").then(($buttons) => {
      expect($buttons.eq(0).text()).to.contain("남은 페이지 이어 만들기");
      expect($buttons.eq(1).text()).to.contain("새로 만들기");
    });
  });

  it("puts starting over first when the source cannot be processed", () => {
    const router = mountPanel("source_unprocessable");
    cy.contains("원본 문서를 처리하지 못했습니다").should("be.visible");
    cy.get("[data-testid=generation-failure-panel] button").first().should("have.text", "새로 만들기").click();
    cy.wrap(router.push).should("have.been.calledWith", "/upload");
  });

  it("keeps the previous text when the failure is unclassified", () => {
    mountPanel(null);
    cy.contains("기존 중단 문구").should("be.visible");
    cy.contains("button", "남은 페이지 이어 만들기").should("be.visible");
  });

  it("follows the resumed deck once the activity list shows it", () => {
    cy.intercept("GET", "**/workspace/operations/op-failed/recovery-plan", {
      recovery_revision: "svg-partial-recovery/v1", operation_id: "op-failed", presentation_id: "p-old",
      manifest_hash: HASH, nonce: HASH, historical_actual_cost_unbounded: false,
      known_indices: [0], new_indices: [1], unknown_indices: [], expected_page_count: 2,
      additional_budget_limit_microunits: 1000, prospective_author_request_count: 1,
      proven_remaining_budget_microunits: null, prospective_retry_request_limit: 2,
      operation_version: 3, presentation_revision: 4,
    });
    cy.intercept("POST", "**/workspace/operations/op-failed/actions", { items: [], has_more: false }).as("retry");
    cy.intercept("GET", "**/workspace/generation-activity", {
      server_time: "2026-10-22T09:00:00Z",
      items: [{
        operation_id: "op-resumed", presentation_id: "p-new", state: "queued", retry_of_operation_id: "op-failed",
        pages: { authored: 0, rendered: 0, persisted: 0, total: 2 },
        created_at: "2026-10-22T08:59:00Z", operation_version: 1, presentation_revision: 1,
      }],
    });
    const router = mountPanel("provider_unstable");
    cy.contains("button", "남은 페이지 이어 만들기").click();
    cy.contains("label", "허용 한도의 25% 안에서").find("input").check();
    cy.contains("button", "확인하고 이어 만들기").click();
    cy.wait("@retry");
    cy.wrap(router.push).should("have.been.calledWith", "/presentation?id=p-new");
  });
});
