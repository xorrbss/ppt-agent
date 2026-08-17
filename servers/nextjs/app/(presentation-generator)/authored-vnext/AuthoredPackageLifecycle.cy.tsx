import React from "react";
import { NextIntlClientProvider } from "next-intl";

import enMessages from "@/i18n/messages/en.json";

import AuthoredPackageLifecycle from "./AuthoredPackageLifecycle";

describe("AuthoredPackageLifecycle", () => {
  it("lets the user close the imported-PPTX panel", () => {
    cy.mount(
      <NextIntlClientProvider locale="en" messages={enMessages} timeZone="UTC">
        <AuthoredPackageLifecycle masterContract={{}} />
      </NextIntlClientProvider>,
    );

    cy.get('[data-testid="authored-package-lifecycle"]').should("be.visible");
    cy.get('[data-testid="authored-package-close"]').click();
    cy.get('[data-testid="authored-package-lifecycle"]').should("not.exist");
  });
});
