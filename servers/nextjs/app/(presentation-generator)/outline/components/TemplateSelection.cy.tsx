import React from "react";
import { Provider } from "react-redux";
import { mount } from "cypress/react";

import { makeStore } from "@/store/store";
import TemplateSelection from "./TemplateSelection";

describe("TemplateSelection authored-only surface", () => {
  it("shows authored styles without structured or built-in generation choices", () => {
    cy.intercept("GET", "**/api/v1/ppt/authored/styles", {
      statusCode: 200,
      body: [],
    });
    mount(
      <Provider store={makeStore()}>
        <TemplateSelection />
      </Provider>,
    );

    cy.get('[data-testid="authored-style-section"]').should("be.visible");
    cy.contains("AI 저작 스타일").should("be.visible");
    cy.contains("Template V2").should("not.exist");
    cy.contains("레이아웃 템플릿").should("not.exist");
  });
});
