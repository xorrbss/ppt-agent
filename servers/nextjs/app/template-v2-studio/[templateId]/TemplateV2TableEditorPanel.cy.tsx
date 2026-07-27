import React, { useState } from "react";
import { mount } from "cypress/react";

import TemplateV2TableEditorPanel, {
  type TemplateV2TableEditorMutation,
} from "./TemplateV2TableEditorPanel";

function cell(text: string, id: string) {
  return {
    runs: [
      {
        text,
        font: { bold: true, family: "Inter" },
        future_run_field: { source: id },
      },
    ],
    alignment: "right",
    color: { color: "#123456", opacity: 0.75 },
    future_cell_field: { id },
  };
}

function table(rowCount = 2) {
  return {
    type: "table",
    name: "Revenue",
    stable_id: "table-1",
    decorative: false,
    min_rows: 1,
    max_rows: 40,
    min_columns: 2,
    max_columns: 8,
    columns: [cell("Quarter", "h1"), cell("Revenue", "h2")],
    rows: Array.from({ length: rowCount }, (_, index) => [
      cell(`Q${index + 1}`, `r${index + 1}c1`),
      cell(String(42 + index), `r${index + 1}c2`),
    ]),
    future_table_field: { retained: true },
  };
}

function Harness({
  initial = table(),
  onMutation,
}: {
  initial?: ReturnType<typeof table>;
  onMutation?: (
    replacement: Record<string, unknown>,
    mutation: TemplateV2TableEditorMutation,
  ) => void;
}) {
  const [element, setElement] = useState<Record<string, unknown>>(initial);
  return (
    <TemplateV2TableEditorPanel
      element={element}
      revision={7}
      disabled={false}
      onApply={(replacement, mutation) => {
        onMutation?.(replacement, mutation);
        setElement(replacement);
      }}
    />
  );
}

describe("TemplateV2TableEditorPanel", () => {
  it("previews before applying row and column structure changes with CAS metadata", () => {
    const onMutation = cy.stub().as("mutation");
    mount(<Harness onMutation={onMutation} />);

    cy.get('button[aria-label="Move row 2 up"]').click();
    cy.get('[aria-label="Table change preview"]').should(
      "contain.text",
      "move-row",
    );
    cy.get("@mutation").should("not.have.been.called");
    cy.contains("button", "Apply preview").click();

    cy.get("@mutation").should("have.been.calledOnce");
    cy.get("@mutation").then((value) => {
      const stub = value as unknown as Cypress.Agent<sinon.SinonStub>;
      const [replacement, metadata] = stub.firstCall.args as [
        ReturnType<typeof table>,
        TemplateV2TableEditorMutation,
      ];
      expect(replacement.stable_id).to.equal("table-1");
      expect(replacement.future_table_field).to.deep.equal({ retained: true });
      expect(replacement.rows[0][0].future_cell_field).to.deep.equal({
        id: "r2c1",
      });
      expect(replacement.rows[0][0].runs[0].future_run_field).to.deep.equal({
        source: "r2c1",
      });
      expect(metadata.expectedRevision).to.equal(7);
      expect(metadata.operation).to.equal("move-row");
      expect(metadata.idempotencyKey).to.equal(metadata.historyKey);
      expect(metadata.beforeDigest).to.match(/^fnv1a32:/);
      expect(metadata.afterDigest).to.match(/^fnv1a32:/);
    });

    cy.contains("button", "Add column").click();
    cy.get('[aria-label="Table change preview"]').should(
      "contain.text",
      "insert-column",
    );
    cy.contains("button", "Cancel").click();
    cy.contains("Preview canceled. No template data changed.").should(
      "be.visible",
    );
    cy.get("@mutation").should("have.been.calledOnce");
  });

  it("supports header conversion and transpose through explicit previews", () => {
    const onMutation = cy.stub().as("mutation");
    mount(<Harness onMutation={onMutation} />);

    cy.contains("button", "Promote first row to header").click();
    cy.contains("Preview · promote-first-row-to-header").should("be.visible");
    cy.contains("button", "Apply preview").click();

    cy.get("@mutation").then((value) => {
      const stub = value as unknown as Cypress.Agent<sinon.SinonStub>;
      const replacement = stub.firstCall.args[0] as ReturnType<typeof table>;
      expect(replacement.columns[0].future_cell_field).to.deep.equal({
        id: "r1c1",
      });
      expect(replacement.rows[0][0].future_cell_field).to.deep.equal({
        id: "h1",
      });
    });

    cy.contains("button", "Transpose").click();
    cy.contains("Preview · transpose").should("be.visible");
    cy.contains("button", "Apply preview").click();
    cy.get("@mutation").should("have.been.calledTwice");
  });

  it("imports quoted CSV, rejects malformed/ragged data, and bounds paste", () => {
    const onMutation = cy.stub().as("mutation");
    mount(<Harness onMutation={onMutation} />);

    cy.get('[aria-label="Delimited table data"]').type(
      'Region,Revenue{enter}"North, East",12{enter}South,18',
    );
    cy.contains("button", "Preview replace import").click();
    cy.contains("Preview · import-csv").should("be.visible");
    cy.get("@mutation").should("not.have.been.called");
    cy.contains("button", "Apply preview").click();

    cy.get("@mutation").then((value) => {
      const stub = value as unknown as Cypress.Agent<sinon.SinonStub>;
      const replacement = stub.firstCall.args[0] as ReturnType<typeof table>;
      expect(replacement.rows[0][0].runs[0].text).to.equal("North, East");
      expect(replacement.rows[0][0].future_cell_field).to.deep.equal({
        id: "r1c1",
      });
      expect(replacement.rows[0][0].runs[0].font).to.deep.equal({
        bold: true,
        family: "Inter",
      });
    });

    cy.get('[aria-label="Delimited table data"]')
      .clear()
      .type("a,b{enter}1");
    cy.contains("button", "Preview replace import").click();
    cy.get('[role="alert"]').should(
      "contain.text",
      "Every imported row must have the same number of cells.",
    );

    cy.get('[aria-label="Delimited table data"]')
      .clear()
      .type("1,2,3");
    cy.contains("button", "Preview bounded paste").click();
    cy.get('[role="alert"]').should(
      "contain.text",
      "The pasted range does not fit inside the current table.",
    );
  });

  it("keeps table-to-chart separate and suggests bounded long-table splits", () => {
    const onMutation = cy.stub().as("mutation");
    mount(<Harness initial={table(25)} onMutation={onMutation} />);

    cy.contains("Long table split suggested").should("be.visible");
    cy.contains("25 rows · 3 slide segments").should("be.visible");
    cy.get('[aria-label="Table chart title"]').type("Quarterly revenue");
    cy.contains("button", "Preview chart conversion").click();
    cy.contains("Preview · table-to-chart").should("be.visible");
    cy.contains("Categories").parent().should("contain.text", "25");
    cy.get("@mutation").should("not.have.been.called");
    cy.contains("button", "Apply preview").click();

    cy.get("@mutation").then((value) => {
      const stub = value as unknown as Cypress.Agent<sinon.SinonStub>;
      const [replacement, metadata] = stub.firstCall.args as [
        Record<string, unknown>,
        TemplateV2TableEditorMutation,
      ];
      expect(replacement.type).to.equal("chart");
      expect(replacement.chart_type).to.equal("bar");
      expect(replacement.title).to.equal("Quarterly revenue");
      expect(metadata.operation).to.equal("table-to-chart");
    });
  });
});
