import React from "react";
import { mount } from "cypress/react";
import { NextIntlClientProvider } from "next-intl";

import koMessages from "@/i18n/messages/ko.json";
import { ReviewRequiredBanner } from "./ReviewRequiredBanner";

function mountBanner(props: Partial<React.ComponentProps<typeof ReviewRequiredBanner>>) {
  mount(
    <NextIntlClientProvider locale="ko" messages={koMessages} timeZone="UTC">
      <ReviewRequiredBanner
        pages={[2, 6]}
        selectedSlide={0}
        onSelectPage={() => undefined}
        {...props}
      />
    </NextIntlClientProvider>,
  );
}

describe("ReviewRequiredBanner", () => {
  it("lists the flagged pages and selects the clicked one", () => {
    const onSelectPage = cy.stub().as("onSelectPage");
    mountBanner({ onSelectPage });
    cy.get('[data-testid="review-required-banner"]').should("contain.text", "검토가 필요한 쪽 2개");
    cy.get('[data-testid="review-required-page-chip"]').should("have.length", 2);
    cy.get('[data-testid="review-required-page-chip"]').last().click();
    cy.get("@onSelectPage").should("have.been.calledWith", 6);
  });

  it("starts the review flow from the button and hides without flagged pages", () => {
    const onReview = cy.stub().as("onReview");
    mountBanner({ onReview });
    cy.get('[data-testid="review-required-start"]').click();
    cy.get("@onReview").should("have.been.calledOnce");
    mountBanner({ pages: [] });
    cy.get('[data-testid="review-required-banner"]').should("not.exist");
  });
});
