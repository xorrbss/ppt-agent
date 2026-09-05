import React, { useState } from "react";
import { mount } from "cypress/react";
import { NextIntlClientProvider } from "next-intl";
import { Provider } from "react-redux";

import enMessages from "@/i18n/messages/en.json";
import koMessages from "@/i18n/messages/ko.json";
import { makeStore } from "@/store/store";
import {
  setFlaggedSlides,
  setPresentationData,
} from "@/store/slices/presentationGeneration";
import { createBlankPresentationSlide } from "../../_shared/blank-slide";
import { deriveFlaggedSlideMap } from "../utils/flaggedSlides";
import { FlaggedSlideBadge } from "./FlaggedSlideBadge";
import SidePanel from "./SidePanel";

const GATE_ENTRY = {
  index: 1,
  reason: "composition_safety:overflow,text_collision",
  remakeAttempted: true,
  remakeOutcome: "remake_budget_exhausted",
};
const AUTHOR_ENTRY = {
  index: 0,
  reason: "author:attempts_exhausted",
  remakeAttempted: true,
  remakeOutcome: "placeholder",
};

function deck(flaggedSlides: unknown[]) {
  return {
    id: "p1",
    language: "ko",
    layout: { name: "blank", ordered: true, slides: [] },
    n_slides: 3,
    title: "Flagged deck",
    slides: Array.from({ length: 3 }, (_, index) =>
      createBlankPresentationSlide({
        id: `s${index + 1}`,
        index,
        presentationId: "p1",
        templateId: "blank",
        isTemplateV2: true,
      }),
    ),
    // The receipt rides on the theme exactly as the backend persists it.
    theme: { mode: "authored", authoredQualityGate: { flaggedSlides } } as never,
  };
}

function mountSidePanel(flaggedSlides: unknown[]) {
  const store = makeStore();
  const presentation = deck(flaggedSlides);
  store.dispatch(setPresentationData(presentation));
  store.dispatch(setFlaggedSlides(deriveFlaggedSlideMap(presentation)));

  function Harness() {
    const [selectedSlide, setSelectedSlide] = useState(0);
    return (
      <SidePanel
        selectedSlide={selectedSlide}
        onSlideClick={setSelectedSlide}
        presentationId="p1"
        loading={false}
        mutationsEnabled
      />
    );
  }

  mount(
    <NextIntlClientProvider locale="en" messages={enMessages} timeZone="UTC">
      <Provider store={store}>
        <Harness />
      </Provider>
    </NextIntlClientProvider>,
  );
}

describe("FlaggedSlideBadge", () => {
  it("marks only the flagged thumbnails and carries the stage", () => {
    mountSidePanel([GATE_ENTRY, AUTHOR_ENTRY]);

    cy.get('[data-testid="flagged-slide-badge"]').should("have.length", 2);
    cy.get('[data-slide-thumbnail-index="0"] [data-testid="flagged-slide-badge"]')
      .should("have.attr", "data-flag-stage", "author")
      .and("contain.text", "Needs review");
    cy.get('[data-slide-thumbnail-index="1"] [data-testid="flagged-slide-badge"]')
      .should("have.attr", "data-flag-stage", "gate")
      .invoke("attr", "title")
      .should("contain", "overflow, text_collision");
    cy.get('[data-slide-thumbnail-index="2"] [data-testid="flagged-slide-badge"]').should(
      "not.exist",
    );
  });

  it("renders nothing when the receipt has no flags", () => {
    mountSidePanel([]);
    cy.get('[data-slide-thumbnail-index]').should("have.length", 3);
    cy.get('[data-testid="flagged-slide-badge"]').should("not.exist");
  });

  it("shows the Korean reason on the slide notice", () => {
    const badge = deriveFlaggedSlideMap(deck([AUTHOR_ENTRY]))["s1"];
    mount(
      <NextIntlClientProvider locale="ko" messages={koMessages} timeZone="UTC">
        <div className="relative h-40 w-80">
          <FlaggedSlideBadge badge={badge} slideNumber={1} variant="slide" />
        </div>
      </NextIntlClientProvider>,
    );
    cy.get('[data-testid="flagged-slide-notice"]')
      .should("have.attr", "data-flag-stage", "author")
      .and("contain.text", "검수 필요");
    cy.get('[data-testid="flagged-slide-notice"] [role="status"]')
      .invoke("attr", "aria-label")
      .should("contain", "자리표시자");
  });
});
