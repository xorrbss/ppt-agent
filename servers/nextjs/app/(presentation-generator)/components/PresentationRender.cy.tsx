import React from "react";
import { Provider } from "react-redux";

import { makeStore } from "@/store/store";
import { Schema as KoreanBizCoverSchema } from "@/app/presentation-templates/korean-biz/CoverSlideLayout";
import SlideScale from "./PresentationRender";

describe("SlideScale interaction defaults", () => {
  it("keeps thumbnail-style renders read-only unless edit mode is explicit", () => {
    const content = KoreanBizCoverSchema.parse({});
    content.image.__image_url__ = "/accessibility-image.png";

    cy.mount(
      <Provider store={makeStore()}>
        <SlideScale
          isClickable={false}
          slide={{
            id: "thumbnail-slide",
            index: 0,
            layout: "korean-biz:korean-biz-cover",
            layout_group: "korean-biz",
            content,
            properties: {},
          }}
        />
      </Provider>,
    );

    cy.get(".editable-layout-wrapper").should("not.exist");
    cy.wait(500);
    cy.get('img[src="/accessibility-image.png"]').should("exist");
    cy.get('img[src="/accessibility-image.png"]').should(
      "not.have.attr",
      "tabindex",
    );
    cy.get('img[src="/accessibility-image.png"]').should(
      "not.have.attr",
      "role",
      "button",
    );
    cy.get('img[src="/accessibility-image.png"]').should(
      "not.have.attr",
      "data-editable-asset",
    );
    cy.get('[data-editable-asset="image"]').should("not.exist");
  });
});
