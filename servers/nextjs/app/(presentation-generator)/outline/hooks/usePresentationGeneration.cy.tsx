import React from "react";
import { mount } from "cypress/react";
import { NextIntlClientProvider } from "next-intl";
import { Provider } from "react-redux";
import { AppRouterContext } from "next/dist/shared/lib/app-router-context.shared-runtime";

import koMessages from "@/i18n/messages/ko.json";
import { setPptGenUploadState } from "@/store/slices/presentationGenUpload";
import { store } from "@/store/store";

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
    "outline-shell-test",
    [{ content: "# Selected outline" }],
  );
  return <button onClick={() => void handleSubmit()}>Generate</button>;
}

describe("usePresentationGeneration authored-only generation", () => {
  beforeEach(() => {
    cy.window().then((window) => window.localStorage.clear());
    store.dispatch(
      setPptGenUploadState({
        authoredStyle: "strategic-navy",
        authoredVisionQa: true,
        companyStandard: {
          masterId: "master:company-standard",
          revision: 3,
          digest: `sha256:${"a".repeat(64)}`,
          name: "회사 표준",
          hasLogo: true,
          primaryFont: "Noto Sans KR",
          layoutCount: 4,
        },
        config: null,
      }),
    );
  });

  it("starts only the authored driver with independent style and company pins", () => {
    cy.stub(router, "replace").as("replace");
    cy.stub(PresentationGenerationApi, "generateAuthoredAsync")
      .resolves({ id: "task-authored", status: "running" } as any)
      .as("generateAuthored");
    cy.spy(PresentationGenerationApi, "generateStandardAsync").as("legacyGenerate");
    cy.spy(PresentationGenerationApi, "generateTemplateV2Async").as("templateV2Generate");

    mount(
      <NextIntlClientProvider locale="ko" messages={koMessages} timeZone="UTC">
        <AppRouterContext.Provider value={router as any}>
          <Provider store={store}>
            <GenerationHarness />
          </Provider>
        </AppRouterContext.Provider>
      </NextIntlClientProvider>,
    );

    cy.contains("button", "Generate").click();
    cy.get("@generateAuthored").should(
      "have.been.calledOnceWith",
      Cypress.sinon.match({
        presentation_id: "outline-shell-test",
        authored_style: "strategic-navy",
        vision_qa: true,
        masterPin: {
          masterId: "master:company-standard",
          revision: 3,
          digest: `sha256:${"a".repeat(64)}`,
        },
      }),
    );
    cy.get("@legacyGenerate").should("not.have.been.called");
    cy.get("@templateV2Generate").should("not.have.been.called");
  });
});
