import React from "react";
import { mount } from "cypress/react";
import { NextIntlClientProvider } from "next-intl";

import koMessages from "@/i18n/messages/ko.json";
import VersionHistoryPopover from "./VersionHistoryPopover";
import { PresentationGenerationApi } from "../../services/api/presentation-generation";

// The component reads the active UI locale, exactly as it does in the app
// under the root provider. Asia/Seoul is deliberate: the specs below assert
// that a UTC instant is rendered in the viewer's zone, not in UTC.
//
// The browser's own zone is overridden through CDP rather than inherited from
// the host, so the assertion means the same thing on a developer's Seoul
// machine and on a CI runner in UTC.
const VIEWER_TIME_ZONE = "Asia/Seoul";

function useViewerTimeZone(timezoneId: string) {
  beforeEach(() => {
    cy.wrap(
      Cypress.automation("remote:debugger:protocol", {
        command: "Emulation.setTimezoneOverride",
        params: { timezoneId },
      }),
      { log: false },
    );
  });
}

function mountWithIntl(ui: React.ReactElement) {
  mount(
    <NextIntlClientProvider locale="ko" messages={koMessages} timeZone={VIEWER_TIME_ZONE}>
      {ui}
    </NextIntlClientProvider>,
  );
}

// The popover loads durable slide versions on open and restores one on click,
// then signals the parent to reload the deck.
describe("VersionHistoryPopover", () => {
  useViewerTimeZone(VIEWER_TIME_ZONE);

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

    mountWithIntl(
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

    // 01:00 UTC is 10:00 in Seoul. The label must show the viewer's wall clock,
    // never the raw UTC hour.
    cy.contains("7월 17일 오전 10:00").should("be.visible");
    cy.contains("오전 01:00").should("not.exist");

    // Restore the first (newest) version.
    cy.contains("button", "복원").first().click();

    cy.get("@workspaceCommand").should("have.been.calledOnce");
  });

  it("shows an empty state when there are no versions", () => {
    cy.stub(PresentationGenerationApi, "getPresentationVersions").resolves([]);

    mountWithIntl(
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

    mountWithIntl(
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
