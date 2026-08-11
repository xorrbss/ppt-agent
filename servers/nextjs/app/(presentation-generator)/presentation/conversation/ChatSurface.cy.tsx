import React from "react";
import { NextIntlClientProvider } from "next-intl";
import enMessages from "@/i18n/messages/en.json";

import { ChatSurface } from "./ChatSurface";

describe("ChatSurface", () => {
  it("keeps assistant activity, approval and smart-selection context in one timeline", () => {
    const onAction = cy.stub().resolves();
    cy.mount(
      <NextIntlClientProvider locale="en" messages={enMessages} timeZone="UTC">
        <ChatSurface
        timeline={{
          hasMore: false,
          items: [
            {
              kind: "message",
              position: 1,
              message: {
                id: "user",
                role: "user",
                content: "Improve this",
                attachments: [{
                  id: "source",
                  name: "original brief.pdf",
                  status: "ready",
                  mimeType: "application/pdf",
                  sizeBytes: 2_048,
                  downloadUrl: "/api/v1/ppt/workspace/assets/source/download",
                }],
              },
            },
            { kind: "tool", position: 2, tool: { id: "tool", runId: "run", name: "edit_slide", status: "started", summary: "Updating title" } },
            { kind: "run", position: 3, run: { id: "run", status: "approval_required", actions: ["approve", "cancel", "undo"] } },
          ],
        }}
        loading={false}
        submitting={false}
        connection="live"
        pendingAssets={[{ id: "asset", name: "brief.pdf", status: "ready" }]}
        context={{ smartSelection: { html: "<p>Title</p>", text: "Title" } }}
        onRemoveAsset={cy.stub()}
        onSend={cy.stub().resolves()}
        onAction={onAction}
        />
      </NextIntlClientProvider>,
    );
    cy.get('[data-testid="smart-selection-badge"]').should("contain", "Smart selection");
    cy.get('[data-testid="conversation-surface"]').should("have.class", "font-inter");
    cy.get('[data-testid="conversation-attachment-source"]')
      .should("contain", "original brief.pdf")
      .and("contain", "2.0 KB");
    cy.get('[data-testid="conversation-attachment-download"]')
      .should("have.attr", "href", "/api/v1/ppt/workspace/assets/source/download")
      .and("have.attr", "download", "original brief.pdf");
    cy.get('[data-testid="tool-activity"]').should("contain", "edit_slide");
    cy.contains("Approval required before this edit is published.").should("be.visible");
    cy.contains("button", "Approve").click();
    cy.wrap(onAction).should("have.been.calledWith", "run", "approve");
  });

  it("uploads files dropped anywhere on the chat surface", () => {
    const onUpload = cy.stub().resolves();
    cy.mount(
      <NextIntlClientProvider locale="en" messages={enMessages} timeZone="UTC">
        <ChatSurface
          timeline={{ items: [], hasMore: false }}
          loading={false}
          submitting={false}
          connection="live"
          pendingAssets={[]}
          onUpload={onUpload}
          onRemoveAsset={cy.stub()}
          onSend={cy.stub().resolves()}
          onAction={cy.stub().resolves()}
        />
      </NextIntlClientProvider>,
    );

    const file = new File(["brief"], "customer-original.pdf", {
      type: "application/pdf",
    });
    const dataTransfer = new DataTransfer();
    dataTransfer.items.add(file);

    cy.get('[data-testid="conversation-surface"]')
      .as("surface")
      .trigger("dragenter", { dataTransfer })
      .then(() => {
        cy.get('[data-testid="conversation-drop-overlay"]')
          .should("be.visible")
          .and("contain", "Drop files to attach them");
      });

    cy.get("@surface").trigger("drop", { dataTransfer, force: true });

    cy.get('[data-testid="conversation-drop-overlay"]').should("not.exist");
    cy.wrap(onUpload).should("have.been.calledOnce").then(() => {
      const uploaded = onUpload.firstCall.args[0] as File[];
      expect(uploaded).to.have.length(1);
      expect(uploaded[0].name).to.equal("customer-original.pdf");
    });
  });

  it("uploads clipboard images pasted into the composer", () => {
    const onUpload = cy.stub().resolves();
    cy.mount(
      <NextIntlClientProvider locale="en" messages={enMessages} timeZone="UTC">
        <ChatSurface
          timeline={{ items: [], hasMore: false }}
          loading={false}
          submitting={false}
          connection="live"
          pendingAssets={[]}
          onUpload={onUpload}
          onRemoveAsset={cy.stub()}
          onSend={cy.stub().resolves()}
          onAction={cy.stub().resolves()}
        />
      </NextIntlClientProvider>,
    );

    const image = new File([new Uint8Array([137, 80, 78, 71])], "image.png", {
      type: "image/png",
    });
    const clipboardData = new DataTransfer();
    clipboardData.items.add(image);

    cy.get('[data-testid="conversation-composer"]').trigger("paste", {
      clipboardData,
    });

    cy.wrap(onUpload).should("have.been.calledOnce").then(() => {
      const uploaded = onUpload.firstCall.args[0] as File[];
      expect(uploaded).to.have.length(1);
      expect(uploaded[0].name).to.equal("image.png");
      expect(uploaded[0].type).to.equal("image/png");
    });
  });
});
