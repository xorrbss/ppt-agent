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
});
