import React from "react";

import { DashboardApi } from "@/app/(presentation-generator)/services/api/dashboard";
import GenerationInfoDialog from "@/app/(presentation-generator)/presentation/components/GenerationInfoDialog";

const generationInfo = {
  prompt: "삼성디스플레이 정보보호 업무 AX 전환 전략을 만들어줘",
  attachment_file_names: ["정보보호 현황.pdf", "추진계획.xlsx"],
  settings: {
    n_slides: 10,
    language: "ko",
    tone: "professional",
    verbosity: "standard",
    instructions: "경영진 보고용으로 작성",
    include_table_of_contents: true,
    include_title_slide: true,
    web_search: false,
    mode: "authored",
    version: "v1-standard",
  },
};

describe("GenerationInfoDialog", () => {
  it("shows the prompt, attachment names, and generation settings", () => {
    cy.stub(DashboardApi, "getPresentationGenerationInfo")
      .resolves(generationInfo)
      .as("getGenerationInfo");

    cy.mount(<GenerationInfoDialog presentationId="deck-1" />);
    cy.get('[data-testid="generation-info-trigger"]').click();

    cy.get("@getGenerationInfo").should("have.been.calledOnceWith", "deck-1");
    cy.contains(generationInfo.prompt).should("be.visible");
    cy.contains("정보보호 현황.pdf").should("be.visible");
    cy.contains("추진계획.xlsx").should("be.visible");
    cy.contains("10장").should("be.visible");
    cy.contains("전문적").should("be.visible");
    cy.contains("경영진 보고용으로 작성").should("be.visible");
  });

  it("shows a clear empty attachment state", () => {
    cy.stub(DashboardApi, "getPresentationGenerationInfo").resolves({
      ...generationInfo,
      attachment_file_names: [],
    });

    cy.mount(<GenerationInfoDialog presentationId="deck-1" />);
    cy.get('[data-testid="generation-info-trigger"]').click();
    cy.contains("첨부한 파일이 없습니다.").should("be.visible");
  });
});
