import React from "react";

import { PageRemakeHistory } from "./PageRemakeHistory";
import { PresentationGenerationApi } from "../../services/api/presentation-generation";
import { notify } from "@/components/ui/sonner";

const restorable = {
  preview_task_id: "task-restorable",
  slide_index: 4,
  status: "completed",
  phase: "preview_ready",
  candidate_image: "users/o/authored/d/slide_4_abc.png",
  candidate_issues: [
    { category: "contrast", severity: "high" },
    { category: "contrast", severity: "medium" },
    { category: "clipping", severity: "medium" },
  ],
  validation: { candidate_dom: "warning" },
  restorable: true,
};

function stubToasts() {
  cy.stub(notify, "success").as("notifySuccess");
  cy.stub(notify, "error").as("notifyError");
}

describe("PageRemakeHistory", () => {
  it("replays a still-valid preview and applies it", () => {
    stubToasts();
    const publish = cy
      .stub(PresentationGenerationApi, "publishAuthoredPageRemake")
      .as("publish")
      .resolves({ status: "published" });

    cy.mount(
      <PageRemakeHistory
        presentationId="deck-1"
        entries={[restorable]}
        onApplied={cy.stub().as("applied")}
      />,
    );

    // The candidate survives a reload as a real preview, not just a log line.
    cy.get('[data-testid="page-remake-history-4"]').should(
      "contain.text",
      "페이지 다시 만들기 · 5페이지",
    );
    cy.get('[data-testid="page-remake-history-4"] img').should("be.visible");
    cy.get('[data-testid="page-remake-history-warning"]')
      .should("contain.text", "확인할 항목이 발견되었습니다")
      .and("contain.text", "명암 대비 2건")
      .and("contain.text", "잘림 1건");

    cy.get('[data-testid="page-remake-history-apply-4"]').click();
    cy.then(() => {
      expect(publish).to.have.been.calledOnceWith(
        "deck-1",
        4,
        "task-restorable",
      );
    });
    cy.get("@applied").should("have.been.calledOnceWith", 4);
    cy.get("@notifyError").should("not.have.been.called");
    // Once applied the same candidate must not be publishable twice.
    cy.get('[data-testid="page-remake-history-apply-4"]').should("not.exist");
    cy.get('[data-testid="page-remake-history-applied"]').should("be.visible");
  });

  // The legacy REST route answers 409 for chat-first decks, so a restored
  // preview must be published through the workspace command pipeline.
  it("publishes through the workspace command when one is available", () => {
    stubToasts();
    const directPublish = cy
      .stub(PresentationGenerationApi, "publishAuthoredPageRemake")
      .as("directPublish");
    const workspaceCommand = cy.stub().as("workspaceCommand").resolves();

    cy.mount(
      <PageRemakeHistory
        presentationId="deck-1"
        entries={[restorable]}
        onWorkspaceCommand={workspaceCommand}
        onApplied={cy.stub().as("applied")}
      />,
    );

    cy.get('[data-testid="page-remake-history-apply-4"]').click();
    cy.then(() => {
      expect(directPublish).not.to.have.been.called;
      expect(workspaceCommand).to.have.been.calledOnce;
      expect(workspaceCommand.firstCall.args[1]).to.deep.equal({
        commandType: "slide.regenerate",
        context: {
          slide_index: 4,
          preview_task_id: "task-restorable",
          source: "page_remake_history",
        },
      });
    });
    cy.get("@applied").should("have.been.calledOnceWith", 4);
    cy.get("@notifyError").should("not.have.been.called");
  });

  it("refuses to apply a preview whose page has since changed", () => {
    stubToasts();
    cy.stub(PresentationGenerationApi, "publishAuthoredPageRemake").as(
      "publish",
    );

    cy.mount(
      <PageRemakeHistory
        presentationId="deck-1"
        entries={[{ ...restorable, restorable: false }]}
      />,
    );

    cy.get('[data-testid="page-remake-history-stale"]').should(
      "contain.text",
      "적용할 수 없습니다",
    );
    cy.get('[data-testid="page-remake-history-apply-4"]').should("not.exist");
    cy.get("@publish").should("not.have.been.called");
  });

  it("shows a failed attempt in Korean without an internal code", () => {
    stubToasts();
    cy.mount(
      <PageRemakeHistory
        presentationId="deck-1"
        entries={[
          {
            preview_task_id: "task-failed",
            slide_index: 2,
            status: "error",
            phase: "failed",
            restorable: false,
            error_code: "authored_page_remake_generation_failed",
          },
        ]}
      />,
    );

    cy.get('[data-testid="page-remake-history-failed"]')
      .should("be.visible")
      .and("not.contain.text", "authored_page_remake_generation_failed");
  });

  it("offers a retry that reopens the flow for that page", () => {
    stubToasts();
    cy.mount(
      <PageRemakeHistory
        presentationId="deck-1"
        entries={[restorable]}
        onRegenerate={cy.stub().as("regenerate")}
      />,
    );

    cy.get('[data-testid="page-remake-history-regenerate-4"]').click();
    cy.get("@regenerate").should("have.been.calledOnceWith", 4);
  });
});
