import React from "react";
import { mount } from "cypress/react";
import { NextIntlClientProvider } from "next-intl";

import type { AuthoredStyleSummary } from "@/app/(presentation-generator)/services/api/authored";
import koMessages from "@/i18n/messages/ko.json";

import StyleSampleDialog from "./StyleSampleDialog";

const CATEGORY_LABELS = {
  general: "범용",
  business: "비즈니스",
  technology: "기술",
  research: "리서치",
  editorial: "에디토리얼",
  creative: "크리에이티브",
} as const;

function pixel(color: string): string {
  const svg = `<svg xmlns="http://www.w3.org/2000/svg" width="16" height="9"><rect width="16" height="9" fill="${color}"/></svg>`;
  return `data:image/svg+xml,${encodeURIComponent(svg)}`;
}

function style(
  id: string,
  titles: string[] | null,
  canvas = { width: 1280, height: 720 },
): AuthoredStyleSummary {
  return {
    id,
    name: `${id} name`,
    description: `${id} description`,
    category: "business",
    tags: ["tag-a"],
    use_cases: ["use-a"],
    supportedAspectRatios: ["16:9"],
    preview: { bg: "#FFFFFF", accent: "#2563EB", palette: ["#FFFFFF"], variant: "clean-light" },
    ...(titles
      ? {
          samples: {
            canvas,
            slides: titles.map((title, index) => ({
              index: index + 1,
              title,
              thumbUrl: pixel("#CCCCCC"),
              fullUrl: pixel("#333333"),
            })),
          },
        }
      : {}),
  };
}

const STYLES = [
  style("alpha", ["Cover", "Market", "Product"]),
  style("beta", ["Cover", "Market"], { width: 1280, height: 905 }),
  style("gamma", null),
];

function mountDialog(initialStyleId = "alpha", selectedStyleId?: string) {
  const onConfirm = cy.stub().as("confirm");
  const onClose = cy.stub().as("close");
  mount(
    <NextIntlClientProvider locale="ko" messages={koMessages} timeZone="UTC">
      <StyleSampleDialog
        styles={STYLES}
        initialStyleId={initialStyleId}
        selectedStyleId={selectedStyleId}
        categoryLabels={CATEGORY_LABELS}
        onConfirm={onConfirm}
        onClose={onClose}
      />
    </NextIntlClientProvider>,
  );
}

const dialog = () => cy.get('[data-testid="style-sample-dialog"]');
const counter = () => cy.get('[data-testid="style-sample-counter"]');
const stage = () => cy.get('[data-testid="style-sample-stage-image"]');

describe("StyleSampleDialog", () => {
  it("pages the stage with buttons, tiles and arrow keys", () => {
    mountDialog();

    cy.get('[data-testid="style-sample-title"]').should("have.text", "alpha name");
    cy.contains("전체 슬라이드 · 3").should("be.visible");
    cy.get('[data-testid^="style-sample-tile-"]').should("have.length", 3);
    cy.get('[data-testid="style-sample-tile-1"] img')
      .should("have.attr", "loading", "lazy")
      .and("have.attr", "alt", "1. Cover");
    counter().should("have.text", "1 / 3");
    stage().should("have.attr", "alt", "1. Cover");
    cy.get('[data-testid="style-sample-prev-slide"]').should("be.disabled");

    cy.get('[data-testid="style-sample-next-slide"]').click();
    counter().should("have.text", "2 / 3");
    stage().should("have.attr", "alt", "2. Market");

    dialog().trigger("keydown", { key: "ArrowRight" });
    counter().should("have.text", "3 / 3");
    cy.get('[data-testid="style-sample-next-slide"]').should("be.disabled");
    dialog().trigger("keydown", { key: "ArrowRight" });
    counter().should("have.text", "3 / 3");

    cy.get('[data-testid="style-sample-tile-1"]').click();
    counter().should("have.text", "1 / 3");
    cy.get('[data-testid="style-sample-tile-1"]').should("have.attr", "aria-current", "true");
    dialog().trigger("keydown", { key: "ArrowLeft" });
    counter().should("have.text", "1 / 3");
  });

  it("keeps the slide number across styles and clamps it to the shorter deck", () => {
    mountDialog();

    cy.get('[data-testid="style-sample-tile-2"]').click();
    dialog().trigger("keydown", { key: "ArrowRight", shiftKey: true });
    cy.get('[data-testid="style-sample-title"]').should("have.text", "beta name");
    counter().should("have.text", "2 / 2");
    stage().parent().should("have.attr", "style").and("match", /aspect-ratio: 1280 \/ 905/);

    dialog().trigger("keydown", { key: "ArrowLeft", shiftKey: true });
    cy.get('[data-testid="style-sample-title"]').should("have.text", "alpha name");
    counter().should("have.text", "2 / 3");

    cy.get('[data-testid="style-sample-tile-3"]').click();
    cy.get('[data-testid="style-sample-next-style"]').click();
    counter().should("have.text", "2 / 2");
    cy.get('[data-testid="style-sample-style-counter"]').should("have.text", "스타일 2 / 3");
  });

  it("shows the empty state for a style without samples", () => {
    mountDialog("gamma");

    cy.get('[data-testid="style-sample-empty"]').should("be.visible");
    cy.get('[data-testid="authored-style-preview-gamma"]').should("exist");
    cy.contains("이 스타일의 샘플 슬라이드를 준비하고 있습니다.").should("be.visible");
    cy.get('[data-testid^="style-sample-tile-"]').should("not.exist");
    cy.get('[data-testid="style-sample-next-style"]').should("be.disabled");

    cy.get('[data-testid="style-sample-prev-style"]').click();
    counter().should("have.text", "1 / 2");
  });

  it("confirms the shown style and marks the selected one", () => {
    mountDialog("beta", "alpha");

    cy.get('[data-testid="style-sample-selected-mark"]').should("not.exist");
    cy.get('[data-testid="style-sample-prev-style"]').click();
    cy.get('[data-testid="style-sample-selected-mark"]').should("contain.text", "현재 선택된 스타일");
    cy.get('[data-testid="style-sample-next-style"]').click();
    cy.get('[data-testid="style-sample-confirm"]').should("contain.text", "이 스타일 사용").click();
    cy.get("@confirm").should("have.been.calledOnceWith", "beta");
  });

  it("closes on Escape and on the close button", () => {
    mountDialog();

    dialog().type("{esc}");
    cy.get("@close").should("have.been.calledOnce");
    // The dialog is controlled; the parent unmounts it on close, so it is still here.
    cy.get('[data-testid="style-sample-close"]').should("have.attr", "aria-label", "닫기").click();
    cy.get("@close").should("have.been.calledTwice");
  });
});
