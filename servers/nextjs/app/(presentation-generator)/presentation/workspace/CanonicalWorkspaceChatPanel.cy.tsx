import React from "react";
import { NextIntlClientProvider } from "next-intl";
import enMessages from "@/i18n/messages/en.json";

import { CanonicalWorkspaceChatPanel } from "./CanonicalWorkspaceChatPanel";
import type { WorkspaceCommandDispatcher } from "./types";

const conversationId = "11111111-1111-4111-8111-111111111111";
const presentationId = "33333333-3333-4333-8333-333333333333";

describe("CanonicalWorkspaceChatPanel", () => {
  it("focuses the active slide and preserves typed canonical commands", () => {
    const onFocus = cy.stub().as("focus");
    let dispatcher: WorkspaceCommandDispatcher | null = null;
    const onDispatcherReady = cy.stub().callsFake((next: WorkspaceCommandDispatcher | null) => {
      dispatcher = next;
    }).as("dispatcher");
    const sentBodies: Array<Record<string, unknown>> = [];
    cy.intercept("POST", "**/api/v1/ppt/conversations", {
      statusCode: 200,
      body: { id: conversationId, presentation_id: presentationId, revision: 3 },
    }).as("conversation");
    cy.intercept("GET", `**/api/v1/ppt/conversations/${conversationId}/timeline*`, {
      statusCode: 200,
      body: {
        items: [{
          item_type: "run",
          id: "44444444-4444-4444-8444-444444444444",
          position: 1,
          run_type: "presentation.edit",
          status: "running",
          version: 2,
          actions: [{ id: "cancel", enabled: true }],
        }],
        event_high_watermark: "7",
        revision: 3,
      },
    }).as("timeline");
    cy.intercept("POST", `**/api/v1/ppt/conversations/${conversationId}/messages`, (request) => {
      sentBodies.push(request.body as Record<string, unknown>);
      request.reply({
        statusCode: 200,
        body: { conversation_id: conversationId, message_id: "55555555-5555-4555-8555-555555555555", run_id: "44444444-4444-4444-8444-444444444444", status: "queued" },
      });
    }).as("typedMessage");
    cy.intercept("GET", `**/api/v1/ppt/conversations/${conversationId}/events*`, {
      statusCode: 200,
      headers: { "content-type": "text/event-stream" },
      body: "",
    });

    cy.mount(
      <NextIntlClientProvider locale="en" messages={enMessages} timeZone="UTC">
        <CanonicalWorkspaceChatPanel
          presentationId={presentationId}
          currentSlide={2}
          onAgentSlideFocus={onFocus}
          onCommandDispatcherReady={onDispatcherReady}
        />
      </NextIntlClientProvider>,
    );

    cy.wait(["@conversation", "@timeline"]);
    cy.get("@focus").should("have.been.calledWithMatch", {
      slideIndex: 2,
      eventId: Cypress.sinon.match("canonical-conversation-focus-"),
    });
    cy.then(() => {
      expect(dispatcher).to.be.a("function");
      return dispatcher!("Restore the selected version", {
        commandType: "version.restore",
        context: { version_id: "version-9", source: "version_history" },
        assetIds: ["66666666-6666-4666-8666-666666666666"],
      });
    });
    cy.wait("@typedMessage").then(() => {
      expect(sentBodies).to.have.length(1);
      expect(sentBodies[0]).to.include({
        content: "Restore the selected version",
        command_type: "version.restore",
      });
      expect(sentBodies[0].operation_context).to.deep.equal({
        version_id: "version-9",
        source: "version_history",
      });
      expect(sentBodies[0].asset_ids).to.deep.equal([
        "66666666-6666-4666-8666-666666666666",
      ]);
    });
  });
});
