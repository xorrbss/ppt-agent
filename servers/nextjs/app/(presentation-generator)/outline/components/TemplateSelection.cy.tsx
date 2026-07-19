import React, { useState } from "react";
import { mount } from "cypress/react";
import { Provider } from "react-redux";
import { AppRouterContext } from "next/dist/shared/lib/app-router-context.shared-runtime";

import TemplateService from "@/app/(presentation-generator)/services/api/template";
import type { TemplateLayoutsWithSettings } from "@/app/presentation-templates/utils";
import {
  DEFAULT_AUTHORED_STYLE,
  normalizeAuthoredStyles,
} from "@/app/(presentation-generator)/services/api/authored";
import type { AuthoredStyleSummary } from "@/app/(presentation-generator)/services/api/authored";
import { setPptGenUploadState } from "@/store/slices/presentationGenUpload";
import { store } from "@/store/store";

import TemplateSelection, { AUTHORED_TEMPLATE_ID } from "./TemplateSelection";

import "@/app/globals.css";

const createRouter = () => ({
  push: cy.stub(),
  back: cy.stub(),
  forward: cy.stub(),
  refresh: cy.stub(),
  replace: cy.stub(),
  prefetch: cy.stub().resolves(),
  route: "/",
  pathname: "/",
  query: {},
  asPath: "/",
});

const serverStyles: AuthoredStyleSummary[] = [
  {
    id: "strategic-navy",
    name: "전략 네이비",
    description: "경영진 보고와 전략 제안에 어울리는 선명한 네이비 스타일",
    preview: { bg: "#E2E8F0", accent: "#0F172A" },
  },
  DEFAULT_AUTHORED_STYLE,
];

function SelectionHarness({
  initialSelection = null,
  onSelect,
}: {
  initialSelection?: string | null;
  onSelect: (
    template: TemplateLayoutsWithSettings | string
  ) => void;
}) {
  const [selectedTemplate, setSelectedTemplate] = useState<string | null>(
    initialSelection
  );

  return (
    <TemplateSelection
      selectedTemplate={selectedTemplate}
      onSelectTemplate={(template) => {
        onSelect(template);
        setSelectedTemplate(typeof template === "string" ? template : template.id);
      }}
    />
  );
}

const mountSelection = (initialSelection: string | null = null) => {
  const onSelect = cy.stub().as("selectTemplate");

  mount(
    <AppRouterContext.Provider value={createRouter() as any}>
      <Provider store={store}>
        <SelectionHarness
          initialSelection={initialSelection}
          onSelect={onSelect}
        />
      </Provider>
    </AppRouterContext.Provider>
  );
};

describe("<TemplateSelection /> authored styles", () => {
  beforeEach(() => {
    cy.viewport(1280, 900);
    cy.stub(TemplateService, "getCustomTemplateSummaries").resolves([]);
    store.dispatch(
      setPptGenUploadState({
        authoredStyle: DEFAULT_AUTHORED_STYLE.id,
        authoredVisionQa: false,
      })
    );
  });

  it("normalizes the public API shape, keeps default first, and drops brief", () => {
    const normalized = normalizeAuthoredStyles([
      {
        ...serverStyles[0],
        brief: "서버 내부 프롬프트는 UI로 전달하지 않는다",
      },
      serverStyles[0],
    ]);

    expect(normalized.map((style) => style.id)).to.deep.equal([
      "default",
      "strategic-navy",
    ]);
    expect(JSON.stringify(normalized)).not.to.contain("brief");
  });

  it("renders both sections and stores a selected authored style separately", () => {
    cy.intercept("GET", "**/api/v1/ppt/authored/styles", {
      statusCode: 200,
      body: serverStyles,
    }).as("authoredStyles");

    mountSelection();
    cy.wait("@authoredStyles");

    cy.get('[data-testid="authored-template-section"]')
      .should("contain", "AI 저작 템플릿(고품질)")
      .and("contain", "기본 블루프린트")
      .and("contain", "전략 네이비");
    cy.get('[data-testid="layout-template-section"]')
      .should("contain", "레이아웃 템플릿(편집 가능 PPTX)");
    cy.get('[data-testid="layout-template-grid"] > :first-child')
      .should("contain", "템플릿 만들기");

    cy.get('[data-testid="authored-style-select-default"]')
      .should("have.attr", "tabindex", "0")
      .focus()
      .type("{rightarrow}");

    cy.get("@selectTemplate").should(
      "have.been.calledOnceWith",
      AUTHORED_TEMPLATE_ID
    );
    cy.get('[data-testid="authored-style-select-strategic-navy"]')
      .should("have.attr", "aria-checked", "true")
      .and("have.attr", "tabindex", "0")
      .and("have.focus")
      .and("contain", "선택됨");
    cy.get('[data-testid="authored-style-select-default"]').should(
      "have.attr",
      "tabindex",
      "-1"
    );
    cy.then(() => {
      expect(store.getState().pptGenUpload.authoredStyle).to.equal(
        "strategic-navy"
      );
    });
  });

  it("keeps vision-QA visible for the selected style without selecting the card again", () => {
    store.dispatch(setPptGenUploadState({ authoredStyle: "strategic-navy" }));
    cy.intercept("GET", "**/api/v1/ppt/authored/styles", {
      statusCode: 200,
      body: serverStyles,
    }).as("selectedAuthoredStyles");

    mountSelection(AUTHORED_TEMPLATE_ID);
    cy.wait("@selectedAuthoredStyles");

    cy.get('[data-testid="authored-vision-qa"]')
      .should("be.visible")
      .and("not.be.checked");
    cy.get('[data-testid="authored-vision-qa"]').click({ force: true });
    cy.get('[data-testid="authored-vision-qa"]').should("be.checked");
    cy.get("@selectTemplate").should("not.have.been.called");
    cy.then(() => {
      expect(store.getState().pptGenUpload.authoredVisionQa).to.equal(true);
    });
  });

  it("offers the default card immediately while styles are loading", () => {
    cy.intercept("GET", "**/api/v1/ppt/authored/styles", {
      delay: 1000,
      statusCode: 200,
      body: serverStyles,
    }).as("delayedAuthoredStyles");

    mountSelection();

    cy.get('[data-testid="authored-style-card-default"]').should("be.visible");
    cy.contains(
      "스타일 목록을 불러오는 중입니다. 기본 스타일은 바로 선택할 수 있습니다."
    ).should("be.visible");
    cy.get('[data-testid="authored-style-select-default"]').click();
    cy.get("@selectTemplate").should(
      "have.been.calledOnceWith",
      AUTHORED_TEMPLATE_ID
    );
    cy.wait("@delayedAuthoredStyles");
  });

  it("falls back to the default card for an empty response", () => {
    cy.intercept("GET", "**/api/v1/ppt/authored/styles", {
      statusCode: 200,
      body: [],
    }).as("emptyAuthoredStyles");

    mountSelection();
    cy.wait("@emptyAuthoredStyles");

    cy.get('[data-testid="authored-style-grid"]')
      .find('[data-testid^="authored-style-card-"]')
      .should("have.length", 1)
      .and("contain", "기본 블루프린트");
  });

  it("falls back to the default card after an API error", () => {
    cy.intercept("GET", "**/api/v1/ppt/authored/styles", {
      statusCode: 500,
      body: { detail: "failed" },
    });

    mountSelection();

    cy.get('[data-testid="authored-style-card-default"]').should("be.visible");
    cy.contains("스타일 목록을 불러오지 못해 기본 스타일을 표시합니다.")
      .should("be.visible");
  });

  it("wraps a long Korean description without overflowing on a small screen", () => {
    const longDescription =
      "아주긴한글설명이작은화면에서도카드바깥으로넘치지않고자연스럽게여러줄로표시되는지확인합니다";
    cy.viewport(375, 760);
    cy.intercept("GET", "**/api/v1/ppt/authored/styles", {
      statusCode: 200,
      body: [
        {
          ...serverStyles[0],
          description: longDescription,
        },
      ],
    });

    mountSelection();

    cy.contains(longDescription).should("be.visible");
    cy.get('[data-testid="authored-style-card-strategic-navy"]').then(
      ([card]) => {
        expect(card.scrollWidth).to.be.at.most(card.clientWidth + 1);
      }
    );
  });
});
