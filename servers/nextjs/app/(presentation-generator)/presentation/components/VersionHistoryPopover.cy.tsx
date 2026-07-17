import React from "react";
import { mount } from "cypress/react";

import VersionHistoryPopover from "./VersionHistoryPopover";
import { PresentationGenerationApi } from "../../services/api/presentation-generation";

// The popover loads durable slide versions on open and restores one on click,
// then signals the parent to reload the deck.
describe("VersionHistoryPopover", () => {
  it("loads versions on open and restores the chosen one", () => {
    cy.stub(PresentationGenerationApi, "getPresentationVersions").resolves([
      {
        id: "v1",
        created_at: "2026-07-17T01:00:00Z",
        label: null,
        slide_count: 5,
      },
      {
        id: "v2",
        created_at: "2026-07-17T00:00:00Z",
        label: "복원 전 자동 저장",
        slide_count: 4,
      },
    ]);
    const restoreStub = cy
      .stub(PresentationGenerationApi, "restorePresentationVersion")
      .resolves({})
      .as("restore");
    const onRestored = cy.stub().as("onRestored");

    mount(
      <VersionHistoryPopover presentationId="p1" onRestored={onRestored} />
    );

    // Opening the popover triggers the version fetch.
    cy.get('button[title="버전 기록"]').click();
    cy.contains("5개 슬라이드").should("be.visible");
    cy.contains("복원 전 자동 저장").should("exist");

    // Restore the first (newest) version.
    cy.contains("button", "복원").first().click();

    cy.get("@restore").should("have.been.calledWith", "p1", "v1");
    cy.get("@onRestored").should("have.been.called");
  });

  it("shows an empty state when there are no versions", () => {
    cy.stub(PresentationGenerationApi, "getPresentationVersions").resolves([]);

    mount(
      <VersionHistoryPopover presentationId="p1" onRestored={cy.stub()} />
    );

    cy.get('button[title="버전 기록"]').click();
    cy.contains("아직 저장된 버전이 없습니다.").should("be.visible");
  });
});
