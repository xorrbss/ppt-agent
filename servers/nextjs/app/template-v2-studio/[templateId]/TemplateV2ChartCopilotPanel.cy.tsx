import React from "react";

import TemplateV2ChartCopilotPanel from "./TemplateV2ChartCopilotPanel";

function chart(overrides: Record<string, unknown> = {}) {
  return {
    type: "chart",
    chart_type: "bar",
    categories: ["Q1", "Q2"],
    series: [
      {
        name: "Revenue",
        values: [10, 12],
        future_series: { retained: true },
      },
      { name: "Profit", values: [2, 3] },
    ],
    title: "Quarterly results",
    legend: true,
    x_axis: true,
    y_axis: true,
    future_chart: { retained: true },
    ...overrides,
  };
}

describe("TemplateV2ChartCopilotPanel", () => {
  it("stages strict controls and series edits, previews, cancels, and applies losslessly", () => {
    const applied: Array<{ chart: Record<string, unknown>; historyKey: string }> =
      [];
    cy.mount(
      <TemplateV2ChartCopilotPanel
        element={chart()}
        disabled={false}
        onApply={(next, historyKey) =>
          applied.push({ chart: next, historyKey })
        }
      />,
    );

    cy.get('[aria-label="Chart copilot title"]').clear().type("Board results");
    cy.get('[aria-label="Chart copilot Show legend"]').uncheck();
    cy.get('[aria-label="Chart copilot series 1 name"]')
      .clear()
      .type("Net revenue");
    cy.get('[aria-label="Move chart series 2 up"]').click();
    cy.contains("button", "Preview draft").click();
    cy.get('[aria-label="Chart copilot preview"]')
      .should("contain.text", "title")
      .and("contain.text", "series");
    cy.contains("button", "Cancel").click();
    cy.wrap(applied).should("have.length", 0);
    cy.get('[aria-label="Chart copilot title"]').should(
      "have.value",
      "Quarterly results",
    );

    cy.get('[aria-label="Chart copilot title"]').clear().type("Board results");
    cy.get('[aria-label="Chart copilot series 1 value 1"]').clear().type("42");
    cy.contains("button", "Preview draft").click();
    cy.contains("button", "Apply chart patch").click();

    cy.wrap(applied).should("have.length", 1);
    cy.wrap(applied).then(([result]) => {
      expect(result.historyKey).to.match(/^chart-copilot-/);
      expect(result.chart.title).to.equal("Board results");
      expect(result.chart.future_chart).to.deep.equal({ retained: true });
      const series = result.chart.series as Array<Record<string, unknown>>;
      expect(series[0].future_series).to.deep.equal({ retained: true });
      expect(series[0].values).to.deep.equal([42, 12]);
    });
  });

  it("validates bounded imports and stages imported table data before apply", () => {
    const applied: Record<string, unknown>[] = [];
    cy.mount(
      <TemplateV2ChartCopilotPanel
        element={chart()}
        disabled={false}
        onApply={(next) => applied.push(next)}
      />,
    );

    cy.get('[aria-label="Chart copilot import data"]').type(
      "Category,Revenue{enter}Q1,nope",
    );
    cy.contains("button", "Validate and stage import").click();
    cy.get('[role="alert"]').should(
      "contain.text",
      "template_v2_chart_copilot_invalid_import",
    );
    cy.get('[aria-label="Chart copilot import data"]')
      .clear()
      .type("Category,Revenue,Profit{enter}2025,20,4{enter}2026,30,6");
    cy.contains("button", "Validate and stage import").click();
    cy.get('[role="status"]').should(
      "contain.text",
      "Imported 2 categories and 2 series",
    );
    cy.contains("button", "Preview draft").click();
    cy.get('[aria-label="Chart copilot preview"]').should(
      "contain.text",
      "categories",
    );
    cy.contains("button", "Apply chart patch").click();
    cy.wrap(applied).then(([result]) => {
      expect(result.categories).to.deep.equal(["2025", "2026"]);
      expect(result.series).to.deep.equal([
        {
          name: "Revenue",
          values: [20, 30],
          future_series: { retained: true },
        },
        { name: "Profit", values: [4, 6] },
      ]);
      expect(result.future_chart).to.deep.equal({ retained: true });
    });
  });

  it("uses deterministic recommendations and fails closed for radar axes", () => {
    cy.mount(
      <TemplateV2ChartCopilotPanel
        element={chart({
          chart_type: "radar",
          x_axis: undefined,
          y_axis: undefined,
        })}
        disabled={false}
        onApply={() => undefined}
      />,
    );

    cy.contains("Axis and grid controls are incompatible with radar").should(
      "be.visible",
    );
    cy.get('[aria-label="Chart copilot Show X axis"]').should("be.disabled");
    cy.get('[aria-label="Chart copilot Axis color"]').should("be.disabled");

    cy.contains("button", "Stage deterministic recommendation").click();
    cy.get('[role="status"]').should(
      "contain.text",
      "multi_series_comparison",
    );
    cy.get('[aria-label="Chart copilot chart type"]').should(
      "have.value",
      "bar",
    );
    cy.contains("button", "Preview draft").click();
    cy.get('[aria-label="Chart copilot preview"]').should(
      "contain.text",
      "chart_type",
    );
  });
});
