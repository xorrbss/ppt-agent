import React from "react";

import { DashboardApi } from "@/app/(presentation-generator)/services/api/dashboard";
import GenerationInfoDialog from "@/app/(presentation-generator)/presentation/components/GenerationInfoDialog";

const generationInfo = {
  prompt: "정보보호 업무 AX 전환 전략을 만들어줘",
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
  it("shows the prompt, attachment basenames, and generation settings", () => {
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

  it("fails closed while loading generation info for a new presentation", () => {
    const nextGenerationInfo = {
      ...generationInfo,
      prompt: "두 번째 발표자료의 프롬프트",
      attachment_file_names: ["두 번째 자료.pdf"],
    };
    let resolveNextRequest!: (value: typeof nextGenerationInfo) => void;
    const nextRequest = new Promise<typeof nextGenerationInfo>((resolve) => {
      resolveNextRequest = resolve;
    });
    const request = cy.stub(DashboardApi, "getPresentationGenerationInfo");
    request.onFirstCall().resolves(generationInfo);
    request.onSecondCall().returns(nextRequest);

    cy.mount(<GenerationInfoDialog presentationId="deck-1" />).then(
      ({ rerender }) => {
        cy.get('[data-testid="generation-info-trigger"]').click();
        cy.contains(generationInfo.prompt).should("be.visible");

        cy.then(() => {
          rerender(<GenerationInfoDialog presentationId="deck-2" />);
        });
        cy.contains(generationInfo.prompt).should("not.exist");
        cy.contains("생성 정보를 불러오는 중입니다.").should("be.visible");
        cy.wrap(request).should("have.been.calledTwice");

        cy.then(() => resolveNextRequest(nextGenerationInfo));
        cy.contains(nextGenerationInfo.prompt).should("be.visible");
        cy.contains("두 번째 자료.pdf").should("be.visible");
      }
    );
  });

  it("refetches after the same presentation is regenerated and reopened", () => {
    const regeneratedInfo = {
      ...generationInfo,
      prompt: "재생성된 발표자료의 프롬프트",
      attachment_file_names: ["재생성 자료.pdf"],
    };
    let resolveRegeneratedRequest!: (value: typeof regeneratedInfo) => void;
    const regeneratedRequest = new Promise<typeof regeneratedInfo>((resolve) => {
      resolveRegeneratedRequest = resolve;
    });
    const request = cy.stub(DashboardApi, "getPresentationGenerationInfo");
    request.onFirstCall().resolves(generationInfo);
    request.onSecondCall().returns(regeneratedRequest);

    cy.mount(<GenerationInfoDialog presentationId="deck-1" />);
    cy.get('[data-testid="generation-info-trigger"]').click();
    cy.contains(generationInfo.prompt).should("be.visible");
    cy.get('[role="dialog"]').contains("button", "Close").click();

    cy.get('[data-testid="generation-info-trigger"]').click();
    cy.contains(generationInfo.prompt).should("not.exist");
    cy.contains("생성 정보를 불러오는 중입니다.").should("be.visible");
    cy.wrap(request).should("have.been.calledTwice");

    cy.then(() => resolveRegeneratedRequest(regeneratedInfo));
    cy.contains(regeneratedInfo.prompt).should("be.visible");
    cy.contains("재생성 자료.pdf").should("be.visible");
  });

  it("fails closed and retries after a request error", () => {
    const request = cy.stub(
      DashboardApi,
      "getPresentationGenerationInfo"
    );
    request.onFirstCall().rejects(new Error("unavailable"));
    request.onSecondCall().resolves(generationInfo);

    cy.mount(<GenerationInfoDialog presentationId="deck-1" />);
    cy.get('[data-testid="generation-info-trigger"]').click();
    cy.contains("생성 정보를 불러오지 못했습니다.").should("be.visible");
    cy.contains("정보보호 현황.pdf").should("not.exist");
    cy.contains("다시 시도").click();
    cy.contains(generationInfo.prompt).should("be.visible");
    cy.wrap(request).should("have.been.calledTwice");
  });
});
