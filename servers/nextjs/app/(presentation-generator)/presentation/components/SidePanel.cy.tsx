import React from "react";
import { mount } from "cypress/react";
import { Provider } from "react-redux";

import { makeStore } from "@/store/store";
import { setPresentationData } from "@/store/slices/presentationGeneration";
import { createBlankPresentationSlide } from "../../_shared/blank-slide";
import SidePanel from "./SidePanel";

function mountSidePanel(mutationsEnabled: boolean) {
  const store = makeStore();
  store.dispatch(
    setPresentationData({
      id: "p1",
      language: "ko",
      layout: { name: "blank", ordered: true, slides: [] },
      n_slides: 1,
      title: "Test deck",
      slides: [
        createBlankPresentationSlide({
          id: "s1",
          presentationId: "p1",
          templateId: "blank",
          isTemplateV2: true,
        }),
      ],
      theme: null,
    }),
  );

  mount(
    <Provider store={store}>
      <SidePanel
        selectedSlide={0}
        onSlideClick={() => undefined}
        presentationId="p1"
        loading={false}
        mutationsEnabled={mutationsEnabled}
      />
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
});
