import React from "react";
import { NextIntlClientProvider } from "next-intl";

import { CanonicalWorkspaceChatPanel } from "@/app/(presentation-generator)/presentation/workspace/CanonicalWorkspaceChatPanel";
import type { WorkspaceCommandDispatcher } from "@/app/(presentation-generator)/presentation/workspace/types";
import enMessages from "@/i18n/messages/en.json";

const presentationId = "11111111-1111-4111-8111-111111111111";
const command = "Canonical live edit [live-success] [live-backfill]";
let backend = "http://127.0.0.1:8765";
let requested = false;

function selectLiveBackend() {
  cy.window().then((window) => {
    const url = new URL(window.location.href);
    url.searchParams.set("fastapiUrl", backend);
    window.history.replaceState({}, "", url);
  });
}

describe("CanonicalWorkspaceChatPanel with live FastAPI", () => {
  before(() => {
    cy.env(["WORKSPACE_LIVE_BACKEND"]).then((environment) => {
      if (!environment.WORKSPACE_LIVE_BACKEND) return;
      requested = true;
      backend = String(environment.WORKSPACE_LIVE_BACKEND);
      cy.request(`${backend}/__test__/health`).its("body.ready").should("equal", true);
    });
  });

  beforeEach(function () {
    if (!requested) {
      this.skip();
      return;
    }
    selectLiveBackend();
  });

  afterEach(() => {
    if (requested) cy.request({ method: "POST", url: `${backend}/__test__/release`, failOnStatusCode: false });
  });

  it("creates the canonical conversation and persists a default presentation.edit turn", () => {
    const root = `${backend}/api/v1/ppt/conversations`;
    let dispatcher: WorkspaceCommandDispatcher | null = null;
    cy.intercept("POST", root).as("createConversation");
    cy.intercept("POST", `${root}/*/messages`).as("sendMessage");
    cy.intercept("GET", `${root}/*/timeline*`).as("timeline");
    cy.mount(
      <NextIntlClientProvider locale="en" messages={enMessages} timeZone="UTC">
        <div style={{ height: "720px", width: "460px" }}>
          <CanonicalWorkspaceChatPanel
            presentationId={presentationId}
            currentSlide={0}
            onCommandDispatcherReady={(next) => { dispatcher = next; }}
          />
        </div>
      </NextIntlClientProvider>,
    );
    cy.wait(["@createConversation", "@timeline"]);
    cy.get('[data-testid="conversation-composer"]').type(command);
    cy.contains("button", "Send").click();
    cy.wait("@sendMessage").then(({ request, response }) => {
      expect(request.body).to.include({ content: command });
      expect(request.body).not.to.have.property("command_type");
      expect(response?.statusCode).to.equal(200);
    });
    cy.contains(command).should("be.visible");
    cy.then(() => {
      expect(dispatcher).to.be.a("function");
      return dispatcher!("Delete this slide [canonical-approval]", {
        commandType: "slide.delete",
        context: { slide_index: 0, source: "canonical-live" },
      });
    });
    cy.wait("@sendMessage").then(({ request }) => {
      expect(request.body).to.deep.include({
        command_type: "slide.delete",
        operation_context: { slide_index: 0, source: "canonical-live" },
      });
    });
    cy.contains('[data-testid="agent-run"]', /approval/i, { timeout: 15_000 })
      .within(() => cy.contains("button", "Approve").should("be.enabled"));
  });
});
