import React, { useState } from "react";
import { mount } from "cypress/react";
import { Provider } from "react-redux";
import { AppRouterContext } from "next/dist/shared/lib/app-router-context.shared-runtime";

import TemplateService from "@/app/(presentation-generator)/services/api/template";
import { DEFAULT_AUTHORED_STYLE } from "@/app/(presentation-generator)/services/api/authored";
import { selectableTemplates } from "@/app/presentation-templates";
import type { TemplateLayoutsWithSettings } from "@/app/presentation-templates/utils";
import { setPptGenUploadState } from "@/store/slices/presentationGenUpload";
import { store } from "@/store/store";

import TemplateSelection, { AUTHORED_TEMPLATE_ID } from "./TemplateSelection";
import {
  catalogStyles,
  createStyle,
} from "./TemplateSelection.test-data";

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

function SelectionHarness({
  initialSelection = null,
  onSelect,
}: {
  initialSelection?: string | null;
  onSelect: (template: TemplateLayoutsWithSettings | string) => void;
}) {
  const [selectedTemplate, setSelectedTemplate] = useState<string | null>(
    initialSelection
  );

  return (
    <TemplateSelection
      selectedTemplate={selectedTemplate}
      onSelectTemplate={(template) => {
        onSelect(template);
        setSelectedTemplate(
          typeof template === "string" ? template : template.id
        );
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

const interceptCatalog = (body: unknown = catalogStyles) => {
  cy.intercept("GET", "**/api/v1/ppt/authored/styles", {
    statusCode: 200,
    body,
  }).as("authoredStyles");
};

describe("<TemplateSelection /> authored style catalog", () => {
  beforeEach(() => {
    cy.viewport(1280, 900);
    cy.intercept("GET", "**/api/v1/ppt/structured-templates*", {
      statusCode: 200,
      body: [],
    }).as("structuredTemplates");
    cy.stub(TemplateService, "getCustomTemplateSummaries")
      .resolves([])
      .as("customTemplateSummaries");
    store.dispatch(
      setPptGenUploadState({
        authoredStyle: DEFAULT_AUTHORED_STYLE.id,
        authoredVisionQa: false,
      })
    );
  });

  it("renders 30 styles with Korean category counts while preserving both template sections", () => {
    interceptCatalog();
    mountSelection();
    cy.wait("@authoredStyles");

    cy.get('[data-testid="authored-template-section"]')
      .should("contain", "AI 저작 템플릿(고품질)")
      .and("contain", "기본 블루프린트")
      .and("contain", "사이버 AI");
    cy.get('[data-testid="layout-template-section"]')
      .should("contain", "레이아웃 템플릿(편집 가능 PPTX)");
    cy.get('[data-testid="layout-template-grid"] > :first-child').should(
      "contain",
      "템플릿 만들기"
    );
    cy.get('[data-testid="authored-style-grid"]')
      .find('[data-testid^="authored-style-card-"]')
      .should("have.length", 30);

    const expectedCounts = {
      all: ["전체", 30],
      general: ["범용", 1],
      business: ["비즈니스", 7],
      technology: ["기술", 6],
      research: ["리서치", 4],
      editorial: ["에디토리얼", 7],
      creative: ["크리에이티브", 5],
    } as const;
    Object.entries(expectedCounts).forEach(([category, [label, count]]) => {
      cy.get(`[data-testid="authored-category-${category}"]`)
        .should("contain", label)
        .and("contain", String(count));
    });
  });

  it("combines category filtering and metadata search without touching layout templates", () => {
    interceptCatalog();
    mountSelection();
    cy.wait("@authoredStyles");

    cy.get('[data-testid="authored-category-business"]')
      .click()
      .should("have.attr", "aria-pressed", "true");
    cy.get('[data-testid="authored-style-grid"]')
      .find('[data-testid^="authored-style-card-"]')
      .should("have.length", 7);
    cy.get('[data-testid="authored-style-search"]').type("이사회");
    cy.get('[data-testid="authored-style-grid"]')
      .find('[data-testid^="authored-style-card-"]')
      .should("have.length", 1)
      .and("contain", "임원 보고서");
    cy.get('[data-testid="authored-results-status"]').should(
      "contain",
      "비즈니스 · 1개 스타일"
    );
    cy.get('[data-testid="layout-template-grid"]').should(
      "contain",
      "템플릿 만들기"
    );
    cy.get("@selectTemplate").should("not.have.been.called");
  });

  it("keeps the built-in and custom layout selection paths working", () => {
    cy.get("@customTemplateSummaries").then((summaryStub: any) => {
      summaryStub.resolves([
        { id: "custom-regression", name: "회귀 커스텀", total_layouts: 1 },
      ]);
    });
    cy.stub(TemplateService, "getCustomTemplateDetails").resolves({
      layouts: [],
      template: {},
    } as any);
    interceptCatalog();
    mountSelection();
    cy.wait("@authoredStyles");

    const firstBuiltIn = selectableTemplates[0];
    cy.get('[data-testid="layout-template-grid"]')
      .contains("h3", firstBuiltIn.name)
      .parent()
      .parent()
      .click();
    cy.get("@selectTemplate").should(
      "have.been.calledWith",
      firstBuiltIn
    );

    cy.get('[data-testid="layout-template-grid"]')
      .contains("h3", "회귀 커스텀")
      .parent()
      .parent()
      .click();
    cy.get("@selectTemplate").should(
      "have.been.calledWith",
      "custom-regression"
    );
  });

  it("selects an immutable Template V2 revision from the layout catalog", () => {
    cy.intercept("GET", "**/api/v1/ppt/structured-templates*", {
      statusCode: 200,
      body: [
        {
          id: "quarterly-review",
          name: "Quarterly Review",
          description: "Native editable business template",
          revision: 12,
          total_layouts: 4,
        },
      ],
    }).as("structuredTemplatesWithRevision");
    interceptCatalog();
    mountSelection();

    cy.wait("@structuredTemplatesWithRevision");
    cy.get('[data-testid="structured-template-quarterly-review"]')
      .should("have.attr", "type", "button")
      .and("have.attr", "aria-pressed", "false")
      .focus()
      .should("have.focus")
      .click();
    cy.get("@selectTemplate").should(
      "have.been.calledWith",
      "template-v2:quarterly-review?revision=12"
    );
  });

  it("searches tags and use cases and resets an empty result", () => {
    interceptCatalog();
    mountSelection();
    cy.wait("@authoredStyles");

    cy.get('[data-testid="authored-style-search"]').type("시스템 아키텍처");
    cy.get('[data-testid="authored-style-grid"]')
      .find('[data-testid^="authored-style-card-"]')
      .should("have.length", 1)
      .and("contain", "사이버 AI");

    cy.get('[data-testid="authored-style-search"]').clear().type("없는 스타일");
    cy.get('[data-testid="authored-empty-results"]').should("be.visible");
    cy.contains("button", "필터 초기화").click();
    cy.get('[data-testid="authored-style-grid"]')
      .find('[data-testid^="authored-style-card-"]')
      .should("have.length", 30);
  });

  it("keeps a selection and QA setting when filters hide and reveal the card", () => {
    interceptCatalog();
    mountSelection();
    cy.wait("@authoredStyles");

    cy.get('[data-testid="authored-style-select-strategic-navy"]').click();
    cy.get("@selectTemplate").should(
      "have.been.calledOnceWith",
      AUTHORED_TEMPLATE_ID
    );
    cy.get('[data-testid="authored-vision-qa"]').click();
    cy.get('[data-testid="authored-category-technology"]').click();

    cy.get('[data-testid="authored-style-card-strategic-navy"]').should(
      "not.exist"
    );
    cy.get('[data-testid="authored-results-status"]')
      .should("be.visible")
      .and("contain", "선택한 스타일은 현재 결과에서 숨겨져 있습니다.");
    cy.get('[data-testid="authored-style-grid"] button[role="radio"]')
      .filter('[tabindex="0"]')
      .should("have.length", 1)
      .and("have.attr", "aria-checked", "false");
    cy.then(() => {
      expect(store.getState().pptGenUpload.authoredStyle).to.equal(
        "strategic-navy"
      );
      expect(store.getState().pptGenUpload.authoredVisionQa).to.equal(true);
    });

    cy.get('[data-testid="authored-category-all"]').click();
    cy.get('[data-testid="authored-style-select-strategic-navy"]')
      .should("have.attr", "aria-checked", "true")
      .and("have.attr", "tabindex", "0");
    cy.get('[data-testid="authored-vision-qa"]').should("be.checked");
    cy.get("@selectTemplate").should("have.been.calledOnce");
  });

  it("supports Arrow, Home, End, and wrapping within the filtered radiogroup", () => {
    interceptCatalog();
    mountSelection();
    cy.wait("@authoredStyles");

    cy.get('[data-testid="authored-category-research"]').click();
    cy.get('[data-testid="authored-style-select-academic-edge"]')
      .should("have.attr", "tabindex", "0")
      .focus()
      .type("{rightarrow}");
    cy.get('[data-testid="authored-style-select-clinical-precision"]')
      .should("have.focus")
      .and("have.attr", "aria-checked", "true")
      .type("{end}");
    cy.get('[data-testid="authored-style-select-science-sketch"]')
      .should("have.focus")
      .and("have.attr", "aria-checked", "true")
      .type("{home}");
    cy.get('[data-testid="authored-style-select-academic-edge"]')
      .should("have.focus")
      .and("have.attr", "aria-checked", "true")
      .type("{leftarrow}");
    cy.get('[data-testid="authored-style-select-science-sketch"]')
      .should("have.focus")
      .and("have.attr", "aria-checked", "true");
    cy.then(() => {
      expect(store.getState().pptGenUpload.authoredStyle).to.equal(
        "science-sketch"
      );
    });
  });

  it("renders palette/variant-driven structures without image dependencies", () => {
    interceptCatalog();
    mountSelection();
    cy.wait("@authoredStyles");

    cy.get('[data-testid="authored-style-preview-cyber-ai"]')
      .should("have.attr", "data-variant", "intelligence-console")
      .and("have.attr", "data-preview-family", "console")
      .and("have.css", "background-color", "rgb(7, 19, 15)")
      .find("img")
      .should("not.exist");
    cy.get('[data-testid="authored-style-preview-architectural-portfolio"]')
      .should("have.attr", "data-variant", "future-layout")
      .and("have.attr", "data-preview-family", "structured");
    cy.get('[data-testid="authored-style-preview-default"]').should(
      "have.attr",
      "data-preview-family",
      "structured"
    );
    cy.get('[data-testid="authored-style-select-cyber-ai"]')
      .should(
        "have.attr",
        "aria-labelledby",
        "authored-style-title-cyber-ai"
      )
      .and(
        "have.attr",
        "aria-describedby",
        "authored-style-description-cyber-ai"
      );
  });

  it("toggles vision-QA independently from style selection", () => {
    store.dispatch(setPptGenUploadState({ authoredStyle: "strategic-navy" }));
    interceptCatalog();
    mountSelection(AUTHORED_TEMPLATE_ID);
    cy.wait("@authoredStyles");

    cy.get('[data-testid="authored-vision-qa"]')
      .should("be.visible")
      .and("not.be.checked")
      .click()
      .should("be.checked");
    cy.get("@selectTemplate").should("not.have.been.called");
    cy.then(() => {
      expect(store.getState().pptGenUpload.authoredVisionQa).to.equal(true);
    });
  });

  it("offers the default card immediately while styles are loading", () => {
    store.dispatch(setPptGenUploadState({ authoredStyle: "strategic-navy" }));
    cy.intercept("GET", "**/api/v1/ppt/authored/styles", {
      delay: 1000,
      statusCode: 200,
      body: catalogStyles,
    }).as("delayedAuthoredStyles");

    mountSelection();

    cy.get('[data-testid="authored-style-card-default"]').should("be.visible");
    cy.get('[data-testid="authored-style-select-default"]').should(
      "have.attr",
      "aria-checked",
      "false"
    );
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
    interceptCatalog([]);
    mountSelection();
    cy.wait("@authoredStyles");

    cy.get('[data-testid="authored-style-grid"]')
      .find('[data-testid^="authored-style-card-"]')
      .should("have.length", 1)
      .and("contain", "기본 블루프린트");
  });

  it("falls back to the default card after an API error", () => {
    store.dispatch(setPptGenUploadState({ authoredStyle: "strategic-navy" }));
    cy.intercept("GET", "**/api/v1/ppt/authored/styles", {
      statusCode: 500,
      body: { detail: "failed" },
    }).as("failedAuthoredStyles");
    mountSelection(AUTHORED_TEMPLATE_ID);
    cy.wait("@failedAuthoredStyles");

    cy.get('[data-testid="authored-style-card-default"]').should("be.visible");
    cy.get('[data-testid="authored-style-select-default"]').should(
      "have.attr",
      "aria-checked",
      "true"
    );
    cy.contains("스타일 목록을 불러오지 못해 기본 스타일을 표시합니다.").should(
      "be.visible"
    );
    cy.then(() => {
      expect(store.getState().pptGenUpload.authoredStyle).to.equal("default");
    });
  });

  it("wraps a long Korean description without overflowing on a small screen", () => {
    const longDescription =
      "아주긴한글설명이작은화면에서도카드바깥으로넘치지않고자연스럽게여러줄로표시되는지확인합니다";
    cy.viewport(375, 760);
    interceptCatalog([
      createStyle("long-copy", "긴 설명", "editorial", 1, {
        description: longDescription,
      }),
    ]);
    mountSelection();
    cy.wait("@authoredStyles");

    cy.contains(longDescription).should("exist");
    cy.get('[data-testid="authored-style-card-long-copy"]').then(([card]) => {
      expect(card.scrollWidth).to.be.at.most(card.clientWidth + 1);
    });
  });
});
