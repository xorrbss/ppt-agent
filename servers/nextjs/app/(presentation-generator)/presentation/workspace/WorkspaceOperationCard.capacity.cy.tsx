import React from "react";
import { NextIntlClientProvider } from "next-intl";
import koMessages from "@/i18n/messages/ko.json";

import { WorkspaceOperationCard } from "./WorkspaceOperationCard";

const mountCard = (card: React.ReactElement) =>
  cy.mount(
    <NextIntlClientProvider
      locale="ko"
      messages={koMessages}
      timeZone="Asia/Seoul"
    >
      {card}
    </NextIntlClientProvider>,
  );

describe("WorkspaceOperationCard capacity waiting", () => {
  it("renders durable capacity waiting and queue position instead of an error", () => {
    mountCard(
      <WorkspaceOperationCard
        operation={{
          anchorType: "operation",
          id: "capacity-waiting-operation",
          operationId: "capacity-waiting-operation",
          position: 3,
          operationType: "design.apply",
          title: "AI 저작 디자인 적용",
          status: "queued",
          capacityWaiting: true,
          queuePosition: 2,
          events: [
            {
              id: "capacity-waiting-event",
              operationId: "capacity-waiting-operation",
              sequence: 2,
              type: "operation.progress",
              status: "queued",
              message:
                "생성 용량을 확보하는 동안 작업을 대기열에서 기다리고 있습니다.",
              capacityWaiting: true,
              queuePosition: 2,
            },
          ],
          allowedActions: [
            {
              id: "cancel",
              label: "취소",
              variant: "secondary",
              enabled: true,
            },
          ],
        }}
        onAction={cy.stub().as("capacityAction")}
      />,
    );

    cy.get('[data-operation-status="queued"]')
      .should("contain.text", "대기 중")
      .and("contain.text", "생성 용량 대기 중");
    cy.get('[data-testid="template-apply-progress"]')
      .should("be.visible")
      .and("contain.text", "생성 용량 대기 중")
      .and("contain.text", "대기 순번 2번째");
    cy.contains("button", "취소").should("be.enabled");
    cy.contains("Presentation generation capacity is temporarily unavailable")
      .should("not.exist");
    cy.get('[role="alert"]').should("not.exist");
  });
});
