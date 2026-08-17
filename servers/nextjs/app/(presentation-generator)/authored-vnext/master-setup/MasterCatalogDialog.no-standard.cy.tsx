import React from "react";
import { NextIntlClientProvider } from "next-intl";

import koMessages from "@/i18n/messages/ko.json";
import { AuthoredVnextMasterClient } from "@/lib/authored-vnext/master/client";

import MasterCatalogDialog from "./MasterCatalogDialog";

const response = (body: unknown) =>
  new Response(JSON.stringify(body), {
    status: 200,
    headers: { "Content-Type": "application/json" },
  });

describe("MasterCatalogDialog company-standard selection", () => {
  it("applies the explicit no-company-standard option", () => {
    const onApply = cy.stub().as("applyMaster");
    const onClear = cy.stub().resolves().as("clearMaster");
    const onOpenChange = cy.stub().as("openChange");
    const client = new AuthoredVnextMasterClient(() =>
      Promise.resolve(response({ items: [], nextCursor: null })),
    );

    cy.mount(
      <NextIntlClientProvider locale="ko" messages={koMessages} timeZone="Asia/Seoul">
        <MasterCatalogDialog
          client={client}
          onApply={onApply}
          onClear={onClear}
          onOpenChange={onOpenChange}
          open
          selectedMasterId="master:previous-standard"
        />
      </NextIntlClientProvider>,
    );

    cy.get('[data-testid="no-company-standard-option"] button')
      .should("contain", "선택 안 함")
      .click();
    cy.contains("회사 표준 없이 생성합니다.").should("be.visible");
    cy.contains("button", "회사 표준 없이 생성").click();

    cy.get("@clearMaster").should("have.been.calledOnce");
    cy.get("@applyMaster").should("not.have.been.called");
    cy.get("@openChange").should("have.been.calledWith", false);
  });
});
