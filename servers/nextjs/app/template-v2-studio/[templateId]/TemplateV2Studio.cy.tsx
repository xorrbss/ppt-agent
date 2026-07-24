import React from "react";

import TemplateV2Studio from "./TemplateV2Studio";

const templateId = "phase-one-template";

const unsupportedImage = {
  type: "image",
  data: "/app_data/images/hero.png",
  decorative: false,
  name: "hero",
  is_icon: false,
  position: { x: 420, y: 40 },
  size: { width: 320, height: 180 },
};

function layouts() {
  return {
    layouts: [
      {
        id: "title-slide",
        description: "Native editable title slide",
        components: [
          {
            id: "hero",
            description: "Editable hero title component",
            position: { x: 0, y: 0 },
            elements: [
              {
                type: "text",
                position: { x: 1, y: 1 },
                size: { width: 400, height: 80 },
                font: {
                  family: "Inter",
                  size: 28,
                  color: "#1d4ed8",
                  bold: true,
                },
                runs: [
                  { text: "Original " },
                  {
                    text: "title",
                    font: {
                      family: "Georgia",
                      size: 34,
                      color: "#dc2626",
                      bold: false,
                      italic: true,
                      underline: true,
                    },
                  },
                ],
                decorative: false,
                name: "title",
                min_length: 1,
                max_length: 120,
              },
              unsupportedImage,
            ],
          },
        ],
      },
    ],
  };
}

function response(payloadLayouts = layouts(), revision = 1) {
  return {
    id: templateId,
    name: "Phase 1 template",
    description: null,
    layouts: payloadLayouts,
    revision,
    updated_at: "2026-07-24T00:00:00Z",
  };
}

describe("TemplateV2Studio API integration", () => {
  it("edits a nested upstream envelope and saves its original shape and unknown fields", () => {
    const nestedLayouts = {
      layouts: {
        ...layouts(),
        future_nested_envelope_field: { retained: true },
      },
      future_top_level_field: "retained",
    };

    cy.intercept(
      "GET",
      `**/api/v1/ppt/structured-templates/${templateId}`,
      { statusCode: 200, body: response(nestedLayouts) }
    ).as("loadNestedTemplate");
    cy.intercept(
      "PATCH",
      `**/api/v1/ppt/structured-templates/${templateId}`,
      (request) => {
        request.reply({
          statusCode: 200,
          body: response(
            request.body.layouts,
            request.body.expected_revision + 1
          ),
        });
      }
    ).as("saveNestedTemplate");

    cy.mount(<TemplateV2Studio templateId={templateId} />);
    cy.wait("@loadNestedTemplate");
    cy.contains("button", "Add rectangle").click();
    cy.contains("button", "Save").click();

    cy.wait("@saveNestedTemplate")
      .its("request.body")
      .then((body) => {
        expect(body.layouts.future_top_level_field).to.equal("retained");
        expect(
          body.layouts.layouts.future_nested_envelope_field
        ).to.deep.equal({ retained: true });
        expect(body.layouts.layouts.layouts[0].components[0].elements).to.have
          .length(3);
      });
    cy.contains("Saved").should("be.visible");
  });

  it("fails closed without rendering or saving malformed upstream layouts", () => {
    let malformedSaveRequests = 0;
    cy.intercept(
      "GET",
      `**/api/v1/ppt/structured-templates/${templateId}`,
      {
        statusCode: 200,
        body: response({
          layouts: [{ id: "valid" }, "malformed"],
        }),
      }
    ).as("loadMalformedTemplate");
    cy.intercept(
      "PATCH",
      `**/api/v1/ppt/structured-templates/${templateId}`,
      (request) => {
        malformedSaveRequests += 1;
        request.reply({ statusCode: 500 });
      }
    );

    cy.mount(<TemplateV2Studio templateId={templateId} />);
    cy.wait("@loadMalformedTemplate");
    cy.contains("Template V2 Studio unavailable").should("be.visible");
    cy.contains("template_v2_upstream_layouts_invalid").should("be.visible");
    cy.contains("button", "Save").should("not.exist");
    cy.then(() => expect(malformedSaveRequests).to.equal(0));
  });

  it("round-trips unsupported elements through explicit GET/PATCH and serializes only one in-flight save", () => {
    cy.intercept(
      "GET",
      `**/api/v1/ppt/structured-templates/${templateId}`,
      { statusCode: 200, body: response() }
    ).as("loadTemplate");
    cy.intercept(
      "PATCH",
      `**/api/v1/ppt/structured-templates/${templateId}`,
      (request) => {
        request.reply({
          statusCode: 200,
          delay: 500,
          body: response(request.body.layouts, request.body.expected_revision + 1),
        });
      }
    ).as("saveTemplate");

    cy.mount(<TemplateV2Studio templateId={templateId} />);
    cy.wait("@loadTemplate");
    // Konva draws the unsupported image placeholder on a canvas, so assert
    // its presence via the inspector count and preserve it in the PATCH body.
    cy.get("dt")
      .contains(/^Elements$/)
      .next("dd")
      .should("have.text", "2");

    cy.contains("button", "Add rectangle").click();
    // A double-click happens before React has to render the disabled state.
    // The component's synchronous in-flight guard must still emit one PATCH.
    cy.contains("button", "Save").dblclick();
    cy.window().then((window) => {
      window.dispatchEvent(
        new KeyboardEvent("keydown", { key: "z", ctrlKey: true })
      );
    });
    cy.get("dt")
      .contains(/^Elements$/)
      .next("dd")
      .should("have.text", "3");

    cy.wait("@saveTemplate")
      .its("request.body")
      .then((body) => {
        expect(body.expected_revision).to.equal(1);
        const elements = body.layouts.layouts[0].components[0].elements;
        expect(elements[0].font).to.deep.equal({
          family: "Inter",
          size: 28,
          color: "#1d4ed8",
          bold: true,
        });
        expect(elements[0].runs).to.deep.equal([
          { text: "Original " },
          {
            text: "title",
            font: {
              family: "Georgia",
              size: 34,
              color: "#dc2626",
              bold: false,
              italic: true,
              underline: true,
            },
          },
        ]);
        expect(elements[1]).to.deep.equal(unsupportedImage);
        expect(elements[2]).to.include({ type: "container" });
      });
    cy.get("@saveTemplate.all").should("have.length", 1);
    cy.contains("Saved").should("be.visible");
  });

  it("keeps local edits and offers an explicit reload after a revision conflict", () => {
    cy.intercept(
      "GET",
      `**/api/v1/ppt/structured-templates/${templateId}`,
      { statusCode: 200, body: response() }
    ).as("loadTemplate");
    cy.intercept(
      "PATCH",
      `**/api/v1/ppt/structured-templates/${templateId}`,
      {
        statusCode: 409,
        delay: 200,
        body: {
          detail: {
            code: "template_v2_revision_conflict",
            expected_revision: 1,
            current_revision: 2,
          },
        },
      }
    ).as("conflictedSave");

    cy.mount(<TemplateV2Studio templateId={templateId} />);
    cy.wait("@loadTemplate");
    cy.contains("button", "Add rectangle").click();
    cy.contains("button", "Save").click();
    cy.wait("@conflictedSave")
      .its("request.body.expected_revision")
      .should("equal", 1);

    cy.contains("Reload server version").should("be.visible");
    cy.contains("Unsaved changes").should("be.visible");
    cy.get("dt")
      .contains(/^Elements$/)
      .next("dd")
      .should("have.text", "3");
  });

  it("wires sibling multi-selection to reorder, lock, group, and ungroup commands", () => {
    cy.intercept(
      "GET",
      `**/api/v1/ppt/structured-templates/${templateId}`,
      { statusCode: 200, body: response() }
    ).as("loadTemplate");

    cy.mount(<TemplateV2Studio templateId={templateId} />);
    cy.wait("@loadTemplate");
    cy.contains("button", "Add rectangle").click();

    cy.contains("button[aria-pressed]", /^title/).click();
    cy.contains("button[aria-pressed]", /^hero/).click({ ctrlKey: true });
    cy.contains("2 selected").should("be.visible");
    cy.contains("button", /^Group$/).should("be.enabled");
    cy.contains("button", "Bring to front").should("be.enabled");

    cy.contains("button", "Lock selected").click({ waitForAnimations: false });
    cy.contains("button", /^Group$/).should("be.disabled");
    cy.contains("button", "Bring to front").should("be.disabled");
    cy.contains("button", "Unlock selected").should("be.enabled");
    cy.get('[aria-label="Locked"]').should("have.length", 2);

    cy.contains("button", "Unlock selected").click({ waitForAnimations: false });
    cy.contains("button", "Bring to front").click({
      waitForAnimations: false,
    });
    cy.contains("button", "Bring to front").should("be.disabled");

    cy.contains("button", /^Group$/).click({ waitForAnimations: false });
    cy.contains("1 selected").should("be.visible");
    cy.contains("button", /^Ungroup$/)
      .should("be.enabled")
      .click({ waitForAnimations: false });
    cy.contains("2 selected").should("be.visible");
    cy.contains("button", /^Group$/).should("be.enabled");
  });

  it("disables direct manipulation controls across ancestor and subtree locks", () => {
    cy.intercept(
      "GET",
      `**/api/v1/ppt/structured-templates/${templateId}`,
      { statusCode: 200, body: response() }
    ).as("loadTemplate");

    cy.mount(<TemplateV2Studio templateId={templateId} />);
    cy.wait("@loadTemplate");
    cy.contains("button", "Add rectangle").click();

    cy.contains("button[aria-pressed]", /^title/).click();
    cy.contains("button[aria-pressed]", /^hero/).click({ ctrlKey: true });
    cy.contains("button", /^Group$/).click({ waitForAnimations: false });
    cy.contains("button", /^group/).should("have.attr", "aria-pressed", "true");

    cy.contains("button", "Lock selected").click({ waitForAnimations: false });
    cy.contains("button[aria-pressed]", /^title/).click();
    cy.contains("button", "Bring to front").should("be.disabled");
    cy.contains(
      "Locked elements cannot be transformed, reordered, or grouped."
    ).should("be.visible");

    cy.contains("button", /^group/).click();
    cy.contains("button", "Unlock selected").click({
      waitForAnimations: false,
    });
    cy.contains("button[aria-pressed]", /^title/).click();
    cy.contains("button", "Lock selected").click({ waitForAnimations: false });
    cy.contains("button", /^group/).click();
    cy.contains("button", "Bring to front").should("be.disabled");
    cy.contains("button", /^Ungroup$/).should("be.disabled");
  });
});
