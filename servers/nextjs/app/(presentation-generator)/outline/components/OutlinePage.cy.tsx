import React from "react";
import { mount } from "cypress/react";
import { Provider } from "react-redux";
import { AppRouterContext } from "next/dist/shared/lib/app-router-context.shared-runtime";
import { NextIntlClientProvider } from "next-intl";

import {
  setOutlines,
  setPresentationId,
} from "@/store/slices/presentationGeneration";
import { setPptGenUploadState } from "@/store/slices/presentationGenUpload";
import { makeStore, store } from "@/store/store";

import { PresentationGenerationApi } from "../../services/api/presentation-generation";
import { DashboardApi } from "../../services/api/dashboard";
import OutlinePage from "./OutlinePage";
import koMessages from "@/i18n/messages/ko.json";

import "@/app/globals.css";

const DURABLE_TASK_STORAGE_KEY = "presenton:durable-generation-tasks:v1";
const SOURCE_PRESENTATION_ID = "outline-shell-resume-banner";
const RESUME_IDEMPOTENCY_KEY = "ppt-generation-resume-banner";

const router = {
  push: () => undefined,
  back: () => undefined,
  forward: () => undefined,
  refresh: () => undefined,
  replace: () => undefined,
  prefetch: () => Promise.resolve(),
};

const seedRetryableTask = (
  lastProgress?: Record<string, unknown>,
  taskExtras: Record<string, unknown> = {}
) => {
  cy.window().then((window) => {
    window.localStorage.setItem(
      DURABLE_TASK_STORAGE_KEY,
      JSON.stringify({
        [RESUME_IDEMPOTENCY_KEY]: {
          version: 1,
          idempotencyKey: RESUME_IDEMPOTENCY_KEY,
          taskId: "interrupted-authored-task",
          mode: "authored",
          sourcePresentationId: SOURCE_PRESENTATION_ID,
          status: "retryable-error",
          createdAt: Date.now(),
          deadlineAt: Date.now() + 30 * 60 * 1000,
          progressDurationSeconds: 120,
          ...(lastProgress ? { lastProgress } : {}),
          ...taskExtras,
        },
      })
    );
  });
};

const mountOutlinePage = (
  pageStore = store,
  initialPresentationId?: string,
) =>
  mount(
    <NextIntlClientProvider
      locale="ko"
      messages={koMessages}
      timeZone="Asia/Seoul"
    >
      <AppRouterContext.Provider value={router as any}>
        <Provider store={pageStore}>
          <OutlinePage
            auto={true}
            initialPresentationId={initialPresentationId}
          />
        </Provider>
      </AppRouterContext.Provider>
    </NextIntlClientProvider>
  );

describe("<OutlinePage /> resume banner", () => {
  beforeEach(() => {
    cy.viewport(1280, 900);
    cy.window().then((window) => window.localStorage.clear());
    store.dispatch(setPresentationId(SOURCE_PRESENTATION_ID));
    store.dispatch(setOutlines([{ content: "# Interrupted outline" }]));
    store.dispatch(
      setPptGenUploadState({
        authoredStyle: "default",
        authoredVisionQa: false,
        config: null,
      })
    );
  });

  it("offers resuming a stored retryable-error task and re-POSTs the same key", () => {
    seedRetryableTask({
      completed_slides: 46,
      total_slides: 90,
      current_slide: 47,
      current_stage: "render",
      finalizing: false,
    });
    cy.stub(PresentationGenerationApi, "generateAuthoredAsync")
      .resolves({ id: "resumed-authored-task", status: "running" } as any)
      .as("generateAuthored");
    cy.stub(PresentationGenerationApi, "getGenerationStatus").resolves({
      status: "running",
    } as any);

    mountOutlinePage();

    cy.get('[data-cy="resume-generation-banner"]')
      .should("be.visible")
      .and(
        "contain.text",
        "이전 생성이 슬라이드 46/90에서 중단되었습니다. 이어서 생성할 수 있습니다."
      );

    cy.contains("button", "이어서 생성").click();

    cy.get("@generateAuthored").should(
      "have.been.calledOnceWith",
      Cypress.sinon.match({ idempotency_key: RESUME_IDEMPOTENCY_KEY })
    );
    cy.get('[data-cy="resume-generation-banner"]').should("not.exist");
  });

  it("shows the banner without slide counts when no progress was stored", () => {
    seedRetryableTask();

    mountOutlinePage();

    cy.get('[data-cy="resume-generation-banner"]')
      .should("be.visible")
      .and(
        "contain.text",
        "이전 생성이 중단되었습니다. 이어서 생성할 수 있습니다."
      );
  });
});

describe("<OutlinePage /> reload recovery", () => {
  it("redirects an already-published auto re-entry to the presentation", () => {
    const publishedPresentationId = "already-published-auto-entry";
    const publishedStore = makeStore();
    publishedStore.dispatch(setPresentationId(publishedPresentationId));
    publishedStore.dispatch(setOutlines([{ content: "# Published outline" }]));
    publishedStore.dispatch(
      setPptGenUploadState({
        authoredStyle: "default",
        authoredVisionQa: false,
        companyStandard: {
          masterId: "master:published",
          revision: 1,
          digest: `sha256:${"b".repeat(64)}`,
          name: "게시 회사 표준",
          hasLogo: true,
          primaryFont: "Noto Sans KR",
          layoutCount: 4,
        },
        config: null,
      })
    );
    cy.window().then((window) => window.localStorage.clear());
    cy.stub(DashboardApi, "getPresentation")
      .resolves({
        id: publishedPresentationId,
        lifecycle_status: "published",
        n_slides: 1,
        outlines: { slides: [{ content: "# Published outline" }] },
        slides: [{ id: "published-slide" }],
      } as any)
      .as("getPresentation");
    cy.stub(PresentationGenerationApi, "generateAuthoredAsync")
      .as("generateAuthored");
    cy.stub(router, "replace").as("replace");

    mountOutlinePage(publishedStore, publishedPresentationId);

    cy.get("@getPresentation").should(
      "have.been.calledWith",
      publishedPresentationId
    );
    cy.get("@replace").should(
      "have.been.calledOnceWith",
      `/presentation?id=${publishedPresentationId}`
    );
    cy.get("@generateAuthored").should("not.have.been.called");
  });

  it("auto-submits a staged first generation when its outline already exists", () => {
    cy.viewport(1280, 900);
    cy.window().then((window) => window.localStorage.clear());
    seedRetryableTask(undefined, {
      taskId: null,
      status: "starting",
      authoredRequest: {
        language: "Korean",
        visionQa: false,
        authoredStyle: "default",
        masterPin: {
          masterId: "master:auto",
          revision: 1,
          digest: `sha256:${"a".repeat(64)}`,
        },
      },
    });
    const autoStore = makeStore();
    autoStore.dispatch(setPresentationId(SOURCE_PRESENTATION_ID));
    autoStore.dispatch(setOutlines([{ content: "# Ready outline" }]));
    autoStore.dispatch(
      setPptGenUploadState({
        authoredStyle: "default",
        authoredVisionQa: false,
        companyStandard: {
          masterId: "master:auto",
          revision: 1,
          digest: `sha256:${"a".repeat(64)}`,
          name: "자동 회사 표준",
          hasLogo: true,
          primaryFont: "Noto Sans KR",
          layoutCount: 4,
        },
        config: null,
      })
    );
    cy.stub(PresentationGenerationApi, "generateAuthoredAsync")
      .resolves({ id: "first-authored-task", status: "running" } as any)
      .as("generateAuthored");

    mountOutlinePage(autoStore);

    cy.get("@generateAuthored").should(
      "have.been.calledOnceWith",
      Cypress.sinon.match({
        idempotency_key: RESUME_IDEMPOTENCY_KEY,
        slides_markdown: ["# Ready outline"],
      })
    );
  });

  it("auto-submits a staged authored template without a master", () => {
    cy.window().then((window) => window.localStorage.clear());
    seedRetryableTask(undefined, {
      taskId: null,
      status: "starting",
      authoredRequest: {
        language: "Korean",
        visionQa: false,
        authoredStyle: "osb-smart-banking",
      },
    });
    const templateOnlyStore = makeStore();
    templateOnlyStore.dispatch(setPresentationId(SOURCE_PRESENTATION_ID));
    templateOnlyStore.dispatch(setOutlines([{ content: "# Template-only outline" }]));
    templateOnlyStore.dispatch(
      setPptGenUploadState({
        authoredStyle: "osb-smart-banking",
        authoredVisionQa: false,
        companyStandard: null,
        config: null,
      })
    );
    cy.stub(PresentationGenerationApi, "generateAuthoredAsync")
      .resolves({ id: "template-only-task", status: "running" } as any)
      .as("generateTemplateOnly");

    mountOutlinePage(templateOnlyStore);

    cy.get("@generateTemplateOnly").should(
      "have.been.calledOnceWith",
      Cypress.sinon.match({
        authored_style: "osb-smart-banking",
        masterPin: undefined,
        slides_markdown: ["# Template-only outline"],
      })
    );
  });

  it("restores the presentation identity carried by the outline URL", () => {
    const reloadStore = makeStore();
    reloadStore.dispatch(setOutlines([{ content: "# Route-restored outline" }]));

    mountOutlinePage(reloadStore, "presentation-from-route");

    cy.wrap(null).should(() => {
      expect(
        reloadStore.getState().presentationGeneration.presentation_id,
      ).to.equal("presentation-from-route");
    });
  });

  // A real reload rebuilds the Redux store from scratch, so nothing may be
  // dispatched before mount: the page must rehydrate the outline shell and
  // the resume banner from the stored durable task alone.
  it("surfaces the stored resumable task after a reload and resumes with the captured settings", () => {
    cy.viewport(1280, 900);
    cy.window().then((window) => window.localStorage.clear());
    seedRetryableTask(
      {
        completed_slides: 46,
        total_slides: 90,
        current_slide: 47,
        current_stage: "render",
        finalizing: false,
      },
      {
        authoredRequest: {
          language: "Korean",
          visionQa: true,
          authoredStyle: "vivid-purple",
        },
      }
    );
    // The outline shell persists server-side; the streaming hook restores the
    // outlines from it once the presentation id is rehydrated.
    cy.stub(DashboardApi, "getPresentation").resolves({
      outlines: { slides: [{ content: "# Interrupted outline" }] },
    } as any);
    cy.stub(PresentationGenerationApi, "generateAuthoredAsync")
      .resolves({ id: "resumed-authored-task", status: "running" } as any)
      .as("generateAuthored");
    cy.stub(PresentationGenerationApi, "getGenerationStatus").resolves({
      status: "running",
    } as any);

    mountOutlinePage(makeStore());

    cy.get('[data-cy="resume-generation-banner"]')
      .should("be.visible")
      .and(
        "contain.text",
        "이전 생성이 슬라이드 46/90에서 중단되었습니다. 이어서 생성할 수 있습니다."
      );
    // The rehydrated mount must never auto-fire generation (the auto-bridge is
    // for a compose-flow stream only); resuming stays an explicit user action.
    cy.get("@generateAuthored").should("not.have.been.called");

    cy.contains("button", "이어서 생성").click();

    // The re-POST must reuse the stored key AND the compose-time settings the
    // reload wiped from Redux — the server 409s a drifted same-key payload.
    cy.get("@generateAuthored").should(
      "have.been.calledOnceWith",
      Cypress.sinon.match({
        idempotency_key: RESUME_IDEMPOTENCY_KEY,
        slides_markdown: ["# Interrupted outline"],
        language: "Korean",
        vision_qa: true,
        authored_style: "vivid-purple",
      })
    );
  });
});
