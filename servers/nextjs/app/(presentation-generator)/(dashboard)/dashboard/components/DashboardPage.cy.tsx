import React from "react";
import { Provider } from "react-redux";
import { AppRouterContext } from "next/dist/shared/lib/app-router-context.shared-runtime";

import { createMockRouter, ApiMockManager, testViewports } from "@/cypress/support/testHelpers";
import { makeStore } from "@/store/store";
import { DashboardApi } from "@/app/(presentation-generator)/services/api/dashboard";
import DashboardPage from "./DashboardPage";

// The cards now mount for real, so the page needs its Redux store: the slide
// stage dispatches through it. The router and viewport come from the shared
// helpers so this spec stays in step with the rest of the suite.
function mountDashboard() {
  cy.mount(
    <AppRouterContext.Provider value={createMockRouter() as never}>
      <Provider store={makeStore()}>
        <DashboardPage />
      </Provider>
    </AppRouterContext.Provider>
  );
}

/**
 * An "authored" deck — its first slide renders as a single cached <img>, the
 * cheapest stage the dashboard has. Anything the deferral leaves mounted here is
 * a floor, not a best case.
 */
function createDeck(index: number) {
  const updatedAt = new Date(Date.UTC(2026, 0, 1) - index * 1000).toISOString();
  return {
    id: `deck-${index}`,
    title: `테스트 발표자료 ${index}`,
    created_at: updatedAt,
    updated_at: updatedAt,
    theme: null,
    slides: [
      {
        id: `deck-${index}-slide-0`,
        index: 0,
        layout: "authored",
        layout_group: "authored",
        content: { __authored__: true, image: "/card_bg.svg" },
        properties: {},
      },
    ],
  };
}

function pageOf(items: ReturnType<typeof createDeck>[], total = items.length) {
  return {
    items,
    total,
    page: 1,
    page_size: 24,
  };
}

const DECK_COUNT = 60;

describe("DashboardPage", () => {
  let apiMocks: ApiMockManager;

  beforeEach(() => {
    apiMocks = new ApiMockManager();
    cy.viewport(testViewports.desktop.width, testViewports.desktop.height);
  });

  afterEach(() => {
    apiMocks.cleanup();
  });

  it("shows a recoverable error (not the empty state) when loading fails", () => {
    // A real fetch failure must NOT be rendered as "no presentations yet" —
    // that reads as account-wide data loss. The grid's retry UI should show.
    apiMocks.setupErrorIntercept(
      "**/api/v1/ppt/presentation/all*",
      500,
      "Internal Server Error",
      "presentationsFail"
    );

    mountDashboard();

    // Wait for async chart rendering to complete if present
    cy.waitForChartsToRender();

    cy.contains("발표자료를 불러오지 못했습니다").should("be.visible");
    cy.contains("button", "재시도").should("be.visible");
    cy.contains("아직 발표자료가 없습니다").should("not.exist");
  });

  it("shows the empty state when there are genuinely no presentations", () => {
    apiMocks.setupGetIntercept(
      "**/api/v1/ppt/presentation/all*",
      pageOf([]),
      "presentationsEmpty"
    );

    mountDashboard();

    // Wait for async chart rendering to complete if present
    cy.waitForChartsToRender();

    cy.contains("아직 발표자료가 없습니다").should("be.visible");
    cy.contains("button", "재시도").should("not.exist");
  });

  it("renders every card frame but only mounts slide stages near the viewport", () => {
    // The grid used to mount one full 1280x720 slide stage per deck, all at
    // once. Cards must still all exist (layout, title, date, Ctrl+F), but the
    // expensive stage may only be built for cards the user can nearly see.
    cy.stub(DashboardApi, "getPresentations").resolves(
      pageOf(Array.from({ length: DECK_COUNT }, (_, index) => createDeck(index)))
    );

    mountDashboard();

    // Every card frame is present immediately — nothing is virtualised away.
    cy.get('[data-testid="deck-thumbnail"]').should("have.length", DECK_COUNT);
    cy.contains(`테스트 발표자료 ${DECK_COUNT - 1}`).should("exist");

    // The top of the grid renders its stage...
    cy.get('[data-testid="deck-thumbnail"]')
      .first()
      .should("have.attr", "data-thumbnail-state", "rendered");

    // ...the tail of the grid does not, and mounts no stage DOM at all.
    cy.get('[data-testid="deck-thumbnail"]')
      .last()
      .should("have.attr", "data-thumbnail-state", "deferred")
      .find('[data-testid="slide-content"]')
      .should("not.exist");

    cy.get('[data-testid="slide-content"]').should(
      "have.length.lessThan",
      DECK_COUNT
    );
  });

  it("reserves the mounted stage's exact box, so materialising a card cannot reflow the grid", () => {
    cy.stub(DashboardApi, "getPresentations").resolves(
      pageOf(Array.from({ length: DECK_COUNT }, (_, index) => createDeck(index)))
    );

    mountDashboard();

    // Wait for SlideScale's ResizeObserver to have measured the container.
    cy.get('[data-testid="deck-thumbnail"]')
      .first()
      .find('[data-testid="slide-content"]')
      .should(($stage) => {
        expect($stage[0].getBoundingClientRect().height).to.be.greaterThan(50);
      });

    // A mounted stage is exactly as tall as the reserved box. (Not as *wide*:
    // SlideScale deliberately over-scales by a 20px bleed and lets the card clip
    // it, so only height drives the card's layout.) If SlideScale's fit math and
    // SLIDE_SCALE_RESERVED_HEIGHT_CSS ever drift apart, cards will jump as they
    // materialise — this is what catches it.
    cy.get('[data-testid="deck-thumbnail"]')
      .first()
      .then(($box) => {
        const reserved = $box[0].getBoundingClientRect();
        const stage = $box[0]
          .querySelector('[data-testid="slide-content"]')!
          .getBoundingClientRect();

        expect(stage.height).to.be.closeTo(reserved.height, 1);
      });

    // Scrolling to a deferred card materialises it without changing its box.
    cy.get('[data-testid="deck-thumbnail"]')
      .last()
      .then(($box) => {
        const before = $box[0].getBoundingClientRect().height;

        cy.wrap($box).scrollIntoView();
        cy.wrap($box).should(
          "have.attr",
          "data-thumbnail-state",
          "rendered"
        );
        cy.wrap($box).should(($after) => {
          expect($after[0].getBoundingClientRect().height).to.be.closeTo(
            before,
            1
          );
        });
      });
  });

  it("sends debounced search, sort, and pagination to the server", () => {
    const getPresentations = cy
      .stub(DashboardApi, "getPresentations")
      .callsFake(async (options) => {
        if (options.page === 2) {
          return { ...pageOf([createDeck(24)], 25), page: 2 };
        }
        if (options.q) {
          return pageOf([createDeck(7)]);
        }
        return pageOf(Array.from({ length: 24 }, (_, index) => createDeck(index)), 25);
      });

    mountDashboard();

    cy.contains("25개").should("be.visible");
    cy.get('[data-testid="dashboard-presentation-search"]').type("분기 계획");
    cy.wait(350).then(() => {
      expect(getPresentations.lastCall.args[0]).to.deep.include({
        q: "분기 계획",
        page: 1,
        pageSize: 24,
        sort: "desc",
      });
    });

    cy.get('[aria-label="정렬 순서 전환"]').click();
    cy.then(() => {
      expect(getPresentations.lastCall.args[0]).to.deep.include({
        q: "분기 계획",
        page: 1,
        sort: "asc",
      });
    });

    cy.get('[data-testid="dashboard-presentation-search"]').clear();
    cy.wait(350);
    cy.get('[aria-label="다음 페이지"]').click();
    cy.then(() => {
      expect(getPresentations.lastCall.args[0]).to.deep.include({
        page: 2,
        sort: "asc",
      });
    });
  });

  it("shows a search-specific empty state", () => {
    cy.stub(DashboardApi, "getPresentations").callsFake(async (options) =>
      options.q ? pageOf([]) : pageOf([createDeck(0)]),
    );

    mountDashboard();
    cy.get('[data-testid="dashboard-presentation-search"]').type("없는 자료");
    cy.wait(350);
    cy.contains("검색 결과가 없습니다.").should("be.visible");
    cy.contains("아직 발표자료가 없습니다.").should("not.exist");
  });
});
