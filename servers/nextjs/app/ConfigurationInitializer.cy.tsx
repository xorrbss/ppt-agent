import React from "react";
import { NextIntlClientProvider } from "next-intl";
import { Provider } from "react-redux";
import { AppRouterContext } from "next/dist/shared/lib/app-router-context.shared-runtime";
import { PathnameContext } from "next/dist/shared/lib/hooks-client-context.shared-runtime";

import { createMockRouter } from "@/cypress/support/testHelpers";
import enMessages from "@/i18n/messages/en.json";
import { makeStore } from "@/store/store";
import { ConfigurationInitializer } from "./ConfigurationInitializer";

describe("<ConfigurationInitializer />", () => {
  const mountInitializer = (router: ReturnType<typeof createMockRouter>) => {
    cy.mount(
      <NextIntlClientProvider locale="en" messages={enMessages} timeZone="UTC">
        <AppRouterContext.Provider value={router as never}>
          <PathnameContext.Provider value="/upload">
            <Provider store={makeStore()}>
              <ConfigurationInitializer>
                <div>Initialized application</div>
              </ConfigurationInitializer>
            </Provider>
          </PathnameContext.Provider>
        </AppRouterContext.Provider>
      </NextIntlClientProvider>
    );
  };

  const interceptEditableConfig = (body: Record<string, unknown>) => {
    cy.intercept("GET", "**/api/can-change-keys", {
      statusCode: 200,
      body: { canChange: true },
    }).as("canChangeKeys");
    cy.intercept("GET", "**/api/user-config", {
      statusCode: 200,
      body,
    }).as("userConfig");
  };

  it("does not send a redacted stored custom key to the provider endpoint or reopen setup", () => {
    const router = createMockRouter();

    interceptEditableConfig({
      LLM: "custom",
      CUSTOM_LLM_URL: "https://custom.example.test/v1",
      CUSTOM_LLM_API_KEY: "__configured__",
      CUSTOM_MODEL: "private-model",
      DISABLE_IMAGE_GENERATION: true,
    });
    cy.intercept(
      "POST",
      "**/api/v1/admin/provider-settings/custom-model-available",
      {
        statusCode: 200,
        body: { available: true },
      }
    ).as("storedCustomModelCheck");
    cy.intercept("POST", "**/api/v1/ppt/openai/models/available", {
      statusCode: 401,
      body: { detail: "Unauthorized" },
    }).as("customModelLookup");

    mountInitializer(router);

    cy.wait(["@canChangeKeys", "@userConfig"]);
    cy.wait("@storedCustomModelCheck").its("request.body").should("be.empty");
    cy.contains("Initialized application").should("be.visible");
    cy.get("@customModelLookup.all").should("have.length", 0);
    cy.wrap(null).should(() => {
      const replace = router.replace as unknown as { called: boolean };
      expect(replace.called).to.equal(false);
    });
  });

  it("reopens setup when the server cannot validate the stored custom endpoint", () => {
    const router = createMockRouter();

    interceptEditableConfig({
      LLM: "custom",
      CUSTOM_LLM_URL: "https://invalid.example.test/v1",
      CUSTOM_LLM_API_KEY: "__configured__",
      CUSTOM_MODEL: "private-model",
      DISABLE_IMAGE_GENERATION: true,
    });
    cy.intercept(
      "POST",
      "**/api/v1/admin/provider-settings/custom-model-available",
      {
        statusCode: 502,
        body: { detail: "Unable to validate stored custom LLM configuration" },
      }
    ).as("storedCustomModelCheck");
    cy.intercept("POST", "**/api/v1/ppt/openai/models/available", []).as(
      "customModelLookup"
    );

    mountInitializer(router);

    cy.wait("@storedCustomModelCheck");
    cy.get("@customModelLookup.all").should("have.length", 0);
    cy.wrap(null).should(() => {
      expect(router.replace).to.have.been.calledWith("/?setup=llm");
    });
  });

  it("keeps checking a newly supplied raw key and redirects on authentication failure", () => {
    const router = createMockRouter();

    interceptEditableConfig({
      LLM: "custom",
      CUSTOM_LLM_URL: "https://custom.example.test/v1",
      CUSTOM_LLM_API_KEY: "new-test-key",
      CUSTOM_MODEL: "private-model",
      DISABLE_IMAGE_GENERATION: true,
    });
    cy.intercept(
      "POST",
      "**/api/v1/admin/provider-settings/custom-model-available",
      { available: true }
    ).as("storedCustomModelCheck");
    cy.intercept("POST", "**/api/v1/ppt/openai/models/available", {
      statusCode: 401,
      body: { detail: "Unauthorized" },
    }).as("customModelLookup");

    mountInitializer(router);

    cy.wait("@customModelLookup").its("request.body").should("deep.equal", {
      url: "https://custom.example.test/v1",
      api_key: "new-test-key",
    });
    cy.get("@storedCustomModelCheck.all").should("have.length", 0);
    cy.wrap(null).should(() => {
      expect(router.replace).to.have.been.calledWith("/?setup=llm");
    });
  });
});
