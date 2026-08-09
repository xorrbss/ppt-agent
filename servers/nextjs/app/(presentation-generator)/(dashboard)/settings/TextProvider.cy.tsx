import React, { useCallback, useState } from "react";

import "@/app/globals.css";
import { ApiMockManager, testViewports } from "@/cypress/support/testHelpers";
import type { LLMConfig } from "@/types/llm_config";
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
    cy.mount(<ConfigHarness initialConfig={{ LLM: "openai" }} />);

    cy.contains("텍스트 생성 설정").should("be.visible");
    cy.contains("텍스트 콘텐츠를 어디서 생성할지 선택하세요").should("be.visible");
    cy.contains("label", "텍스트 제공자 선택").should("be.visible");
    cy.contains('button[role="combobox"]', "OpenAI").should("be.visible");
    cy.contains("label", "openai API 키").should("be.visible");

    // No API key yet, so the lookup cannot run.
    cy.contains("button", "모델 확인").should("be.disabled");
    // Nothing has been checked yet — the empty-result warning must stay hidden.
    cy.contains("모델을 찾을 수 없습니다. 제공자 인증 정보가").should("not.exist");
  });

  it("switches provider from the searchable list and swaps the credential field", () => {
    const onChangeSpy = cy.spy().as("onInputChange");

    cy.mount(
      <ConfigHarness
        initialConfig={{ LLM: "openai" }}
        onChangeSpy={onChangeSpy}
      />
    );

    cy.contains('button[role="combobox"]', "OpenAI").click();
    cy.get('input[placeholder="제공자 검색..."]').type("anthro");
    cy.get("[cmdk-item]").contains("Anthropic").click();

    cy.get("@onInputChange").should("have.been.calledWith", "anthropic", "LLM");
    cy.contains('button[role="combobox"]', "Anthropic").should("be.visible");
    cy.contains("label", "anthropic API 키").should("be.visible");
    cy.contains("label", "openai API 키").should("not.exist");
  });

  it("loads models for the entered API key and lets the user pick one", () => {
    apiMocks.setupPostIntercept(
      "**/api/v1/ppt/openai/models/available",
      ["gpt-4o", "gpt-4.1"] as unknown as Record<string, any>,
      "openaiModels"
    );

    cy.mount(
      <ConfigHarness
        initialConfig={{ LLM: "openai", OPENAI_API_KEY: "sk-test-key" }}
      />
    );

    cy.contains("button", "모델 확인").should("not.be.disabled").click();
    cy.wait("@openaiModels")
      .its("request.body.api_key")
      .should("equal", "sk-test-key");

    cy.contains("label", "OpenAI 모델 선택").should("be.visible");
    // gpt-4.1 is the preferred default when the provider offers it.
    cy.contains('button[role="combobox"]', "gpt-4.1").should("be.visible");

    cy.contains('button[role="combobox"]', "gpt-4.1").click();
    cy.get("[cmdk-item]").contains("gpt-4o").click();
    cy.contains('button[role="combobox"]', "gpt-4o").should("be.visible");
  });

  it("warns the user when the provider returns an error instead of models", () => {
    apiMocks.setupErrorIntercept(
      "**/api/v1/ppt/openai/models/available",
      500,
      "Internal Server Error",
      "openaiModelsError"
    );

    cy.mount(
      <ConfigHarness
        initialConfig={{ LLM: "openai", OPENAI_API_KEY: "sk-broken-key" }}
      />
    );

    cy.contains("button", "모델 확인").click();

    cy.contains(
      "모델을 찾을 수 없습니다. 제공자 인증 정보가 올바른지, 선택한 제공자에 연결할 수 있는지 확인하세요."
    ).should("be.visible");
    // The lookup stays available so the user can retry after fixing the key.
    cy.contains("button", "모델 확인").should("be.visible");
    cy.contains("label", "OpenAI 모델 선택").should("not.exist");
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

    cy.mount(<ConfigHarness initialConfig={{ LLM: "ollama" }} />);

    cy.wait("@ollamaModels");

    cy.contains("label", "지원되는 모델 선택").should("be.visible");
    cy.contains('button[role="combobox"]', "llama3.2:3b (2.0 GB)").should(
      "be.visible"
    );

    cy.contains("button", "Ollama URL 사용").click();
    cy.contains("label", "Ollama URL").should("be.visible");
    cy.get('input[placeholder="http://localhost:11434"]').should(
      "have.value",
      "http://localhost:11434"
    );
    cy.contains("button", "기본 Ollama URL 사용").should("be.visible");
  });
});
