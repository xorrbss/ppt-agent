import React from "react";

import TemplateV2PptxImportPanel from "./TemplateV2PptxImportPanel";

const importId = "3f979bee-ab86-48ac-83cf-fc689364367e";

function importResponse(
  state: "queued" | "review_required" | "confirmed",
  revision: number
) {
  return {
    id: importId,
    task_id: "task-safe-import",
    requested_template_id: "new-imported-template",
    draft_template_id:
      state === "confirmed" ? "new-imported-template" : null,
    confirmed_template_id:
      state === "confirmed" ? "new-imported-template" : null,
    state,
    revision,
    source_filename: "review-source.pptx",
    source_size_bytes: 4096,
    source_sha256: "a".repeat(64),
    pipeline_version: "template-v2-pptx-ooxml-v1",
    attempt_number: state === "queued" ? 0 : 1,
    lease_expires_at: null,
    heartbeat_at: null,
    source_retention_expires_at: null,
    source_cleanup_attempted_at: null,
    source_deleted_at: null,
    manifest: {},
    analysis_result:
      state === "queued"
        ? null
        : {
            provider: {
              id: "deterministic-ooxml-static",
              status: "available",
              external_ai: false,
            },
            preview: { status: "not_provided" },
            render: { status: "not_run" },
            candidates: {
              slides: [
                {
                  shapes: [
                    {
                      source_id: "shape-1",
                      name: "Title",
                      kind: "text",
                      x: 10,
                      y: 20,
                      width: 300,
                      height: 40,
                      confidence: 0.92,
                    },
                    {
                      source_id: "shape-7",
                      name: "Chart",
                      kind: "unsupported",
                      x: 400,
                      y: 100,
                      width: 200,
                      height: 160,
                      confidence: 0,
                      unsupported_reason: "unsupported_graphic_frame",
                    },
                  ],
                },
              ],
            },
            summary: {
              slide_count: 2,
              shape_count: 7,
              unsupported_shape_count: 1,
              visual_fidelity_status: "not_evaluated",
              review_required: true,
            },
          },
    repeat_suggestions:
      state === "queued"
        ? []
        : [
            {
              id: "repeat-safe",
              axis: "horizontal",
              source_ids: ["shape-1", "shape-2"],
              confidence: 0.8,
            },
          ],
    confirmed_at: state === "confirmed" ? "2026-07-25T00:00:00Z" : null,
    cancelled_at: null,
    task_status: "completed",
    task_message:
      state === "confirmed"
        ? "Template V2 created after explicit confirmation"
        : state === "review_required"
          ? "PPTX analysis complete; explicit confirmation required"
          : "Queued for private PPTX validation",
    task_error: null,
    created_at: "2026-07-25T00:00:00Z",
    updated_at: "2026-07-25T00:00:00Z",
  };
}

describe("TemplateV2PptxImportPanel", () => {
  it("stops at review and creates a template only after explicit confirmation", () => {
    cy.intercept(
      "POST",
      "**/api/v1/ppt/structured-templates/imports",
      (request) => {
        expect(request.headers).to.have.property("idempotency-key");
        request.reply({ statusCode: 202, body: importResponse("queued", 1) });
      }
    ).as("uploadImport");
    cy.intercept(
      "GET",
      `**/api/v1/ppt/structured-templates/imports/${importId}`,
      { statusCode: 200, body: importResponse("review_required", 2) }
    ).as("pollImport");
    cy.intercept(
      "POST",
      `**/api/v1/ppt/structured-templates/imports/${importId}/confirm`,
      (request) => {
        expect(request.body).to.deep.equal({ expected_revision: 2 });
        request.reply({
          statusCode: 200,
          body: importResponse("confirmed", 3),
        });
      }
    ).as("confirmImport");
    cy.stub(window, "confirm").returns(true).as("explicitConfirm");

    cy.mount(
      <TemplateV2PptxImportPanel currentTemplateId="existing-template" />
    );
    cy.contains("button", "Open reviewed PPTX import").click();
    cy.contains("label", "New template ID")
      .find("input")
      .clear()
      .type("new-imported-template");
    cy.contains("label", "PPTX source")
      .find("input")
      .selectFile(
        {
          contents: Cypress.Buffer.from("fixture content"),
          fileName: "review-source.pptx",
          mimeType:
            "application/vnd.openxmlformats-officedocument.presentationml.presentation",
          lastModified: 1,
        },
        { force: true }
      );
    cy.contains("button", "Upload and analyze").click();
    cy.wait("@uploadImport");

    cy.wait("@pollImport");
    cy.contains("PPTX analysis complete; explicit confirmation required");
    cy.contains("deterministic-ooxml-static");
    cy.contains("External AI").parent().contains("not used");
    cy.contains("Repeat-block suggestions (not applied)");
    cy.contains("summary", "Review candidate differences (2)").click();
    cy.contains("td", "Editable text");
    cy.contains("td", "Manual review");
    cy.contains("td", "92%");
    cy.get("@confirmImport.all").should("have.length", 0);

    cy.contains("button", "Confirm and create new Template V2").click();
    cy.get("@explicitConfirm").should("have.been.calledOnce");
    cy.wait("@confirmImport");
    cy.contains("a", "Open confirmed template").should(
      "have.attr",
      "href",
      "/template-v2-studio/new-imported-template"
    );
  });
});
