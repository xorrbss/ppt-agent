import React from "react";

import { WorkspaceComposer } from "./WorkspaceComposer";

function mountComposer(onFilesSelected: (files: File[]) => Promise<void>) {
  cy.mount(
    <WorkspaceComposer
      value=""
      onValueChange={cy.stub()}
      onFilesSelected={onFilesSelected}
      onSend={cy.stub().resolves()}
    />,
  );
}

describe("WorkspaceComposer", () => {
  it("uploads files dropped onto the previous chat composer", () => {
    const onFilesSelected = cy.stub().resolves();
    mountComposer(onFilesSelected);
    const file = new File(["brief"], "customer-original.pdf", {
      type: "application/pdf",
    });
    const dataTransfer = new DataTransfer();
    dataTransfer.items.add(file);

    cy.get('[data-testid="workspace-command-form"]')
      .as("composer")
      .trigger("dragenter", { dataTransfer });
    cy.get("@composer").should("have.class", "ring-blue-400");
    cy.get("@composer").trigger("drop", { dataTransfer, force: true });

    cy.wrap(onFilesSelected).should("have.been.calledOnce").then(() => {
      const files = onFilesSelected.firstCall.args[0] as File[];
      expect(files[0].name).to.equal("customer-original.pdf");
    });
  });

  it("uploads clipboard images pasted into the previous chat composer", () => {
    const onFilesSelected = cy.stub().resolves();
    mountComposer(onFilesSelected);
    const image = new File([new Uint8Array([137, 80, 78, 71])], "image.png", {
      type: "image/png",
    });
    const clipboardData = new DataTransfer();
    clipboardData.items.add(image);

    cy.get("#workspace-command").trigger("paste", { clipboardData });

    cy.wrap(onFilesSelected).should("have.been.calledOnce").then(() => {
      const files = onFilesSelected.firstCall.args[0] as File[];
      expect(files[0].name).to.equal("image.png");
      expect(files[0].type).to.equal("image/png");
    });
  });
});
