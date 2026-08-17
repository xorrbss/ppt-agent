import React from "react";
import { Provider } from "react-redux";
import { NextIntlClientProvider } from "next-intl";
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
import enMessages from "@/i18n/messages/en.json";

type AxeRuntime = typeof import("axe-core");

const presentationId = "deck-1";
const presentationUrl = `**/api/v1/ppt/presentation/${presentationId}`;

const standardRuntime = {
  profile: {
    schema_version: 1,
    driver_id: "standard",
    content_model: "legacy-elements",
    generation_strategy: "standard",
    style_source: "builtin",
    generation_mode: "standard",
  },
  capabilities: {
    schema_version: 1,
    driver_id: "standard",
    renderer_adapter_id: "legacy-elements",
    editor_adapter_id: "legacy-elements",
    editor: {
      element_text_edit: true,
      element_geometry_edit: true,
      chart_edit: true,
      theme_apply: true,
      template_apply: true,
      slide_image_regenerate: false,
      smart_selection: false,
    },
    agent_tool_capability: "standard",
    export_adapter_id: "legacy-general",
    export_editable_pptx: true,
  },
} as const;

const smartRuntime = {
  profile: {
    schema_version: 1,
    driver_id: "smart",
    content_model: "smart-html",
    generation_strategy: "smart",
    style_source: "community",
    generation_mode: "smart",
  },
  capabilities: {
    ...standardRuntime.capabilities,
    driver_id: "smart",
    renderer_adapter_id: "smart-html",
    editor_adapter_id: "smart-html",
    editor: {
      ...standardRuntime.capabilities.editor,
      chart_edit: false,
      theme_apply: false,
      template_apply: false,
      smart_selection: true,
    },
    agent_tool_capability: "smart",
  },
} as const;

const buildDeck = (slideCount: number) => ({
  id: presentationId,
  title: "테스트 발표자료",
  language: "ko",
  layout: { name: "blank", ordered: true, slides: [] },
  n_slides: slideCount,
  revision: 1,
  lifecycle_status: "published",
  theme: null,
  ...standardRuntime,
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

const buildSmartDeck = () => ({
  ...buildDeck(0),
  ...smartRuntime,
  mode: "authored",
  slides: [
    {
      id: "smart-slide-1",
      presentation: presentationId,
      index: 0,
      layout_group: "authored",
      layout: "authored:SMART_SMOKE",
      content: { __authored__: true },
      html_content:
        "<!doctype html><html><body><main><p>Choose this Smart paragraph</p></main></body></html>",
      properties: {},
    },
  ],
  n_slides: 1,
});

function assertNoSevereAxeViolations() {
  cy.readFile("node_modules/axe-core/axe.min.js", "utf8")
    .then((source: string) =>
      cy.window().then((window) => {
        window.Function(source)();
        const pageAxe = (window as typeof window & { axe?: AxeRuntime }).axe;
        if (!pageAxe) throw new Error("axe-core did not attach to the component window");
        const drawer = window.document.querySelector(
          '[data-testid="mobile-thumbnail-drawer"]',
        );
        if (!drawer) throw new Error("Thumbnail drawer was not mounted for the axe audit");
        return pageAxe.run(drawer);
      }),
    )
    .then((results) => {
      const severe = results.violations.filter(
        ({ impact }) => impact === "serious" || impact === "critical",
      );
      const summary = severe
        .map(
          (violation) =>
            `${violation.id}: ${violation.nodes
              .map((node) => node.target.join(" > "))
              .join(", ")}`,
        )
        .join("\n");
      expect(severe, summary).to.have.length(0);
    });
}

describe("<PresentationPage />", () => {
  let apiMocks: ApiMockManager;
  let router: ReturnType<typeof createMockRouter>;

  const mountPage = (workspaceStaging = false) => {
    router = createMockRouter();
    cy.mount(
      <NextIntlClientProvider locale="en" messages={enMessages} timeZone="UTC">
        <AppRouterContext.Provider value={router as never}>
          <PathnameContext.Provider value="/presentation">
            <SearchParamsContext.Provider
              value={new URLSearchParams(
                `id=${presentationId}${workspaceStaging ? "&workspace_staging=template" : ""}`,
              ) as never}
            >
              <Provider store={makeStore()}>
                <PresentationPage
                  presentation_id={presentationId}
                  workspaceStaging={workspaceStaging}
                />
              </Provider>
            </SearchParamsContext.Provider>
          </PathnameContext.Provider>
        </AppRouterContext.Provider>
      </NextIntlClientProvider>,
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
    apiMocks.setupGetIntercept("**/api/v1/ppt/workspace/capabilities*", {
      supported: true,
      enabled: true,
      timeline: true,
      commands: true,
      events: true,
    }, "workspaceCapability");
    apiMocks.setupPostIntercept("**/api/v1/ppt/conversations", {
      id: "11111111-1111-4111-8111-111111111111",
      presentation_id: presentationId,
      revision: 1,
    }, "conversation");
    apiMocks.setupGetIntercept(
      "**/api/v1/ppt/conversations/11111111-1111-4111-8111-111111111111/timeline*",
      { items: [], revision: 1, event_high_watermark: "0" },
      "conversationTimeline",
    );
    cy.intercept(
      "GET",
      "**/api/v1/ppt/conversations/11111111-1111-4111-8111-111111111111/events*",
      { statusCode: 503, body: { error: { safe_message: "stream unavailable" } } },
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
    cy.get('main[data-render-artifact-contract="presenton.render-artifact/v1"]')
      .should("have.attr", "data-render-artifact-target", "preview")
      .and("have.attr", "data-editor-adapter", "legacy-elements")
      .and("have.attr", "data-assistant-adapter", "canonical-workspace");
  });

  it("stops workspace staging when the design operation has failed", () => {
    apiMocks.setupGetIntercept(presentationUrl, buildDeck(0), "loadStagingDeck");
    apiMocks.setupGetIntercept(
      `**/api/v1/ppt/workspace/presentations/${presentationId}/timeline*`,
      {
        schema_version: 1,
        items: [
          {
            schema_version: 1,
            anchor_type: "operation",
            id: "operation-failed",
            operation_id: "operation-failed",
            position: 1,
            operation_type: "design.apply",
            title: "디자인 변경",
            status: "dead_letter",
            safe_message: "디자인 변경 작업을 완료하지 못했습니다.",
            events: [],
          },
        ],
        revision: 0,
        has_more: false,
      },
      "stagingTimeline",
    );

    mountPage(true);

    cy.wait("@loadStagingDeck");
    cy.wait("@stagingTimeline");
    cy.get('[data-testid="workspace-staging-failure"]')
      .should("be.visible")
      .and("contain.text", "디자인 변경에 실패했습니다");
    cy.get('[data-testid="workspace-staging-loading"]').should("not.exist");
  });

  it("enables Smart HTML selection and forwards the selected element to canonical chat", () => {
    cy.window().then((window) => {
      (window as Window & { tailwind?: unknown }).tailwind = {};
    });
    apiMocks.setupGetIntercept(presentationUrl, buildSmartDeck(), "loadSmartDeck");

    mountPage();

    cy.wait("@loadSmartDeck");
    cy.contains("Choose this Smart paragraph").click();
    cy.get('[data-testid="smart-selection-badge"]')
      .should("be.visible")
      .and("contain.text", "Smart selection");
    cy.contains("Selected for AI").should("exist");
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
    cy.get('[role="alert"]')
      .should("be.visible")
      .within(() => {
        cy.get("button").should("have.length", 2).eq(1).click();
      });

    cy.wrap(null).should(() => {
      expect(router.push).to.have.been.calledWith("/upload");
    });
  });

  it("uses a focus-safe thumbnail drawer on mobile and returns focus on Escape", () => {
    cy.viewport(testViewports.mobile.width, testViewports.mobile.height);
    apiMocks.setupGetIntercept(presentationUrl, buildDeck(3), "loadDeck");

    mountPage();
    cy.wait("@loadDeck");

    cy.get('[data-thumbnail-display="rail"]').should("not.be.visible");
    cy.get('[data-testid="mobile-thumbnail-drawer-trigger"]')
      .should("be.visible")
      .focus()
      .click()
      .should("have.attr", "aria-expanded", "true");
    cy.get('[data-testid="mobile-thumbnail-drawer"]')
      .should("be.visible")
      .and("have.attr", "role", "dialog")
      .should(($dialog) => {
        expect($dialog[0].contains($dialog[0].ownerDocument.activeElement)).to.eq(true);
      });
    cy.get('[data-testid="mobile-thumbnail-drawer"] > button').first().should(
      ($button) => {
        const bounds = $button[0].getBoundingClientRect();
        expect(bounds.width).to.be.at.least(44);
        expect(bounds.height).to.be.at.least(44);
      },
    );
    assertNoSevereAxeViolations();

    cy.press(Cypress.Keyboard.Keys.ESC);
    cy.get('[data-testid="mobile-thumbnail-drawer"]').should("not.exist");
    cy.get('[data-testid="mobile-thumbnail-drawer-trigger"]').should("be.focused");

    cy.get('[data-testid="mobile-thumbnail-drawer-trigger"]').click();
    cy.get('[data-thumbnail-display="drawer"] [data-testid="slide-thumbnail-select-1"]').click();
    cy.get('[data-testid="mobile-thumbnail-drawer"]').should("not.exist");
    cy.get("#presentation-slides-pane").should("be.visible");

    cy.get('[data-testid="mobile-thumbnail-drawer-trigger"]').click();
    cy.get('[data-thumbnail-display="drawer"] [data-testid="slide-thumbnail-select-1"]')
      .should("have.attr", "aria-current", "page");
  });

  it("keeps the desktop thumbnail rail and hides the mobile drawer trigger", () => {
    apiMocks.setupGetIntercept(presentationUrl, buildDeck(2), "loadDeck");

    mountPage();
    cy.wait("@loadDeck");

    cy.get('[data-thumbnail-display="rail"]').should("be.visible");
    cy.get('[data-testid="mobile-thumbnail-drawer-trigger"]').should("not.be.visible");
  });
});
