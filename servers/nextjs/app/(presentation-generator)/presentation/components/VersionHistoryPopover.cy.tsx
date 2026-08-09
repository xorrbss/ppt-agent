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
    const workspaceCommand = cy.stub().as("workspaceCommand").resolves();

    mount(
      <VersionHistoryPopover
        presentationId="p1"
        workspaceCommandsEnabled
        onWorkspaceCommand={workspaceCommand}
      />
    );

    // Opening the popover triggers the version fetch.
    cy.get('button[title="버전 기록"]').click();
    cy.contains("5개 슬라이드").should("be.visible");
    cy.contains("복원 전 자동 저장").should("exist");

    // Restore the first (newest) version.
    cy.contains("button", "복원").first().click();

    cy.get("@workspaceCommand").should("have.been.calledOnce");
  });

  it("shows an empty state when there are no versions", () => {
    cy.stub(PresentationGenerationApi, "getPresentationVersions").resolves([]);

    mount(
      <VersionHistoryPopover presentationId="p1" />
    );

    cy.get('button[title="버전 기록"]').click();
    cy.contains("아직 저장된 버전이 없습니다.").should("be.visible");
  });

  it("dispatches a durable restore command instead of a direct write", () => {
    cy.stub(PresentationGenerationApi, "getPresentationVersions").resolves([
      {
        id: "version-workspace",
        created_at: "2026-08-03T00:00:00Z",
        label: "검토 완료",
        slide_count: 7,
      },
    ]);
    const workspaceCommand = cy.stub().as("workspaceCommand").resolves();

    mount(
      <VersionHistoryPopover
        presentationId="p1"
        workspaceCommandsEnabled
        onWorkspaceCommand={workspaceCommand}
      />
    );

    cy.get('[data-testid="version-history-trigger"]').click();
    cy.get('[data-testid="version-restore"]').click();

    cy.get("@workspaceCommand").should("have.been.calledOnce");
    cy.wrap(null).then(() => {
      expect(workspaceCommand.firstCall.args[0]).to.include("버전으로 복원해 줘");
      expect(workspaceCommand.firstCall.args[1]).to.deep.equal({
        commandType: "version.restore",
        context: {
          version_id: "version-workspace",
          source: "version_history",
        },
      });
    });
  });
});
