import React, { useCallback, useState } from "react";

import "@/app/globals.css";
import { ApiMockManager, testViewports } from "@/cypress/support/testHelpers";
import type { LLMConfig } from "@/types/llm_config";
import { Toaster } from "@/components/ui/sonner";
import enMessages from "@/i18n/messages/en.json";
import { NextIntlClientProvider } from "next-intl";
import TextProvider from "./TextProvider";

/**
 * TextProvider is a controlled component: every edit is reported through
 * `onInputChange` and only shows up on screen once the parent writes it back.
 * The settings page owns that state, so the spec reproduces it here.
 *
 * `onInputChange` must keep a stable identity — the component lists it in the
 * dependency array of the effect that resets the selected model, so a fresh
 * closure per render would re-run that effect forever.
 */
const ConfigHarness = ({
  initialConfig,
  onChangeSpy,
}: {
  initialConfig: LLMConfig;
  onChangeSpy?: (value: string | boolean, field: string) => void;
}) => {
  const [config, setConfig] = useState<LLMConfig>(initialConfig);

  const onInputChange = useCallback(
    (value: string | boolean, field: string) => {
      onChangeSpy?.(value, field);
      setConfig((previous) => ({ ...previous, [field]: value }));
    },
    [onChangeSpy]
  );

  return <TextProvider llmConfig={config} onInputChange={onInputChange} />;
};

const mountProvider = (initialConfig: LLMConfig, onChangeSpy?: (value: string | boolean, field: string) => void) =>
  cy.mount(
    <NextIntlClientProvider locale="en" messages={enMessages} timeZone="UTC">
      <ConfigHarness initialConfig={initialConfig} onChangeSpy={onChangeSpy} />
      <Toaster />
    </NextIntlClientProvider>
  );

describe("<TextProvider />", () => {
  let apiMocks: ApiMockManager;

  beforeEach(() => {
    apiMocks = new ApiMockManager();
    cy.viewport(testViewports.desktop.width, testViewports.desktop.height);
  });

  afterEach(() => {
    apiMocks.cleanup();
  });

  it("renders the saved provider and blocks the model lookup until credentials exist", () => {
    mountProvider({ LLM: "openai" });

    cy.contains("텍스트 생성 설정").should("be.visible");
    cy.contains("텍스트 콘텐츠를 어디서 생성할지 선택하세요").should("be.visible");
    cy.contains("label", "텍스트 제공자 선택").should("be.visible");
    cy.contains('button[role="combobox"]', "OpenAI").should("be.visible");
    cy.contains("label", "OPENAI *").should("be.visible");
    cy.get('button[aria-label="Toggle credential visibility"]').should("be.visible");

    // No API key yet, so the lookup cannot run.
    cy.contains("button", "모델 확인").should("be.disabled");
    // Nothing has been checked yet — the empty-result warning must stay hidden.
    cy.contains("모델을 찾을 수 없습니다. 제공자 인증 정보가").should("not.exist");
  });

  it("switches provider from the searchable list and swaps the credential field", () => {
    const onChangeSpy = cy.spy().as("onInputChange");

    mountProvider({ LLM: "openai" }, onChangeSpy);

    cy.contains('button[role="combobox"]', "OpenAI").click();
    cy.get('input[placeholder="제공자 검색..."]').type("anthro");
    cy.get("[cmdk-item]").contains("Anthropic").click();

    cy.get("@onInputChange").should("have.been.calledWith", "anthropic", "LLM");
    cy.contains('button[role="combobox"]', "Anthropic").should("be.visible");
    cy.contains("label", "ANTHROPIC *").should("be.visible");
    cy.contains("label", "OPENAI *").should("not.exist");
  });

  it("fails closed for an unknown configured provider instead of using OpenAI", () => {
    mountProvider({ LLM: "future-provider" });

    cy.contains('button[role="combobox"]', "future-provider").should("be.visible");
    cy.get('[role="alert"]').within(() => {
      cy.contains("Unsupported provider").should("be.visible");
      cy.contains("future-provider is unknown or disabled").should("be.visible");
    });
    cy.contains("label", "OPENAI *").should("not.exist");
    cy.contains("button", "모델 확인").should("not.exist");
  });

  it("loads models for the entered API key and lets the user pick one", () => {
    apiMocks.setupPostIntercept(
      "**/api/v1/ppt/openai/models/available",
      ["gpt-4o", "gpt-4.1"] as unknown as Record<string, any>,
      "openaiModels"
    );

    mountProvider({ LLM: "openai", OPENAI_API_KEY: "sk-test-key" });

    cy.contains("button", "모델 확인").should("not.be.disabled").click();
    cy.wait("@openaiModels")
      .its("request.body.api_key")
      .should("equal", "sk-test-key");

    cy.contains("label", "OpenAI 모델 선택").should("be.visible");
    cy.contains('button[role="combobox"]', "gpt-4o").should("be.visible");

    cy.contains('button[role="combobox"]', "gpt-4o").click();
    cy.get("[cmdk-item]").contains("gpt-4.1").click();
    cy.contains('button[role="combobox"]', "gpt-4.1").should("be.visible");
  });

  it("invalidates discovered models when a URL credential changes", () => {
    const onChangeSpy = cy.spy().as("onInputChange");
    apiMocks.setupPostIntercept(
      "**/api/v1/ppt/openai/models/available",
      ["model-a", "model-b"] as unknown as Record<string, any>,
      "litellmModels"
    );

    mountProvider(
      {
        LLM: "litellm",
        LITELLM_BASE_URL: "https://gateway-one.example/v1",
      },
      onChangeSpy
    );

    cy.contains("button", "모델 확인").click();
    cy.wait("@litellmModels")
      .its("request.body.url")
      .should("equal", "https://gateway-one.example/v1");
    cy.contains('button[role="combobox"]', "model-a").should("be.visible");

    cy.get('input[type="url"]')
      .clear()
      .type("https://gateway-two.example/v1");

    cy.get("@onInputChange").should(
      "have.been.calledWith",
      "",
      "LITELLM_MODEL"
    );
    cy.contains('button[role="combobox"]', "model-a").should("not.exist");
    cy.contains("button", "모델 확인").should("be.visible");
  });

  it("warns the user when the provider returns an error instead of models", () => {
    apiMocks.setupErrorIntercept(
      "**/api/v1/ppt/openai/models/available",
      500,
      "Internal Server Error",
      "openaiModelsError"
    );

    mountProvider({ LLM: "openai", OPENAI_API_KEY: "sk-broken-key" });

    cy.contains("button", "모델 확인").click();

    cy.contains(
      "모델을 찾을 수 없습니다. 제공자 인증 정보가 올바른지, 선택한 제공자에 연결할 수 있는지 확인하세요."
    ).should("be.visible");
    cy.contains("Unable to load models").should("be.visible");
    cy.contains("Check the OpenAI provider configuration and try again.").should("be.visible");
    // The lookup stays available so the user can retry after fixing the key.
    cy.contains("button", "모델 확인").should("be.visible");
    cy.contains("label", "OpenAI 모델 선택").should("not.exist");
  });

  it("runs the ChatGPT sign-in flow instead of the server-managed notice", () => {
    apiMocks.setupGetIntercept(
      "**/api/v1/ppt/codex/auth/status",
      { status: "unauthenticated" },
      "codexStatus"
    );

    mountProvider({ LLM: "codex" });

    cy.wait("@codexStatus");
    cy.contains("button", "ChatGPT로 로그인").should("be.visible");
    // The OAuth flow lives in this app, so the read-only notice must not win.
    cy.contains("This provider is authenticated through the server-managed sign-in flow.").should("not.exist");
  });

  it("shows the connected ChatGPT account and seeds the default model", () => {
    apiMocks.setupGetIntercept(
      "**/api/v1/ppt/codex/auth/status",
      {
        status: "authenticated",
        account_id: "acct-42",
        username: "presenton-admin",
        email: "admin@example.com",
      },
      "codexStatus"
    );

    mountProvider({ LLM: "codex" });

    cy.wait("@codexStatus");
    cy.contains("presenton-admin").should("be.visible");
    cy.contains("admin@example.com").should("be.visible");
    cy.contains("ChatGPT에 로그인됨").should("be.visible");
    cy.get('button[title="토큰 갱신"]').should("be.visible");
    cy.get('button[title="로그아웃"]').should("be.visible");

    cy.contains("label", "GPT 모델 선택").should("be.visible");
    cy.contains('button[role="combobox"]', "GPT-5.6 Sol").should("be.visible");

    cy.contains('button[role="combobox"]', "GPT-5.6 Sol").click();
    cy.get("[cmdk-item]").contains("GPT-5.4 mini").click();
    cy.contains('button[role="combobox"]', "GPT-5.4 mini").should("be.visible");
  });

  it("auto-loads supported Ollama models and exposes the custom URL field", () => {
    apiMocks.setupGetIntercept(
      "**/api/v1/ppt/ollama/models/supported",
      [
        { value: "llama3.2:3b", label: "llama3.2:3b", size: "2.0 GB" },
        { value: "qwen3:8b", label: "qwen3:8b", size: "5.2 GB" },
      ] as unknown as Record<string, any>,
      "ollamaModels"
    );

    mountProvider({ LLM: "ollama" });

    cy.wait("@ollamaModels");

    cy.contains("label", "지원되는 모델 선택").should("be.visible");
    cy.contains('button[role="combobox"]', "llama3.2:3b (2.0 GB)").should(
      "be.visible"
    );

    cy.contains("label", "OLLAMA URL *").should("be.visible");
    cy.contains("label", "OLLAMA URL *")
      .find('input[type="url"]')
      .should("have.value", "");
  });
});
