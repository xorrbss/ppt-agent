import React, { useState } from "react";
import { mount } from "cypress/react";
import { Provider } from "react-redux";

import { makeStore } from "@/store/store";
import { setPresentationData } from "@/store/slices/presentationGeneration";
import { createBlankPresentationSlide } from "../../_shared/blank-slide";
import SidePanel from "./SidePanel";

function mountSidePanel(mutationsEnabled: boolean, slideCount = 1) {
  const store = makeStore();
  store.dispatch(
    setPresentationData({
      id: "p1",
      language: "ko",
      layout: { name: "blank", ordered: true, slides: [] },
      n_slides: slideCount,
      title: "Test deck",
      slides: Array.from({ length: slideCount }, (_, index) =>
        createBlankPresentationSlide({
          id: `s${index + 1}`,
          index,
          presentationId: "p1",
          templateId: "blank",
          isTemplateV2: true,
        }),
      ),
      theme: null,
    }),
  );

  function Harness() {
    const [selectedSlide, setSelectedSlide] = useState(0);
    return (
      <>
        <output data-testid="selected-slide">{selectedSlide}</output>
        <button type="button" onClick={() => setSelectedSlide(slideCount - 1)}>
          마지막 슬라이드 선택
        </button>
        <SidePanel
          selectedSlide={selectedSlide}
          onSlideClick={setSelectedSlide}
          presentationId="p1"
          loading={false}
          mutationsEnabled={mutationsEnabled}
        />
      </>
    );
  }

  mount(
    <Provider store={store}>
      <Harness />
    </Provider>,
  );

  return store;
}

describe("SidePanel mutation gate", () => {
  it("hides local slide mutation entry points in chat-first mode", () => {
    mountSidePanel(false);
    cy.get('[data-testid="side-panel-add-slide"]').should("not.exist");
  });

  it("preserves the legacy add-slide entry point in rollback mode", () => {
    mountSidePanel(true);
    cy.get('[data-testid="side-panel-add-slide"]').should("be.visible");
  });

  it("scrolls the thumbnail strip when the selected main slide moves out of view", () => {
    cy.stub(HTMLElement.prototype, "getBoundingClientRect").callsFake(function () {
      const element = this as HTMLElement;
      const thumbnailIndex = element.dataset.slideThumbnailIndex;
      if (element.dataset.testid === "slide-thumbnail-scroll") {
        return { top: 0, bottom: 100, height: 100 } as DOMRect;
      }
      if (thumbnailIndex === "5") {
        return { top: 200, bottom: 250, height: 50 } as DOMRect;
      }
      return { top: 0, bottom: 50, height: 50 } as DOMRect;
    });
    cy.stub(HTMLElement.prototype, "scrollTo").as("thumbnailScrollTo");

    mountSidePanel(false, 6);
    cy.get('[data-testid="slide-thumbnail-scroll"]').then(($element) => {
      Object.defineProperty($element[0], "clientHeight", {
        configurable: true,
        value: 100,
      });
    });
    cy.get("@thumbnailScrollTo").invoke("resetHistory");
    cy.contains("마지막 슬라이드 선택").click();

    cy.get("@thumbnailScrollTo").should(
      "have.been.calledWithMatch",
      { top: 158, behavior: "smooth" },
    );
  });

  it("selects read-only thumbnails with Enter, Space, and click", () => {
    mountSidePanel(false, 3);

    cy.get('[data-testid="slide-thumbnail-select-1"]').focus();
    cy.press(Cypress.Keyboard.Keys.ENTER);
    cy.get('[data-testid="selected-slide"]').should("have.text", "1");
    cy.get('[data-testid="slide-thumbnail-select-1"]').should(
      "have.attr",
      "aria-current",
      "page",
    );

    cy.get('[data-testid="slide-thumbnail-select-2"]').focus();
    cy.press(Cypress.Keyboard.Keys.SPACE);
    cy.get('[data-testid="selected-slide"]').should("have.text", "2");

    cy.get('[data-testid="slide-thumbnail-select-0"]').click();
    cy.get('[data-testid="selected-slide"]').should("have.text", "0");
  });

  it("keeps keyboard reordering on a dedicated drag handle", () => {
    const store = mountSidePanel(true, 3);

    cy.get('[data-testid="slide-thumbnail-select-0"]').focus();
    cy.press(Cypress.Keyboard.Keys.SPACE);
    cy.get('[data-testid="selected-slide"]').should("have.text", "0");

    cy.get('button[aria-label="Reorder slide 1"]').as("firstDragHandle").focus();
    cy.get("@firstDragHandle").type("{enter}");
    cy.get("@firstDragHandle").should("have.attr", "aria-pressed", "true");
    cy.get("@firstDragHandle").type("{downarrow}");
    cy.get('[role="status"]').should("contain.text", "moved over droppable area");
    cy.get("@firstDragHandle").type("{enter}");
    cy.get("@firstDragHandle").should("not.have.attr", "aria-pressed", "true");
    cy.get('[data-testid="selected-slide"]').should("have.text", "1");
    cy.get('[data-testid="slide-thumbnail-select-1"]').should(
      "have.attr",
      "aria-current",
      "page",
    );
    cy.focused().should("have.attr", "aria-label", "Reorder slide 2");

    cy.then(() => {
      expect(
        store.getState().presentationGeneration.presentationData?.slides.map(
          (slide) => slide.id,
        ),
      ).to.deep.equal(["s2", "s1", "s3"]);
    });
  });
});
