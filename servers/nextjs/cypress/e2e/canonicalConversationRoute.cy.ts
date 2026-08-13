const presentationId = "33333333-3333-4333-8333-333333333333";
const operationId = "44444444-4444-4444-8444-444444444444";

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
  it("uses one workspace conversation surface and refreshes only after a durable operation succeeds", () => {
    let presentationReads = 0;
    let eventConnections = 0;
    const operation = (status: "approval_required" | "succeeded") => ({
      anchor_type: "operation",
      id: operationId,
      operation_id: operationId,
      position: 1,
      operation_type: "presentation.edit",
      title: "Edit presentation",
      status,
      version: status === "succeeded" ? 2 : 1,
      progress: status === "succeeded" ? 100 : 0,
      allowed_actions: status === "approval_required"
        ? [
            { id: "approve", label: "Approve", variant: "primary", enabled: true },
            { id: "cancel", label: "Cancel", variant: "secondary", enabled: true },
          ]
        : [],
      events: [],
      ...(status === "succeeded" ? { result: { revision: 1 } } : {}),
    });
    const timeline = (status: "approval_required" | "succeeded") => ({
      schema_version: 1,
      revision: status === "succeeded" ? 1 : 0,
      event_high_watermark: status === "succeeded" ? "2" : "0",
      has_more: false,
      items: [operation(status)],
    });
    cy.intercept("GET", `**/api/v1/ppt/presentation/${presentationId}`, (request) => {
      presentationReads += 1;
      request.reply({ statusCode: 200, body: deck });
    }).as("presentation");
    cy.intercept("GET", "**/api/v1/ppt/themes/all", { statusCode: 200, body: [] });
    cy.intercept("GET", "**/api/v1/ppt/presentation/*/generation-info", {
      statusCode: 200,
      body: { prompt: "", attachment_file_names: [], settings: {} },
    });
    cy.intercept("GET", "**/api/v1/ppt/presentation/*/remake/previews", {
      statusCode: 200,
      body: [],
    });
    cy.intercept("GET", "**/api/v1/ppt/workspace/capabilities*", {
      statusCode: 200,
      body: { supported: true, enabled: true, timeline: true, commands: true, events: true },
    });
    cy.intercept("GET", `**/api/v1/ppt/workspace/presentations/${presentationId}/timeline*`, {
      statusCode: 200,
      body: timeline("approval_required"),
    }).as("timeline");
    cy.intercept("POST", `**/api/v1/ppt/workspace/operations/${operationId}/actions`, {
      statusCode: 200,
      body: timeline("succeeded"),
    }).as("operationAction");
    cy.intercept("GET", `**/api/v1/ppt/workspace/presentations/${presentationId}/events*`, (request) => {
      eventConnections += 1;
      const url = new URL(request.url);
      if (url.searchParams.get("stream") === "1") {
        request.reply({
          statusCode: 200,
          headers: { "content-type": "text/event-stream" },
          body: "",
        });
        return;
      }
      request.reply({
        statusCode: 200,
        body: { schema_version: 1, events: [], event_high_watermark: "0" },
      });
    }).as("workspaceEvents");

    cy.visit(`/presentation?id=${presentationId}`);
    cy.wait(["@presentation", "@timeline", "@workspaceEvents"]);
    cy.get('[data-testid="workspace-timeline-scroll"]').should("be.visible");
    cy.get(`[data-operation-id="${operationId}"]`).should("be.visible");
    cy.get('[data-testid="operation-approval-alert"]')
      .should("have.attr", "role", "alert")
      .and("have.attr", "aria-live", "assertive");
    cy.get(
      '[data-testid="workspace-timeline-scroll"] [role="status"][aria-live="polite"]',
    ).should("have.length", 1);
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
    cy.get('[data-testid="operation-approve"]').click();
    cy.wait("@operationAction");
    cy.wrap(null).should(() => {
      expect(presentationReads).to.equal(2);
      expect(eventConnections).to.be.greaterThan(0);
    });
  });
});
