import React from "react";
import { mount } from "cypress/react";
import { NextIntlClientProvider } from "next-intl";
import { Provider } from "react-redux";
import { PathnameContext } from "next/dist/shared/lib/hooks-client-context.shared-runtime";

import enMessages from "@/i18n/messages/en.json";
import { makeStore } from "@/store/store";
import { setPresentationData, setStreaming } from "@/store/slices/presentationGeneration";
import { createBlankPresentationSlide } from "../../_shared/blank-slide";
import SlideContent from "./SlideContent";

function mountSlide(mutationsEnabled: boolean) {
  const slide = createBlankPresentationSlide({
    id: "slide-1",
    presentationId: "deck-1",
    templateId: "blank",
    isTemplateV2: true,
  });
  const store = makeStore();
  store.dispatch(
    setPresentationData({
      id: "deck-1",
      language: "ko",
      layout: { name: "blank", ordered: true, slides: [] },
      n_slides: 1,
      title: "Test deck",
      slides: [slide],
      theme: null,
    }),
  );
  store.dispatch(setStreaming(false));

  mount(
    <NextIntlClientProvider locale="en" messages={enMessages} timeZone="UTC">
      <PathnameContext.Provider value="/presentation/deck-1">
        <Provider store={store}>
          <SlideContent
            slide={slide}
            index={0}
            presentationId="deck-1"
            mutationsEnabled={mutationsEnabled}
          />
        </Provider>
      </PathnameContext.Provider>
    </NextIntlClientProvider>,
  );
}

// SlideContent used to carry an inline "add a slide below" control
// (`slide-add-control`), which b11a92e62 ("refactor: remove legacy presentation
// sources") deleted together with the NewSlide picker it opened. Adding a slide
// is still a product feature, but it is now a deck-level action owned by the
// side panel (`side-panel-add-slide`, covered by SidePanel.cy.tsx). Per slide,
// the mutation surface is exactly the hover toolbar asserted below, which is
// what this gate has to keep switching on and off.
const SLIDE_MUTATION_CONTROLS = [
  "프롬프트로 슬라이드 수정",
  "발표자 노트 추가",
  "슬라이드 삭제",
];

describe("SlideContent mutation gate", () => {
  it("renders the slide read-only and removes direct mutation entry points in chat-first mode", () => {
    mountSlide(false);

    cy.get("#slide-0").should("have.attr", "data-mutations-enabled", "false");
    cy.get("#slide-0").should("have.class", "md:mb-6");
    cy.get('[data-testid="slide-page-boundary"]')
      .should("have.class", "ring-slate-300")
      .and("have.class", "shadow-[0_3px_12px_rgba(15,23,42,0.16)]");
    cy.get('[data-testid="slide-mutation-controls"]').should("not.exist");
    SLIDE_MUTATION_CONTROLS.forEach((label) => {
      cy.get(`#slide-0 button[aria-label="${label}"]`).should("not.exist");
    });
    cy.get('[contenteditable="true"]').should("not.exist");
  });

  it("restores the legacy edit controls when chat-first mode is rolled back", () => {
    mountSlide(true);

    cy.get("#slide-0").should("have.attr", "data-mutations-enabled", "true");
    cy.get('[data-testid="slide-mutation-controls"]').should("exist");
    SLIDE_MUTATION_CONTROLS.forEach((label) => {
      cy.get('[data-testid="slide-mutation-controls"]')
        .find(`button[aria-label="${label}"]`)
        .should("exist");
    });
  });
});
