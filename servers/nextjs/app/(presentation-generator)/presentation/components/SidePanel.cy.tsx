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
});
