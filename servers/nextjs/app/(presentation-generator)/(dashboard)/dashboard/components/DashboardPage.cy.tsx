import React from "react";
import { mount } from "cypress/react";

import DashboardPage from "./DashboardPage";
import { DashboardApi } from "@/app/(presentation-generator)/services/api/dashboard";

describe("DashboardPage", () => {
  it("shows a recoverable error (not the empty state) when loading fails", () => {
    // A real fetch failure must NOT be rendered as "no presentations yet" —
    // that reads as account-wide data loss. The grid's retry UI should show.
    cy.stub(DashboardApi, "getPresentations").rejects(
      new Error("Internal Server Error")
    );

    mount(<DashboardPage />);

    cy.contains("발표자료를 불러오지 못했습니다").should("be.visible");
    cy.contains("button", "재시도").should("be.visible");
    cy.contains("아직 발표자료가 없습니다").should("not.exist");
  });

  it("shows the empty state when there are genuinely no presentations", () => {
    cy.stub(DashboardApi, "getPresentations").resolves([]);

    mount(<DashboardPage />);

    cy.contains("아직 발표자료가 없습니다").should("be.visible");
    cy.contains("button", "재시도").should("not.exist");
  });
});
