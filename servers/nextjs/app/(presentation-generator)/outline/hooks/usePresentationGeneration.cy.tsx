import React from "react";
import { mount } from "cypress/react";
import { Provider } from "react-redux";
import { AppRouterContext } from "next/dist/shared/lib/app-router-context.shared-runtime";

import { setPptGenUploadState } from "@/store/slices/presentationGenUpload";
import { store } from "@/store/store";

import { AUTHORED_TEMPLATE_ID } from "../components/TemplateSelection";
import { PresentationGenerationApi } from "../../services/api/presentation-generation";
import { usePresentationGeneration } from "./usePresentationGeneration";

const router = {
  push: () => undefined,
  back: () => undefined,
  forward: () => undefined,
  refresh: () => undefined,
  replace: () => undefined,
  prefetch: () => Promise.resolve(),
};

function GenerationHarness() {
  const { handleSubmit } = usePresentationGeneration(
    null,
    [{ content: "# 선택한 스타일 요청" }],
    AUTHORED_TEMPLATE_ID,
    () => undefined
  );

  return (
    <button type="button" onClick={() => void handleSubmit()}>
      생성
    </button>
  );
}

describe("usePresentationGeneration authored request", () => {
  it("passes the Redux-authored style and vision-QA setting to generation", () => {
    store.dispatch(
      setPptGenUploadState({
        authoredStyle: "strategic-navy",
        authoredVisionQa: true,
        config: null,
      })
    );
    cy.stub(PresentationGenerationApi, "generateAuthoredAsync")
      .resolves({} as any)
      .as("generateAuthored");

    mount(
      <AppRouterContext.Provider value={router as any}>
        <Provider store={store}>
          <GenerationHarness />
        </Provider>
      </AppRouterContext.Provider>
    );

    cy.contains("button", "생성").click();
    cy.get("@generateAuthored").should("have.been.calledOnceWith", {
      content: "# 선택한 스타일 요청",
      slides_markdown: ["# 선택한 스타일 요청"],
      language: null,
      vision_qa: true,
      authored_style: "strategic-navy",
    });
  });
});
