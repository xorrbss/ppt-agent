import React from "react";
import { AppRouterContext } from "next/dist/shared/lib/app-router-context.shared-runtime";

import DashboardSidebar from "@/app/(presentation-generator)/(dashboard)/Components/DashboardSidebar";
import { createMockRouter } from "@/cypress/support/testHelpers";
import ModeSelectStep from "./ModeSelectStep";
import OnBoardingHeader from "./OnBoardingHeader";
import OnBoardingSlidebar from "./OnBoardingSlidebar";

function mountOnboarding() {
  cy.mount(
    <div className="relative flex min-h-screen w-full min-w-0 flex-col overflow-x-clip md:flex-row">
      <OnBoardingSlidebar step={1} />
      <main
        data-testid="onboarding-main"
        className="relative mx-auto w-full min-w-0 max-w-[1440px] px-4 pb-24 sm:px-8 md:pl-20 md:pr-8"
      >
        <OnBoardingHeader currentStep={1} setStep={() => undefined} />
        <ModeSelectStep
          selectedMode="presenton"
          setStep={() => undefined}
          setSelectedMode={() => undefined}
        />
      </main>
    </div>,
  );
}

function mountDashboardSidebar() {
  cy.mount(
    <AppRouterContext.Provider value={createMockRouter() as never}>
      <div className="flex min-h-screen min-w-0 bg-white pb-20 sm:pb-0 sm:pr-4">
        <DashboardSidebar />
        <main className="min-w-0 w-full">Dashboard content</main>
      </div>
    </AppRouterContext.Provider>,
  );
}

describe("responsive onboarding and dashboard navigation", () => {
  it("keeps onboarding inside a 390px viewport", () => {
    cy.viewport(390, 844);
    mountOnboarding();

    cy.get("[data-testid=onboarding-sidebar]").should(($sidebar) => {
      expect($sidebar[0].getBoundingClientRect().width).to.be.closeTo(
        390,
        1,
      );
    });
    cy.get("[data-testid=onboarding-main]").should(($main) => {
      expect($main[0].getBoundingClientRect().width).to.be.at.most(
        390,
      );
      expect(document.documentElement.scrollWidth).to.be.at.most(
        390,
      );
    });
  });

  it("uses a full-width bottom dashboard nav on mobile without wrapping labels", () => {
    cy.viewport(390, 844);
    mountDashboardSidebar();

    cy.get("[data-testid=dashboard-sidebar]").should(($sidebar) => {
      const rect = $sidebar[0].getBoundingClientRect();
      expect(rect.width).to.be.closeTo(390, 1);
      expect(rect.bottom).to.be.closeTo(844, 1);
    });
    cy.get("[data-testid=dashboard-sidebar] span").each(($label) => {
      expect($label[0].getClientRects()).to.have.length(1);
      expect($label[0].getBoundingClientRect().height).to.be.lessThan(20);
    });
    cy.document().then((document) => {
      expect(document.documentElement.scrollWidth).to.be.at.most(
        390,
      );
    });
  });

  it("preserves the 115px desktop dashboard rail", () => {
    cy.viewport(1280, 720);
    mountDashboardSidebar();

    cy.get("[data-testid=dashboard-sidebar]").should(($sidebar) => {
      const rect = $sidebar[0].getBoundingClientRect();
      expect(rect.width).to.be.closeTo(115, 1);
      expect(rect.height).to.be.closeTo(720, 1);
    });
  });
});
