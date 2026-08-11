import React from "react";
import { Provider } from "react-redux";
import { AppRouterContext } from "next/dist/shared/lib/app-router-context.shared-runtime";
import { PathnameContext } from "next/dist/shared/lib/hooks-client-context.shared-runtime";
import { NextIntlClientProvider } from "next-intl";
import enMessages from "@/i18n/messages/en.json";

import { createMockRouter, ApiMockManager, testViewports } from "@/cypress/support/testHelpers";
import { store } from "@/store/store";
import { setCanChangeKeys, setLLMConfig } from "@/store/slices/userConfig";
import { Toaster } from "@/components/ui/sonner";
import type { LLMConfig } from "@/types/llm_config";
import SettingsPage from "./SettingPage";

/** A config that passes validation: OpenAI text + GPT Image 1.5 (same key). */
const validConfig: LLMConfig = {
  LLM: "openai",
  OPENAI_API_KEY: "sk-test-key",
  OPENAI_MODEL: "gpt-4o",
  IMAGE_PROVIDER: "gpt-image-1.5",
};

describe("<SettingsPage />", () => {
  let apiMocks: ApiMockManager;
  let router: ReturnType<typeof createMockRouter>;

  const mountSettings = (llmConfig: LLMConfig, canChangeKeys = true) => {
    store.dispatch(setLLMConfig(llmConfig));
    store.dispatch(setCanChangeKeys(canChangeKeys));
    router = createMockRouter();

    cy.mount(
      <NextIntlClientProvider locale="en" messages={enMessages} timeZone="UTC">
        <AppRouterContext.Provider value={router as never}>
          <PathnameContext.Provider value="/settings">
            <Provider store={store}>
              <SettingsPage />
              <Toaster />
            </Provider>
          </PathnameContext.Provider>
        </AppRouterContext.Provider>
      </NextIntlClientProvider>
    );
  };

  beforeEach(() => {
    apiMocks = new ApiMockManager();
    cy.viewport(testViewports.desktop.width, testViewports.desktop.height);
    // Keep telemetry off so mounting never reaches the real Mixpanel host.
    apiMocks.setupGetIntercept(
      "**/api/telemetry-status",
      { telemetryEnabled: false },
      "telemetryStatus"
    );
  });

  afterEach(() => {
    apiMocks.cleanup();
  });

  it("renders the settings shell with the active provider summary", () => {
    mountSettings(validConfig);

    cy.contains("h3", "설정").should("be.visible");
    cy.contains("OpenAI (gpt-4o) · GPT Image 1.5").should("be.visible");
    cy.contains("button", "텍스트 제공자").should("be.visible");
    cy.contains("button", "설정 저장").should("be.visible").and("not.be.disabled");
  });

  it("redirects to the dashboard and renders nothing when keys are locked", () => {
    mountSettings({
      LLM: "openai",
      IMAGE_PROVIDER: "gpt-image-1.5",
    }, false);

    cy.contains("설정 저장").should("not.exist");
    cy.get("h3").should("not.exist");
    cy.get("[data-sonner-toast]").should("not.exist");
    cy.wrap(null).should(() => {
      expect(router.push).to.have.been.calledWith("/dashboard");
    });
  });

  it("switches to the sign-out section and hides the save button there", () => {
    mountSettings(validConfig);

    cy.contains("button", "로그아웃").click();

    cy.contains(
      "이 배포에서 세션을 종료합니다. 앱을 사용하고 API에 접근하려면 다시 로그인해야 합니다."
    ).should("be.visible");
    cy.contains("button", "설정 저장").should("not.exist");
  });

  it("persists the configuration and confirms the save", () => {
    apiMocks.setupPostIntercept("**/api/user-config", {}, "saveUserConfig");

    mountSettings(validConfig);

    cy.contains("button", "설정 저장").click();

    cy.wait("@saveUserConfig").its("request.body").should((body: string) => {
      expect(JSON.parse(body)).to.include({
        LLM: "openai",
        OPENAI_MODEL: "gpt-4o",
      });
    });
    cy.contains("설정 저장됨").should("be.visible");
    cy.contains("설정이 성공적으로 저장되었습니다.").should("be.visible");
  });

  it("blocks the save and explains why when the provider config is incomplete", () => {
    apiMocks.setupPostIntercept("**/api/user-config", {}, "saveUserConfig");

    // Model chosen but no API key: validation must fail before any request.
    mountSettings({
      LLM: "openai",
      OPENAI_MODEL: "gpt-4o",
      IMAGE_PROVIDER: "gpt-image-1.5",
    });

    cy.contains("button", "설정 저장").click();

    cy.contains("설정을 저장할 수 없습니다").should("be.visible");
    cy.contains("OpenAI API key is required.").should("be.visible");
    cy.get("@saveUserConfig.all").should("have.length", 0);
  });
});
