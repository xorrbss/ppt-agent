import React from "react";
import { Provider } from "react-redux";
import { mount } from "cypress/react";
import { NextIntlClientProvider } from "next-intl";

import { makeStore } from "@/store/store";
import koMessages from "@/i18n/messages/ko.json";
import TemplateSelection from "./TemplateSelection";

describe("TemplateSelection authored-only surface", () => {
  it("shows authored styles without structured or built-in generation choices", () => {
    cy.intercept("GET", "**/api/v1/ppt/authored/styles", {
      statusCode: 200,
      body: [],
    });
    mount(
      <NextIntlClientProvider locale="ko" messages={koMessages} timeZone="UTC">
        <Provider store={makeStore()}>
          <TemplateSelection />
        </Provider>
      </NextIntlClientProvider>,
    );

    cy.get('[data-testid="authored-style-section"]').should("be.visible");
    cy.contains("AI 저작 스타일").should("be.visible");
    cy.contains("Template V2").should("not.exist");
    cy.contains("레이아웃 템플릿").should("not.exist");
  });

  it("opens the sample dialog from a card and selects only on confirm", () => {
    const samplesFor = (id: string) => ({
      canvas: { width: 1280, height: 720 },
      slides: ["Cover", "Market"].map((title, index) => ({
        index: index + 1,
        title,
        thumb: `/api/v1/ppt/authored/styles/${id}/samples/${index + 1}?size=thumb`,
        full: `/api/v1/ppt/authored/styles/${id}/samples/${index + 1}?size=full`,
      })),
    });
    const catalogueStyle = (id: string, name: string) => ({
      id,
      name,
      description: `${name} description`,
      category: "business",
      tags: ["tag"],
      use_cases: ["use"],
      preview: { bg: "#FFFFFF", accent: "#2563EB", palette: ["#FFFFFF"], variant: "clean-light" },
      samples: samplesFor(id),
    });
    cy.intercept("GET", "**/api/v1/ppt/authored/styles", {
      statusCode: 200,
      body: [catalogueStyle("default", "Default"), catalogueStyle("neon-venture", "Neon Venture")],
    });
    cy.intercept("GET", "**/api/v1/ppt/authored/styles/*/samples/*", {
      statusCode: 200,
      headers: { "content-type": "image/svg+xml" },
      body: '<svg xmlns="http://www.w3.org/2000/svg" width="16" height="9"><rect width="16" height="9" fill="#999"/></svg>',
    });
    mount(
      <NextIntlClientProvider locale="ko" messages={koMessages} timeZone="UTC">
        <Provider store={makeStore()}>
          <TemplateSelection />
        </Provider>
      </NextIntlClientProvider>,
    );

    cy.get('[data-testid="authored-style-grid"]').should("have.attr", "role", "list");
    cy.get('[data-testid="authored-style-select-neon-venture"]').should("not.have.attr", "role");
    cy.get('[data-testid="authored-style-select-neon-venture"]').should("not.have.attr", "aria-current");
    cy.get('[data-testid="authored-style-select-neon-venture"]')
      .should("have.attr", "aria-haspopup", "dialog");
    cy.get('[data-testid="authored-style-select-neon-venture"]').click();

    cy.get('[data-testid="style-sample-dialog"]').should("be.visible");
    cy.get('[data-testid="style-sample-title"]').should("have.text", "Neon Venture");
    cy.get('[data-testid="style-sample-stage-image"]').should("have.attr", "alt", "1. Cover");
    cy.get('[data-testid="authored-style-select-neon-venture"]').should("not.have.attr", "aria-current");
    cy.get('[data-testid="authored-style-select-default"]').should("have.attr", "aria-current", "true");

    cy.get('[data-testid="style-sample-confirm"]').click();
    cy.get('[data-testid="style-sample-dialog"]').should("not.exist");
    cy.get('[data-testid="authored-style-select-neon-venture"]').should("have.attr", "aria-current", "true");
    cy.get('[data-testid="authored-style-select-default"]').should("not.have.attr", "aria-current");
  });
});
