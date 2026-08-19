import React from "react";
import { NextIntlClientProvider } from "next-intl";
import koMessages from "@/i18n/messages/ko.json";

import { QualityReviewRemainingIssues } from "./QualityReviewRemainingIssues";

const mountSection = (section: React.ReactElement) =>
  cy.mount(
    <NextIntlClientProvider locale="ko" messages={koMessages} timeZone="Asia/Seoul">
      {section}
    </NextIntlClientProvider>,
  );

describe("QualityReviewRemainingIssues", () => {
  it("offers no remake for issues the review could not attribute to a page", () => {
    mountSection(
      <QualityReviewRemainingIssues
        issues={[{ description: "dom.clipping" }]}
        totalIssueCount={1}
        remainingSlideIndexes={[]}
        onRemakePage={cy.stub().as("remakePage")}
      />,
    );

    cy.get('[data-testid="quality-review-remaining-issues"]').should(
      "contain.text",
      "슬라이드 미지정 · dom.clipping",
    );
    cy.get('input[type="checkbox"]').should("not.exist");
    cy.get('[data-testid="quality-review-batch-remake"]').should("not.exist");
  });

  it("collapses several issues on one page into a single remake selection", () => {
    const onRemakePage = cy.stub().as("remakePage");
    mountSection(
      <QualityReviewRemainingIssues
        issues={[
          { slideIndex: 6, description: "text_overflow" },
          { slideIndex: 6, description: "dom.clipping", reason: "constraint.no_safe_space" },
          { description: "deck summary" },
        ]}
        totalIssueCount={3}
        remainingSlideIndexes={[]}
        onRemakePage={onRemakePage}
      />,
    );

    cy.get('[data-testid^="quality-review-select-slide-"]').should("have.length", 1);
    cy.get('[data-testid="quality-review-select-slide-7"]').check();
    cy.get('[data-testid="quality-review-batch-remake"]').should(
      "contain.text",
      "선택한 1개 페이지 다시 만들기",
    );
    cy.get('[data-testid="quality-review-batch-remake"]').click();
    cy.get("@remakePage").should("have.been.calledOnceWith", [6]);
  });

  it("selects every attributable page at once and reports them ascending", () => {
    const onRemakePage = cy.stub().as("remakePage");
    mountSection(
      <QualityReviewRemainingIssues
        issues={[
          { slideIndex: 8, description: "text_overflow" },
          { slideIndex: 2, description: "contrast" },
          { slideIndex: 8, description: "dom.clipping" },
          { description: "deck summary" },
        ]}
        totalIssueCount={4}
        remainingSlideIndexes={[]}
        onRemakePage={onRemakePage}
      />,
    );

    cy.get('[data-testid="quality-review-select-all-slides"]').check();
    cy.get('[data-testid="quality-review-batch-remake"]')
      .should("contain.text", "선택한 2개 페이지 다시 만들기")
      .click();
    cy.get("@remakePage").should("have.been.calledOnceWith", [2, 8]);
  });

  it("keeps focusing a residual page independent of the remake selection", () => {
    const onFocusSlide = cy.stub().as("focusSlide");
    mountSection(
      <QualityReviewRemainingIssues
        issues={[{ slideIndex: 4, description: "text_overflow" }]}
        totalIssueCount={1}
        remainingSlideIndexes={[]}
        onFocusSlide={onFocusSlide}
        onRemakePage={cy.stub()}
      />,
    );

    cy.get('[data-testid="quality-review-focus-remaining-slide-5"]').click();
    cy.get("@focusSlide").should("have.been.calledOnceWith", 4);
    // Focusing is navigation, not selection: nothing becomes remakeable.
    cy.get('[data-testid="quality-review-batch-remake"]').should("be.disabled");
  });

  it("cannot start a remake while the workspace blocks writes", () => {
    mountSection(
      <QualityReviewRemainingIssues
        issues={[{ slideIndex: 1, description: "text_overflow" }]}
        totalIssueCount={1}
        remainingSlideIndexes={[]}
        disabled
        onRemakePage={cy.stub().as("remakePage")}
      />,
    );

    cy.get('[data-testid="quality-review-select-slide-2"]').should("be.disabled");
    cy.get('[data-testid="quality-review-batch-remake"]').should("be.disabled");
  });

  it("still lists residual pages that carry no issue text", () => {
    const onFocusSlide = cy.stub().as("focusSlide");
    mountSection(
      <QualityReviewRemainingIssues
        issues={[]}
        totalIssueCount={0}
        remainingSlideIndexes={[3]}
        onFocusSlide={onFocusSlide}
      />,
    );

    cy.contains("button", "4번으로 이동").click();
    cy.get("@focusSlide").should("have.been.calledOnceWith", 3);
  });
});
