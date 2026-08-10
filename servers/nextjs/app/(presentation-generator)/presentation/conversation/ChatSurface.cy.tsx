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
            { kind: "message", position: 1, message: { id: "user", role: "user", content: "Improve this", attachments: [] } },
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
    cy.get('[data-testid="tool-activity"]').should("contain", "edit_slide");
    cy.contains("Approval required before this edit is published.").should("be.visible");
    cy.contains("button", "Approve").click();
    cy.wrap(onAction).should("have.been.calledWith", "run", "approve");
  });
});
