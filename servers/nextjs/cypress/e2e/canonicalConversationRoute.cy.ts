const presentationId = "33333333-3333-4333-8333-333333333333";
const conversationId = "11111111-1111-4111-8111-111111111111";
const runId = "44444444-4444-4444-8444-444444444444";

type AxeRuntime = {
  run: (document: Document) => Promise<{
    violations: Array<{ id: string; nodes: Array<{ target: string[] }> }>;
  }>;
};

const deck = {
  id: presentationId,
  title: "Canonical conversation fixture",
  language: "en",
  n_slides: 0,
  layout: { name: "blank", ordered: true, slides: [] },
  slides: [],
};

describe("canonical conversation presentation route", () => {
  it("uses one conversation surface and refreshes the deck only after presentation.published", () => {
    let presentationReads = 0;
    let eventConnections = 0;
    cy.intercept("GET", `**/api/v1/ppt/presentation/${presentationId}`, (request) => {
      presentationReads += 1;
      request.reply({ statusCode: 200, body: deck });
    }).as("presentation");
    cy.intercept("GET", "**/api/v1/ppt/themes/all", { statusCode: 200, body: [] });
    cy.intercept("GET", "**/api/v1/ppt/presentation/*/generation-info", {
      statusCode: 200,
      body: { prompt: "", attachment_file_names: [], settings: {} },
    });
    cy.intercept("GET", "**/api/v1/ppt/workspace/capabilities*", {
      statusCode: 200,
      body: { supported: true, enabled: true, timeline: true, commands: true, events: true },
    });
    cy.intercept("POST", "**/api/v1/ppt/conversations", {
      statusCode: 200,
      body: { id: conversationId, presentation_id: presentationId, revision: 0 },
    }).as("conversation");
    cy.intercept("GET", `**/api/v1/ppt/conversations/${conversationId}/timeline*`, {
      statusCode: 200,
      body: {
        revision: 0,
        event_high_watermark: "0",
        items: [{
          item_type: "run",
          id: runId,
          position: 1,
          run_type: "presentation.edit",
          status: "approval_required",
          version: 1,
          actions: [{ id: "approve", enabled: true }, { id: "cancel", enabled: true }],
        }],
      },
    }).as("timeline");
    cy.intercept("POST", `**/api/v1/ppt/conversations/${conversationId}/runs/${runId}/actions`, {
      statusCode: 200,
      body: { conversation_id: conversationId, run_id: runId, status: "queued" },
    }).as("runAction");
    cy.intercept("GET", `**/api/v1/ppt/conversations/${conversationId}/events*`, (request) => {
      eventConnections += 1;
      const after = new URL(request.url).searchParams.get("after");
      if (after === "2") {
        request.reply({ statusCode: 200, headers: { "content-type": "text/event-stream" }, body: "" });
        return;
      }
      request.reply({
        statusCode: 200,
        headers: { "content-type": "text/event-stream" },
        body: `id: 2\nevent: conversation_event\ndata: ${JSON.stringify({
          schema_version: 1,
          id: "event-published",
          type: "presentation.published",
          durability: "durable",
          conversation_id: conversationId,
          run_id: runId,
          message_id: null,
          presentation_id: presentationId,
          cursor: "2",
          checkpoint_cursor: "2",
          occurred_at: "2026-08-11T00:00:00Z",
          data: { revision: 1 },
        })}\n\n`,
      });
    }).as("conversationEvents");

    cy.visit(`/presentation?id=${presentationId}`);
    cy.wait(["@presentation", "@conversation", "@timeline", "@conversationEvents"]);
    cy.get('[data-testid="conversation-surface"]').should("be.visible");
    cy.get('[data-testid="agent-run"]').should("contain.text", "approval_required");
    cy.get('[data-testid="operation-approval-alert"]')
      .should("have.attr", "role", "alert")
      .and("have.attr", "aria-live", "assertive");
    cy.get('[role="status"][aria-live="polite"]').should("have.length", 1);
    cy.readFile("node_modules/axe-core/axe.min.js", "utf8")
      .then((source: string) => cy.window().then((window) => {
        window.Function(source)();
        const pageAxe = (window as typeof window & { axe?: AxeRuntime }).axe;
        if (!pageAxe) throw new Error("axe-core did not attach to the application window");
        return pageAxe.run(window.document);
      }))
      .then((results) => {
        const violationSummary = results.violations
          .map((violation) => `${violation.id}: ${violation.nodes.map((node) => node.target.join(" > ")).join(", ")}`)
          .join("\n");
        expect(results.violations, violationSummary).to.have.length(0);
      });
    cy.get('[data-testid="run-action-approve"]').click();
    cy.wait("@runAction");
    cy.wrap(null).should(() => {
      expect(presentationReads).to.equal(2);
      expect(eventConnections).to.be.greaterThan(0);
    });
  });
});
