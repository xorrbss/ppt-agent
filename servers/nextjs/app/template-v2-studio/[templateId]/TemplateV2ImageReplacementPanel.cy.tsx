import React from "react";

import TemplateV2ImageReplacementPanel, {
  type TemplateV2ImageReplacementApplyIntent,
} from "./TemplateV2ImageReplacementPanel";

const ONE_PIXEL_PNG =
  "iVBORw0KGgoAAAANSUhEUgAAAAIAAAABCAYAAAD0In+KAAAADElEQVR42mNk+M8AAAICAQB7CYspAAAAAElFTkSuQmCC";

function mountPanel(
  onApply: (
    intent: TemplateV2ImageReplacementApplyIntent,
    historyKey: string,
  ) => void,
) {
  cy.mount(
    <TemplateV2ImageReplacementPanel
      element={{
        type: "image",
        data: "/app_data/images/original.png",
        position: { x: 10, y: 10 },
        size: { width: 200, height: 100 },
        fit: "cover",
        is_icon: false,
        vendor_metadata: { preserve: true },
      }}
      revision={5}
      disabled={false}
      onApply={onApply}
    />,
  );
}

describe("TemplateV2ImageReplacementPanel", () => {
  it("validates, previews, chooses a bounded crop, and emits an apply intent", () => {
    const apply = cy.stub().as("apply");
    mountPanel(apply);

    cy.get('[aria-label="Choose local replacement image"]').selectFile(
      {
        contents: Cypress.Buffer.from(ONE_PIXEL_PNG, "base64"),
        fileName: "local.png",
        mimeType: "image/png",
      },
      { force: true },
    );

    cy.get('[aria-label="Local image validation results"]').within(() => {
      cy.contains("Valid · image/png");
      cy.contains("2 × 1 px");
    });
    cy.get('[aria-label="Image crop preview"]').should("be.visible");
    cy.contains("Remote URLs, R2 uploads, and network fetches are not available.");
    cy.contains("Rule of thirds").click();
    cy.contains("button", "Apply local replacement").click();

    cy.get("@apply").should("have.been.calledOnce");
    cy.get<sinon.SinonStub>("@apply").then((stub) => {
      const [intent, historyKey] = stub.getCall(0).args as [
        TemplateV2ImageReplacementApplyIntent,
        string,
      ];
      expect(intent.expectedRevision).to.equal(5);
      expect(intent.idempotencyKey).to.match(/^template-v2-local-image-/);
      expect(intent.patch.data).to.match(/^data:image\/png;base64,/);
      expect(intent.patch.focus_x).to.equal(33.333);
      expect(intent.patch.focus_y).to.equal(50);
      expect(intent.patch.crop_scale).to.equal(1.25);
      expect(
        intent.patch.__template_v2_local_asset.retention.delete_immediately,
      ).to.equal(false);
      expect(historyKey).to.match(/^image-replacement-/);
    });
    cy.get('[aria-label="Local image validation results"]').should("not.exist");
  });

  it("rejects a spoofed MIME before preview", () => {
    mountPanel(cy.stub());

    cy.get('[aria-label="Choose local replacement image"]').selectFile(
      {
        contents: Cypress.Buffer.from(ONE_PIXEL_PNG, "base64"),
        fileName: "spoofed.jpg",
        mimeType: "image/jpeg",
      },
      { force: true },
    );

    cy.contains("The declared MIME type does not match the file magic bytes.");
    cy.contains("template_v2_local_image_magic_mismatch");
    cy.get('[aria-label="Image crop preview"]').should("not.exist");
  });

  it("cancels without applying", () => {
    const apply = cy.stub().as("apply");
    mountPanel(apply);
    cy.get('[aria-label="Choose local replacement image"]').selectFile(
      {
        contents: Cypress.Buffer.from(ONE_PIXEL_PNG, "base64"),
        fileName: "local.png",
        mimeType: "image/png",
      },
      { force: true },
    );
    cy.contains("button", "Cancel preview").click();
    cy.get("@apply").should("not.have.been.called");
    cy.get('[aria-label="Image crop preview"]').should("not.exist");
  });
});
