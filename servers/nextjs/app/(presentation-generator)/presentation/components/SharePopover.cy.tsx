import React from "react";
import { mount } from "cypress/react";

import SharePopover from "./SharePopover";
import { PresentationGenerationApi } from "../../services/api/presentation-generation";

describe("SharePopover", () => {
  it("creates a share link when not yet shared", () => {
    cy.stub(PresentationGenerationApi, "getShareStatus").resolves({
      shared: false,
      share_token: null,
    });
    cy.stub(PresentationGenerationApi, "enableShare")
      .resolves({ shared: true, share_token: "tok_new_1234567890" })
      .as("enable");

    mount(<SharePopover presentationId="p1" />);

    cy.contains("button", "공유").click();
    cy.contains("button", "공유 링크 만들기").click();

    cy.get("@enable").should("have.been.calledWith", "p1", false);
    cy.get("input[readonly]")
      .invoke("val")
      .should("contain", "/p/tok_new_1234567890");
  });

  it("shows the existing link and can disable sharing", () => {
    cy.stub(PresentationGenerationApi, "getShareStatus").resolves({
      shared: true,
      share_token: "tok_existing_1234567890",
    });
    cy.stub(PresentationGenerationApi, "disableShare")
      .resolves({ shared: false, share_token: null })
      .as("disable");

    mount(<SharePopover presentationId="p1" />);

    cy.contains("button", "공유").click();
    cy.get("input[readonly]")
      .invoke("val")
      .should("contain", "/p/tok_existing_1234567890");

    cy.contains("button", "공유 끄기").click();
    cy.get("@disable").should("have.been.calledWith", "p1");
  });
});
