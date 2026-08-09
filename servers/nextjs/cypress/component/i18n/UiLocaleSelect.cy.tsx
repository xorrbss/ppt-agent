import { NextIntlClientProvider } from "next-intl";

import en from "@/i18n/messages/en.json";
import { UiLocaleSelect } from "@/components/i18n/UiLocaleSelect";

function mountSelect(overrides: Partial<React.ComponentProps<typeof UiLocaleSelect>> = {}) {
  const onLocaleChange = cy.stub().as("onLocaleChange");
  cy.mount(
    <NextIntlClientProvider locale="en" messages={en}>
      <UiLocaleSelect
        failed={false}
        isPending={false}
        locale="en"
        onLocaleChange={onLocaleChange}
        {...overrides}
      />
    </NextIntlClientProvider>,
  );
}

describe("UiLocaleSelect", () => {
  it("exposes an accessible locale choice and emits a supported locale", () => {
    mountSelect();
    cy.contains("label", "Language").should("have.attr", "for");
    cy.get("select").should("have.value", "en").select("ko");
    cy.get("@onLocaleChange").should("have.been.calledOnceWith", "ko");
  });

  it("announces persistence state and a failed change", () => {
    mountSelect({ failed: true, isPending: true });
    cy.get("select")
      .should("be.disabled")
      .and("have.attr", "aria-busy", "true");
    cy.get('[role="status"]').should("contain.text", en.languageSwitcher.saving);
    cy.get('[role="alert"]').should("contain.text", en.languageSwitcher.error);
  });
});
