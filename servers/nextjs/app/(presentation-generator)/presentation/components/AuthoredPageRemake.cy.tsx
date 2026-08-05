import React from "react";

import AuthoredPageRemake from "./AuthoredPageRemake";
import { PresentationGenerationApi } from "../../services/api/presentation-generation";

const previewData = {
  kind: "authored_page_remake_preview" as const,
  phase: "preview_ready" as const,
  slide_index: 4,
  candidate_image: "/app_data/images/remake.png",
  candidate_digest: "sha256:candidate",
  source_digest: "sha256:source",
  candidate_issues: [],
  validation: { candidate_dom: "passed" },
  generation: { elapsed_ms: 8123, llm_scope: "single_page" },
};

function preparePreview() {
  cy.clock();
  cy.stub(PresentationGenerationApi, "previewAuthoredPageRemake")
    .as("previewRequest")
    .resolves({ id: "task-remake-preview" });
  cy.stub(PresentationGenerationApi, "getGenerationStatus")
    .as("previewStatus")
    .resolves({
      status: "completed",
      message: "5페이지의 새 버전 미리보기가 준비되었습니다.",
      data: previewData,
    });
}

describe("AuthoredPageRemake", () => {
  it("uses the exact label and generates only the current page preview", () => {
    preparePreview();
    cy.mount(
      <AuthoredPageRemake presentationId="deck-1" currentSlide={4} />,
    );

    cy.get('[data-testid="page-remake-trigger"]')
      .should("contain.text", "페이지 다시 만들기")
      .click();
    cy.contains("5페이지만 새 레이아웃으로 다시 만듭니다.").should("be.visible");
    cy.get('[data-testid="page-remake-preview-create"]').click();
    cy.tick(2_000);

    cy.get("@previewRequest").should("have.been.calledOnceWith", "deck-1", 4);
    cy.get('[data-testid="page-remake-preview"]').should("be.visible");
    cy.get('[data-testid="page-remake-publish"]').should(
      "contain.text",
      "이 버전 적용",
    );
  });

  it("publishes only after explicit approval in the legacy write path", () => {
    preparePreview();
    const publish = cy
      .stub(PresentationGenerationApi, "publishAuthoredPageRemake")
      .as("publishRequest")
      .resolves({ status: "published" });
    const onReload = cy.stub().as("reload");
    cy.mount(
      <AuthoredPageRemake
        presentationId="deck-1"
        currentSlide={4}
        onReload={onReload}
      />,
    );

    cy.get('[data-testid="page-remake-trigger"]').click();
    cy.get('[data-testid="page-remake-preview-create"]').click();
    cy.tick(2_000);
    cy.then(() => expect(publish).not.to.have.been.called);
    cy.get('[data-testid="page-remake-publish"]').click();

    cy.get("@publishRequest").should(
      "have.been.calledOnceWith",
      "deck-1",
      4,
      "task-remake-preview",
    );
    cy.get("@reload").should("have.been.calledOnce");
  });

  it("publishes through the fenced workspace operation in chat-first mode", () => {
    preparePreview();
    const workspaceCommand = cy.stub().as("workspaceCommand").resolves();
    const directPublish = cy
      .stub(PresentationGenerationApi, "publishAuthoredPageRemake")
      .as("directPublish")
      .resolves({ status: "published" });
    cy.mount(
      <AuthoredPageRemake
        presentationId="deck-1"
        currentSlide={4}
        chatFirstEnabled
        workspaceCommandsEnabled
        onWorkspaceCommand={workspaceCommand}
      />,
    );

    cy.get('[data-testid="page-remake-trigger"]').click();
    cy.get('[data-testid="page-remake-preview-create"]').click();
    cy.tick(2_000);
    cy.get('[data-testid="page-remake-publish"]').click();

    cy.then(() => {
      expect(directPublish).not.to.have.been.called;
      expect(workspaceCommand).to.have.been.calledOnce;
      expect(workspaceCommand.firstCall.args).to.deep.equal([
        "5페이지 미리보기를 적용해 줘",
        {
          commandType: "slide.regenerate",
          context: {
            slide_index: 4,
            preview_task_id: "task-remake-preview",
            source: "page_remake_button",
          },
        },
      ]);
    });
  });
});
