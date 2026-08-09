import React from "react";

import { DashboardApi } from "@/app/(presentation-generator)/services/api/dashboard";
import { SharePopover } from "./SharePopover";

const inactive = {
  shared: false,
  share_token: null,
  expires_at: null,
  view_count: 0,
  expired: false,
};

describe("SharePopover", () => {
  it("creates a link with an explicit UTC expiry", () => {
    cy.stub(DashboardApi, "getShareStatus").resolves(inactive);
    const enableShare = cy
      .stub(DashboardApi, "enableShare")
      .resolves({
        shared: true,
        share_token: "new-token",
        expires_at: "2026-09-09T00:00:00Z",
        view_count: 0,
        expired: false,
      });

    cy.mount(
      <SharePopover
        presentationId="deck-1"
        title="분기 계획"
        open
        onOpenChange={cy.stub()}
      />,
    );

    cy.contains("발표자료 공유").should("be.visible");
    cy.get("select").select("30");
    cy.contains("button", "공유 링크 만들기").click();
    cy.wrap(enableShare).should("have.been.calledOnce");
    cy.then(() => {
      const [presentationId, expiresAt, regenerate] = enableShare.firstCall.args;
      expect(presentationId).to.equal("deck-1");
      expect(regenerate).to.equal(false);
      expect(new Date(expiresAt).toISOString()).to.equal(expiresAt);
      const remainingDays =
        (new Date(expiresAt).getTime() - Date.now()) / 86_400_000;
      expect(remainingDays).to.be.within(29.9, 30.1);
    });
    cy.get('input[aria-label="공유 링크"]').should(
      "have.value",
      `${window.location.origin}/p/new-token`,
    );
  });

  it("shows expiry and views and rotates the current link", () => {
    const active = {
      shared: true,
      share_token: "active-token",
      expires_at: "2099-08-10T00:00:00Z",
      view_count: 42,
      expired: false,
    };
    cy.stub(DashboardApi, "getShareStatus").resolves(active);
    const enableShare = cy.stub(DashboardApi, "enableShare").resolves({
      ...active,
      share_token: "rotated-token",
      view_count: 0,
    });

    cy.mount(
      <SharePopover
        presentationId="deck-2"
        title="공유 중인 자료"
        open
        onOpenChange={cy.stub()}
      />,
    );

    cy.contains("조회 42회").should("be.visible");
    cy.contains("2099").should("be.visible");
    cy.contains("button", "링크 재생성").click();
    cy.wrap(enableShare).should("have.been.calledOnce");
    cy.then(() => {
      expect(enableShare.firstCall.args[0]).to.equal("deck-2");
      expect(enableShare.firstCall.args[2]).to.equal(true);
    });
  });

  it("clears the previous deck token when the next status request fails", () => {
    const getShareStatus = cy.stub(DashboardApi, "getShareStatus");
    getShareStatus.onFirstCall().resolves({
      shared: true,
      share_token: "deck-a-token",
      expires_at: null,
      view_count: 3,
      expired: false,
    });
    getShareStatus.onSecondCall().rejects(new Error("request failed"));

    function Harness() {
      const [deck, setDeck] = React.useState({ id: "deck-a", title: "Deck A" });
      return (
        <>
          <button
            type="button"
            onClick={() => setDeck({ id: "deck-b", title: "Deck B" })}
          >
            Open B
          </button>
          <SharePopover
            presentationId={deck.id}
            title={deck.title}
            open
            onOpenChange={cy.stub()}
          />
        </>
      );
    }

    cy.mount(<Harness />);
    cy.get('input[aria-label="공유 링크"]').should(
      "have.value",
      `${window.location.origin}/p/deck-a-token`,
    );

    cy.contains("button", "Open B").click({ force: true });
    cy.contains("Deck B").should("be.visible");
    cy.contains("공유 설정을 불러오지 못했습니다.").should(
      "be.visible",
    );
    cy.get('input[aria-label="공유 링크"]').should("not.exist");
    cy.contains("/p/deck-a-token").should("not.exist");
  });
});
