import React from "react";
import { NextIntlClientProvider } from "next-intl";
import { Provider } from "react-redux";

import { makeStore } from "@/store/store";
import enMessages from "@/i18n/messages/en.json";
import { Schema as KoreanBizCoverSchema } from "@/app/presentation-templates/korean-biz/CoverSlideLayout";
import SlideScale from "./PresentationRender";

describe("SlideScale interaction defaults", () => {
  it("keeps thumbnail-style renders read-only unless edit mode is explicit", () => {
    const content = KoreanBizCoverSchema.parse({});
    content.image.__image_url__ = "/accessibility-image.png";

    cy.mount(
      <NextIntlClientProvider locale="en" messages={enMessages} timeZone="UTC">
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
        </Provider>
      </NextIntlClientProvider>,
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

  it("renders persisted Template V2 UI without looking up a legacy native layout", () => {
    cy.mount(
      <NextIntlClientProvider locale="en" messages={enMessages} timeZone="UTC">
        <Provider store={makeStore()}>
          <SlideScale
            slide={{
              id: "template-v2-slide",
              index: 0,
              layout: "pptx_layout_7572554409e6",
              layout_group: "native",
              content: {},
              properties: {},
              ui: {
                id: "pptx_layout_7572554409e6",
                components: [
                  {
                    id: "body",
                    position: { x: 0, y: 0 },
                    elements: [
                      {
                        type: "text",
                        position: { x: 80, y: 80 },
                        size: { width: 600, height: 120 },
                        text: "Template V2 persisted content",
                      },
                    ],
                  },
                ],
              },
            }}
          />
        </Provider>
      </NextIntlClientProvider>,
    );

    cy.get('[aria-label="Template v2 slide preview"]').should("be.visible");
    cy.contains("Template V2 persisted content").should("be.visible");
    cy.contains("layout not found").should("not.exist");
  });
});
