import React from "react";

import {
  templateV2MatrixLayouts,
  templateV2MatrixResponse,
  templateV2MatrixTemplateId,
} from "../../../cypress/fixtures/template-v2-matrix/templateV2MatrixFixtures";
import TemplateV2Studio from "./TemplateV2Studio";
import { mountWithStudioI18n } from "./studioI18nTestUtils.cy";

function mountMatrix(persistedLayouts = templateV2MatrixLayouts(), revision = 1) {
  cy.intercept(
    "GET",
    `**/api/v1/ppt/structured-templates/${templateV2MatrixTemplateId}`,
    (request) => request.reply({ statusCode: 200, body: templateV2MatrixResponse(persistedLayouts, revision) })
  ).as("loadMatrix");
  mountWithStudioI18n(<TemplateV2Studio templateId={templateV2MatrixTemplateId} />);
  cy.wait("@loadMatrix");
}

describe("Template V2 source-backed E2E matrix", () => {
  beforeEach(() => {
    cy.intercept("GET", "**/api/upload-limits", {
      statusCode: 200,
      body: {
        document: { bytes: 52_428_800, mb: 50, hardMaxMb: 512 },
        image: { bytes: 10_485_760, mb: 10, hardMaxMb: 64 },
        requestTotal: { bytes: 52_428_800, mb: 50, hardMaxMb: 512 },
        reason: "Template V2 matrix fixture limit contract.",
      },
    });
  });

  it("keeps nested selection remapped under ancestor and subtree locks, with an accessible portal toolbar", () => {
    mountMatrix();

    // The image route is represented by the floating portal; groups themselves
    // intentionally use the inspector actions below.
    cy.contains("button[aria-pressed]", /^hero\s*\(4\)$/).click();
    cy.get('[role="toolbar"][aria-label="Selection toolbar"]')
      .should("be.visible")
      .within(() => cy.get('button[aria-label="Lock selection"]').should("have.attr", "aria-pressed", "false"));

    cy.contains("button[aria-pressed]", /^group/).click();
    cy.contains("button", "Lock selected").click({ force: true, waitForAnimations: false });

    // A child is blocked by its locked group ancestor.
    cy.contains("button[aria-pressed]", /^group-card\s*\(2 \/ children \/ 0\)$/).click();
    cy.contains("button", "Bring to front").should("be.disabled");
    cy.contains("Locked elements cannot be transformed, reordered, or grouped.").should("be.visible");

    // The group can be unlocked, then its child locked. Selecting the ancestor
    // must still be blocked by a locked subtree.
    cy.contains("button[aria-pressed]", /^group/).click();
    cy.contains("button", "Unlock selected").click({ force: true, waitForAnimations: false });
    cy.contains("button[aria-pressed]", /^group-card\s*\(2 \/ children \/ 0\)$/).click();
    cy.contains("button", "Lock selected").click({ force: true, waitForAnimations: false });
    cy.contains("button[aria-pressed]", /^group/).click();
    cy.contains("button", "Ungroup").should("be.disabled");

  });

  it("caps command history at forty, coalesces continuous edits, serializes optimistic saves, and reloads persisted selection data", () => {
    let persisted = templateV2MatrixLayouts();
    let revision = 1;
    const expectedRevisions: number[] = [];
    cy.intercept(
      "GET",
      `**/api/v1/ppt/structured-templates/${templateV2MatrixTemplateId}`,
      (request) => request.reply({ statusCode: 200, body: templateV2MatrixResponse(persisted, revision) })
    ).as("loadMatrix");
    cy.intercept(
      "PATCH",
      `**/api/v1/ppt/structured-templates/${templateV2MatrixTemplateId}`,
      (request) => {
        expect(request.body.layouts.matrix_fixture_contract).to.equal(
          "template-v2-e2e-matrix-v1"
        );
        expect(
          request.body.layouts.layouts[0].components[0].elements[0]
            .upstream_element_extension
        ).to.equal("text-vNext");
        expectedRevisions.push(request.body.expected_revision);
        persisted = request.body.layouts;
        revision += 1;
        request.reply({
          statusCode: 200,
          delay: expectedRevisions.length === 1 ? 350 : 0,
          body: templateV2MatrixResponse(persisted, revision),
        });
      }
    ).as("saveMatrix");

    mountWithStudioI18n(<TemplateV2Studio templateId={templateV2MatrixTemplateId} />);
    cy.wait("@loadMatrix");
    // Forty-five discrete commands exercise the user-visible forty-entry
    // history ceiling.
    for (let index = 0; index < 45; index += 1) {
      cy.contains("button", "Add rectangle").click({ waitForAnimations: false });
    }
    cy.get('button[aria-label="Undo (Ctrl or Command plus Z)"]').should("be.enabled");
    for (let index = 0; index < 40; index += 1) {
      cy.window().then((window) => window.dispatchEvent(new KeyboardEvent("keydown", { key: "z", ctrlKey: true })));
    }
    cy.get('button[aria-label="Undo (Ctrl or Command plus Z)"]').should("be.disabled");
    cy.get('button[aria-label="Redo (Ctrl or Command plus Shift plus Z, or Ctrl plus Y)"]').should("be.enabled");
    cy.window().then((window) => window.dispatchEvent(new KeyboardEvent("keydown", { key: "y", ctrlKey: true })));

    // Character-level text updates share a continuous history entry: one undo
    // restores the original title, and redo restores the complete replacement.
    cy.contains("button[aria-pressed]", /^title\s*\(0\)$/).click();
    cy.get('textarea[aria-label$=" content"]')
      .first()
      .type("{selectall}Matrix continuous edit", { waitForAnimations: false });
    cy.get('button[aria-label="Undo (Ctrl or Command plus Z)"]').click({ force: true });
    cy.contains("button[aria-pressed]", /^title\s*\(0\)$/).click();
    cy.get('textarea[aria-label$=" content"]').first().should("have.value", "Renderer contract");
    cy.get('button[aria-label="Redo (Ctrl or Command plus Shift plus Z, or Ctrl plus Y)"]').click({ force: true });
    cy.contains("button[aria-pressed]", /^title\s*\(0\)$/).click();
    cy.get('textarea[aria-label$=" content"]').first().should("have.value", "Matrix continuous edit");

    // Two edits on either side of the first in-flight save must become two
    // ordered, optimistic PATCHes rather than clobbering the later snapshot.
    cy.wait(850);
    cy.contains("button", "Add rectangle").click({ waitForAnimations: false });
    cy.wait("@saveMatrix");
    cy.wait("@saveMatrix");
    cy.then(() => expect(expectedRevisions).to.deep.equal([1, 2]));

    mountWithStudioI18n(<div data-testid="matrix-unmounted" />);
    cy.get('[data-testid="template-v2-selection-toolbar"]').should("not.exist");
    mountWithStudioI18n(<TemplateV2Studio templateId={templateV2MatrixTemplateId} />);
    cy.wait("@loadMatrix");
    cy.contains("button", "Add rectangle").should("be.enabled");
    cy.contains("button[aria-pressed]", /^title\s*\(0\)$/).click();
    cy.get('textarea[aria-label$=" content"]').first().should("have.value", "Matrix continuous edit");
    cy.contains("Saved automatically").should("not.exist");
  });
});
