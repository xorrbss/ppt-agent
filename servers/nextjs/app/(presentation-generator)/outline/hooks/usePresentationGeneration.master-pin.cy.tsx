import React from "react";
import { mount } from "cypress/react";
import { NextIntlClientProvider } from "next-intl";
import { AppRouterContext } from "next/dist/shared/lib/app-router-context.shared-runtime";
import { Provider } from "react-redux";

import koMessages from "@/i18n/messages/ko.json";
import {
  setOutlines,
  setPresentationId,
} from "@/store/slices/presentationGeneration";
import { setPptGenUploadState } from "@/store/slices/presentationGenUpload";
import { makeStore } from "@/store/store";

import { PresentationGenerationApi } from "../../services/api/presentation-generation";
import { usePresentationGeneration } from "./usePresentationGeneration";

const STORAGE_KEY = "presenton:durable-generation-tasks:v1";
const PRESENTATION_ID = "presentation-auto-master-pin";
const IDEMPOTENCY_KEY = "ppt-generation-upload-staged";
const CLICK_PIN = {
  masterId: "master:ibiz-blue",
  revision: 4,
  digest: `sha256:${"a".repeat(64)}` as `sha256:${string}`,
};
const LATER_REDUX_PIN = {
  masterId: "master:ibiz-blue",
  revision: 5,
  digest: `sha256:${"b".repeat(64)}` as `sha256:${string}`,
  name: "IBIZ 제안서 블루",
  hasLogo: true,
  primaryFont: "Noto Sans KR",
  layoutCount: 4,
};

function GenerationHarness() {
  const { handleSubmit } = usePresentationGeneration(
    PRESENTATION_ID,
    [{ content: "# 첫 슬라이드" }],
    () => undefined,
  );
  return <button onClick={() => void handleSubmit()}>generate</button>;
}

describe("usePresentationGeneration durable Upload handoff", () => {
  it("uses the master pin captured at the Upload click even if Redux later changes", () => {
    const pageStore = makeStore();
    pageStore.dispatch(setPresentationId(PRESENTATION_ID));
    pageStore.dispatch(setOutlines([{ content: "# 첫 슬라이드" }]));
    pageStore.dispatch(
      setPptGenUploadState({
        companyStandard: LATER_REDUX_PIN,
        authoredStyle: "default",
        authoredVisionQa: false,
      }),
    );
    cy.window().then((window) => {
      window.localStorage.setItem(
        STORAGE_KEY,
        JSON.stringify({
          [IDEMPOTENCY_KEY]: {
            version: 1,
            idempotencyKey: IDEMPOTENCY_KEY,
            taskId: null,
            mode: "authored",
            sourcePresentationId: PRESENTATION_ID,
            status: "starting",
            createdAt: Date.now(),
            deadlineAt: Date.now() + 30 * 60 * 1000,
            progressDurationSeconds: 120,
            authoredRequest: {
              language: "Korean",
              visionQa: false,
              authoredStyle: "default",
              masterPin: CLICK_PIN,
            },
          },
        }),
      );
    });

    const replace = cy.stub().as("replace");
    cy.stub(PresentationGenerationApi, "generateAuthoredAsync")
      .resolves({ id: "generation-task", status: "running" } as any)
      .as("generateAuthored");

    mount(
      <NextIntlClientProvider locale="ko" messages={koMessages} timeZone="Asia/Seoul">
        <AppRouterContext.Provider
          value={{
            push: () => undefined,
            back: () => undefined,
            forward: () => undefined,
            refresh: () => undefined,
            replace,
            prefetch: () => Promise.resolve(),
          } as any}
        >
          <Provider store={pageStore}>
            <GenerationHarness />
          </Provider>
        </AppRouterContext.Provider>
      </NextIntlClientProvider>,
    );

    cy.contains("button", "generate").click();
    cy.get("@generateAuthored").should(
      "have.been.calledOnceWith",
      Cypress.sinon.match({
        idempotency_key: IDEMPOTENCY_KEY,
        masterPin: CLICK_PIN,
      }),
    );
    cy.get("@replace").should(
      "have.been.calledOnceWith",
      `/presentation?id=${PRESENTATION_ID}`,
    );
  });
});
