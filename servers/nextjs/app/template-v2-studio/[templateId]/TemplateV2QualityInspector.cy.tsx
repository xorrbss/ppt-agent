import React, { useState } from "react";

import type { TemplateV2QualityApplyResult } from "@/lib/template-v2-quality-inspector";
import type { JsonRecord } from "@/lib/template-v2-studio";
import TemplateV2QualityInspector from "./TemplateV2QualityInspector";

function fixture(): JsonRecord {
  return {
    layouts: [
      {
        id: "quality-layout",
        description: "Quality UI fixture",
        components: [
          {
            id: "content",
            description: "Quality content",
            elements: [
              {
                type: "text",
                name: "title",
                decorative: false,
                runs: [
                  {
                    text: "A title that overflows",
                    font: {
                      size: 7,
                      color: "#777777",
                      vendor_font_token: "keep-me",
                    },
                  },
                ],
                fill: { color: "#888888" },
                max_length: 8,
                vendor_metadata: { keep: true },
              },
              {
                type: "chart",
                name: "trend",
                decorative: false,
                series: [
                  { name: "Actual", values: [1, 2] },
                  { name: "Plan", values: [2, 3] },
                ],
                legend: false,
              },
              {
                type: "table",
                name: "wide-table",
                decorative: false,
                columns: Array.from({ length: 9 }, (_, index) => ({
                  runs: [{ text: `Column ${index}` }],
                })),
                rows: [],
              },
              {
                type: "image",
                name: "legacy",
                decorative: false,
                raster_only: true,
                compatibility: { unsupported_reason: "legacy_effect" },
              },
            ],
          },
        ],
      },
    ],
    vendor_envelope: { preserve: true },
  };
}

function Harness({
  onApplied,
}: {
  onApplied(result: TemplateV2QualityApplyResult): void;
}) {
  const [layouts, setLayouts] = useState(fixture());
  return (
    <div className="w-[420px] bg-slate-900 p-4 text-slate-100">
      <TemplateV2QualityInspector
        layouts={layouts}
        revision={7}
        onApply={(result) => {
          onApplied(result);
          setLayouts(result.layouts);
        }}
      />
    </div>
  );
}

describe("TemplateV2QualityInspector", () => {
  it("keeps inspection separate and requires diff preview plus explicit apply", () => {
    const onApplied = cy.stub().as("onApplied");
    cy.mount(<Harness onApplied={onApplied} />);

    cy.get("@onApplied").should("not.have.been.called");
    cy.get('[data-testid="quality-inspection-run"]').click();
    for (const code of [
      "TEXT_OVERFLOW",
      "TEXT_BELOW_9PT",
      "TEXT_LOW_CONTRAST",
      "CHART_UNIT_UNSPECIFIED",
      "CHART_LEGEND_MISSING",
      "TABLE_TOO_MANY_COLUMNS",
      "ELEMENT_UNSUPPORTED",
      "ELEMENT_RASTER_ONLY",
    ]) {
      cy.get(`[data-testid="quality-finding-${code}"]`).should("exist");
    }
    cy.get('[data-testid="quality-finding-TEXT_OVERFLOW"]').should(
      "contain.text",
      "Review only"
    );
    cy.get("@onApplied").should("not.have.been.called");

    cy.get('[data-testid="quality-finding-TEXT_BELOW_9PT"]')
      .find("button")
      .click();
    cy.get('[data-testid="quality-fix-preview"]')
      .should("contain.text", "Before")
      .and("contain.text", "7")
      .and("contain.text", "After")
      .and("contain.text", "9");
    cy.get("@onApplied").should("not.have.been.called");
    cy.get('[data-testid="quality-fix-cancel"]').click();
    cy.get('[data-testid="quality-fix-preview"]').should("not.exist");
    cy.get("@onApplied").should("not.have.been.called");

    cy.get('[data-testid="quality-finding-TEXT_BELOW_9PT"]')
      .find("button")
      .click();
    cy.get('[data-testid="quality-fix-apply"]').click();
    cy.get("@onApplied")
      .should("have.been.calledOnce")
      .then((stub) => {
        const result = (stub as unknown as Cypress.Agent<sinon.SinonStub>)
          .firstCall.args[0] as TemplateV2QualityApplyResult;
        const title = (
          (((result.layouts.layouts as JsonRecord[])[0]
            .components as JsonRecord[])[0].elements as JsonRecord[])
        )[0];
        expect(((title.runs as JsonRecord[])[0].font as JsonRecord).size).to.eq(
          9
        );
        expect(title.vendor_metadata).to.deep.eq({ keep: true });
        expect(result.layouts.vendor_envelope).to.deep.eq({ preserve: true });
        expect(result.autosave).to.deep.eq({
          expected_revision: 7,
          idempotency_key: "quality:studio:7:0002",
        });
      });
  });
});
