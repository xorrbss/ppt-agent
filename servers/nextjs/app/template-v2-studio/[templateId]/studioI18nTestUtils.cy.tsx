import type { ReactNode } from "react";
import { NextIntlClientProvider } from "next-intl";

import enMessages from "@/i18n/messages/en.json";

export function mountWithStudioI18n(component: ReactNode) {
  return cy.mount(
    <NextIntlClientProvider locale="en" messages={enMessages} timeZone="UTC">
      {component}
    </NextIntlClientProvider>,
  );
}
