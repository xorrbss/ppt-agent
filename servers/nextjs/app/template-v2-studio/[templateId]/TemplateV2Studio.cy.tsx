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

function settleSuccessfulPersistence() {
  // The debounced autosave may finish between querying the button and clicking
  // it. Waiting on the user-visible dirty state covers both the pending-save
  // and already-saved paths without racing the disabled button transition.
  cy.contains("Unsaved changes").should("not.exist");
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
    // Re-scheduling the same immutable snapshot while it is in flight must not
    // duplicate the PATCH.
    cy.contains("button", "Save").dblclick();
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

  it("edits chart data and safe image assets through the autosave contract", () => {
    const editableLayouts = layouts();
    editableLayouts.layouts[0].components[0].elements.push({
      type: "chart",
      name: "revenue",
      position: { x: 40, y: 260 },
      size: { width: 420, height: 240 },
      chart_type: "bar",
      title: "Revenue",
      categories: ["Q1", "Q2"],
      series: [
        {
          name: "Actual",
          values: [10, 20],
          future_series_field: { retained: true },
        },
      ],
      future_chart_field: "retained",
    });
    cy.intercept(
      "GET",
      `**/api/v1/ppt/structured-templates/${templateId}`,
      { statusCode: 200, body: response(editableLayouts) }
    ).as("loadEditableTemplate");
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
    ).as("saveEditableTemplate");

    cy.mount(<TemplateV2Studio templateId={templateId} />);
    cy.wait("@loadEditableTemplate");
    cy.contains("button[aria-pressed]", /^revenue/).click({
      waitForAnimations: false,
    });
    cy.contains("label", "Chart title")
      .find("input")
      .clear({ waitForAnimations: false })
      .type("Bookings", { waitForAnimations: false });
    cy.contains("label", "Category 2")
      .find("input")
      .clear({ waitForAnimations: false })
      .type("FY26 Q2", { waitForAnimations: false });
    cy.contains("label", "Series 1 name")
      .find("input")
      .clear({ waitForAnimations: false })
      .type("Forecast", { waitForAnimations: false });
    cy.contains("label", "Series 1, value 2")
      .find("input")
      .type("{selectall}42.5", { waitForAnimations: false });

    cy.contains("button[aria-pressed]", /^hero/).click({
      waitForAnimations: false,
    });
    cy.contains("label", "Asset source")
      .find("textarea")
      .type("{selectall}/app_data/images/updated.png", {
        waitForAnimations: false,
      });
    cy.contains("label", "Asset fit")
      .find("select")
      .select("contain", { force: true });
    cy.contains("button", "Save").click({ waitForAnimations: false });

    cy.wait("@saveEditableTemplate")
      .its("request.body.layouts.layouts.0.components.0.elements")
      .then((elements) => {
        expect(elements[1]).to.deep.equal({
          ...unsupportedImage,
          data: "/app_data/images/updated.png",
          fit: "contain",
        });
        expect(elements[2]).to.deep.equal({
          type: "chart",
          name: "revenue",
          position: { x: 40, y: 260 },
          size: { width: 420, height: 240 },
          chart_type: "bar",
          title: "Bookings",
          categories: ["Q1", "FY26 Q2"],
          series: [
            {
              name: "Forecast",
              values: [10, 42.5],
              future_series_field: { retained: true },
            },
          ],
          future_chart_field: "retained",
        });
      });
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
    );

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
    cy.get('[role="application"]')
      .should("have.attr", "data-konva-move-enabled", "false")
      .and("have.attr", "data-konva-resize-enabled", "false")
      .and("have.attr", "data-konva-rotate-enabled", "false");

    cy.contains("button", "Unlock selected").click({ waitForAnimations: false });
    cy.get('[role="application"]')
      .should("have.attr", "data-konva-move-enabled", "true")
      .and("have.attr", "data-konva-resize-enabled", "true")
      .and("have.attr", "data-konva-rotate-enabled", "true");
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
    settleSuccessfulPersistence();
  });

  it("disables direct manipulation controls across ancestor and subtree locks", () => {
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
          body: response(
            request.body.layouts,
            request.body.expected_revision + 1
          ),
        });
      }
    );

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
    settleSuccessfulPersistence();
  });

  it("keeps native text undo isolated while supporting global undo and redo variants", () => {
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
          body: response(
            request.body.layouts,
            request.body.expected_revision + 1
          ),
        });
      }
    );

    cy.mount(<TemplateV2Studio templateId={templateId} />);
    cy.wait("@loadTemplate");
    cy.contains("button", "Add rectangle").click();
    cy.get("dt").contains(/^Elements$/).next("dd").should("have.text", "3");

    cy.window().then((window) => {
      window.dispatchEvent(
        new KeyboardEvent("keydown", { key: "z", ctrlKey: true })
      );
    });
    cy.get("dt").contains(/^Elements$/).next("dd").should("have.text", "2");

    cy.window().then((window) => {
      window.dispatchEvent(
        new KeyboardEvent("keydown", { key: "y", ctrlKey: true })
      );
    });
    cy.get("dt").contains(/^Elements$/).next("dd").should("have.text", "3");

    cy.contains("button[aria-pressed]", /^title/).click();
    cy.get("textarea").first().trigger("keydown", {
      key: "z",
      ctrlKey: true,
      waitForAnimations: false,
    });
    cy.get("dt").contains(/^Elements$/).next("dd").should("have.text", "3");
    settleSuccessfulPersistence();
  });

  it("nudges and clears the canvas selection from the keyboard", () => {
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
          body: response(
            request.body.layouts,
            request.body.expected_revision + 1
          ),
        });
      }
    );

    cy.mount(<TemplateV2Studio templateId={templateId} />);
    cy.wait("@loadTemplate");
    cy.contains("button[aria-pressed]", /^title/).click();
    cy.get('[aria-label="X geometry"]').should("have.value", "1");

    cy.get('[role="application"]').focus().trigger("keydown", {
      key: "ArrowRight",
      waitForAnimations: false,
    });
    cy.get('[aria-label="X geometry"]').should("have.value", "2");

    cy.get('[role="application"]').trigger("keydown", {
      key: "ArrowDown",
      shiftKey: true,
      waitForAnimations: false,
    });
    cy.get('[aria-label="Y geometry"]').should("have.value", "9");

    cy.get('[role="application"]').trigger("keydown", {
      key: "ArrowRight",
      ctrlKey: true,
      waitForAnimations: false,
    });
    cy.get('[aria-label="X geometry"]').should("have.value", "2");

    cy.get('[role="application"]').trigger("keydown", {
      key: "Escape",
      waitForAnimations: false,
    });
    cy.contains("0 selected").should("be.visible");
    settleSuccessfulPersistence();
  });

  it("aligns and distributes sibling selections without losing upstream fields", () => {
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
          body: response(
            request.body.layouts,
            request.body.expected_revision + 1
          ),
        });
      }
    ).as("saveAlignedTemplate");

    cy.mount(<TemplateV2Studio templateId={templateId} />);
    cy.wait("@loadTemplate");
    cy.contains("button", "Add rectangle").click();
    cy.contains("button[aria-pressed]", /^title/).click();
    cy.contains("button[aria-pressed]", /^hero/).click({ ctrlKey: true });
    cy.contains("button[aria-pressed]", /^container/).click({ ctrlKey: true });

    cy.contains("button", "Align top").click({ waitForAnimations: false });
    cy.contains("button", "Distribute horizontally").click({
      waitForAnimations: false,
    });
    cy.wait(0);
    cy.contains("button", "Save").click();

    cy.wait("@saveAlignedTemplate")
      .its("request.body.layouts.layouts.0.components.0.elements")
      .then((elements) => {
        expect(elements).to.have.length(3);
        expect(elements.map((element: { position: { y: number } }) =>
          element.position.y
        )).to.deep.equal([1, 1, 1]);
        expect(elements[2].position.x).to.equal(290.5);
        expect(elements[1]).to.include({
          decorative: false,
          is_icon: false,
          name: "hero",
        });
        expect(elements[1].data).to.equal("/app_data/images/hero.png");
      });
  });

  it("queues edits made during an autosave and advances the revision once per snapshot", () => {
    const savedElementCounts: number[] = [];
    const expectedRevisions: number[] = [];
    cy.intercept(
      "GET",
      `**/api/v1/ppt/structured-templates/${templateId}`,
      { statusCode: 200, body: response() }
    ).as("loadTemplate");
    cy.intercept(
      "PATCH",
      `**/api/v1/ppt/structured-templates/${templateId}`,
      (request) => {
        expectedRevisions.push(request.body.expected_revision);
        savedElementCounts.push(
          request.body.layouts.layouts[0].components[0].elements.length
        );
        request.reply({
          statusCode: 200,
          delay: 600,
          body: response(
            request.body.layouts,
            request.body.expected_revision + 1
          ),
        });
      }
    ).as("queuedAutosave");

    cy.mount(<TemplateV2Studio templateId={templateId} />);
    cy.wait("@loadTemplate");
    cy.contains("button", "Add rectangle").click();
    cy.wait(850);
    cy.contains("button", "Add rectangle").should("be.enabled").click();

    cy.wait("@queuedAutosave");
    cy.wait("@queuedAutosave");
    cy.then(() => {
      expect(expectedRevisions).to.deep.equal([1, 2]);
      expect(savedElementCounts).to.deep.equal([3, 4]);
    });
    cy.contains("Saved automatically").should("be.visible");
  });

  it("flushes a debounced snapshot on pagehide", () => {
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
          body: response(
            request.body.layouts,
            request.body.expected_revision + 1
          ),
        });
      }
    ).as("pagehideSave");

    cy.mount(<TemplateV2Studio templateId={templateId} />);
    cy.wait("@loadTemplate");
    cy.contains("button", "Add rectangle").click();
    cy.window().then((window) => {
      window.dispatchEvent(new Event("pagehide"));
    });
    cy.wait("@pagehideSave")
      .its("request.body.layouts.layouts.0.components.0.elements")
      .should("have.length", 3);
  });
});
