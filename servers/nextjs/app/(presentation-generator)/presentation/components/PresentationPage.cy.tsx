import React from "react";
import { Provider } from "react-redux";
import { AppRouterContext } from "next/dist/shared/lib/app-router-context.shared-runtime";
import {
  PathnameContext,
  SearchParamsContext,
} from "next/dist/shared/lib/hooks-client-context.shared-runtime";

import { makeStore } from "@/store/store";
import { createBlankPresentationSlide } from "../../_shared/blank-slide";
import {
  ApiMockManager,
  createMockRouter,
  testViewports,
} from "@/cypress/support/testHelpers";
import PresentationPage from "./PresentationPage";

const presentationId = "deck-1";
const presentationUrl = `**/api/v1/ppt/presentation/${presentationId}`;

const buildDeck = (slideCount: number) => ({
  id: presentationId,
  title: "테스트 발표자료",
  language: "ko",
  layout: { name: "blank", ordered: true, slides: [] },
  n_slides: slideCount,
  theme: null,
  slides: Array.from({ length: slideCount }, (_, index) =>
    createBlankPresentationSlide({
      id: `slide-${index + 1}`,
      index,
      presentationId,
      templateId: "blank",
      isTemplateV2: true,
    }),
  ),
});

describe("<PresentationPage />", () => {
  let apiMocks: ApiMockManager;
  let router: ReturnType<typeof createMockRouter>;

  const mountPage = () => {
    router = createMockRouter();
    cy.mount(
      <AppRouterContext.Provider value={router as never}>
        <PathnameContext.Provider value="/presentation">
          <SearchParamsContext.Provider
            value={new URLSearchParams(`id=${presentationId}`) as never}
          >
            <Provider store={makeStore()}>
              <PresentationPage presentation_id={presentationId} />
            </Provider>
          </SearchParamsContext.Provider>
        </PathnameContext.Provider>
      </AppRouterContext.Provider>,
    );
  };

  beforeEach(() => {
    apiMocks = new ApiMockManager();
    cy.viewport(testViewports.desktop.width, testViewports.desktop.height);

    // Side panels mounted by the editor shell load their own data; keep them
    // deterministic so the assertions below only describe PresentationPage.
    apiMocks.setupGetIntercept("**/api/v1/ppt/themes/all", [] as never, "themes");
    apiMocks.setupGetIntercept(
      "**/api/v1/ppt/presentation/*/generation-info",
      { prompt: "", attachment_file_names: [], settings: {} },
      "generationInfo",
    );
    apiMocks.setupGetIntercept(
      "**/api/v1/ppt/chat/conversations*",
      [] as never,
      "conversations",
    );
  });

  afterEach(() => {
    apiMocks.cleanup();
  });

  it("renders the loaded deck once the presentation request resolves", () => {
    apiMocks.setupGetIntercept(presentationUrl, buildDeck(2), "loadDeck");

    mountPage();

    cy.wait("@loadDeck");
    cy.get("#presentation-editor-title").should("have.text", "테스트 발표자료");
    cy.get("#slide-0").should("exist");
    cy.get("#slide-1").should("exist");
    cy.get('[data-testid="slides-scroll-container"]').should("be.visible");
  });

  it("offers a recoverable empty state and reloads the deck on demand", () => {
    let requestCount = 0;
    apiMocks.setupGetIntercept(
      presentationUrl,
      () => {
        requestCount += 1;
        return buildDeck(requestCount === 1 ? 0 : 1);
      },
      "loadDeck",
    );

    mountPage();

    cy.wait("@loadDeck");
    cy.contains("슬라이드가 없습니다").should("be.visible");
    cy.contains(
      "이 발표자료에는 아직 생성된 슬라이드가 없습니다. 다시 불러오거나 상단의 재생성 버튼으로 슬라이드를 만들 수 있습니다.",
    ).should("be.visible");

    cy.contains("button", "다시 불러오기").click();

    cy.wait("@loadDeck");
    cy.contains("슬라이드가 없습니다").should("not.exist");
    cy.get("#slide-0").should("exist");
  });

  it("navigates to the dashboard from the empty state", () => {
    apiMocks.setupGetIntercept(presentationUrl, buildDeck(0), "loadDeck");

    mountPage();

    cy.wait("@loadDeck");
    cy.contains("button", "대시보드로 이동").click();

    cy.wrap(null).should(() => {
      expect(router.push).to.have.been.calledWith("/dashboard");
    });
  });

  it("shows the failure state and routes back to upload when loading fails", () => {
    apiMocks.setupErrorIntercept(presentationUrl, 500, "boom", "loadDeckError");

    mountPage();

    cy.wait("@loadDeckError");
    cy.contains("문제가 발생했습니다").should("be.visible");
    cy.contains("발표자료를 불러오지 못했습니다. 다시 시도해 주세요.").should(
      "be.visible",
    );
    cy.contains("button", "페이지 새로고침").should("be.visible");

    cy.contains("button", "업로드로 이동").click();

    cy.wrap(null).should(() => {
      expect(router.push).to.have.been.calledWith("/upload");
    });
  });
});
