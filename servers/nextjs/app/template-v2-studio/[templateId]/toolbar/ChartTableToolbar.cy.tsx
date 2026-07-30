import React, { useState } from "react";

import type { TemplateV2ToolbarRecord } from "@/lib/template-v2-toolbar";
import ChartToolbar from "./ChartToolbar";
import TableToolbar from "./TableToolbar";
import type { TemplateV2ToolbarPatch } from "./types";

type PatchOptions = { historyKey?: string; continuous?: boolean };

function setReactInputValue(ariaLabel: string, value: string) {
  cy.get(`input[aria-label="${ariaLabel}"]`).then(($input) => {
    const input = $input[0] as HTMLInputElement;
    const setter = Object.getOwnPropertyDescriptor(
      HTMLInputElement.prototype,
      "value"
    )?.set;
    expect(setter, "native input value setter").to.be.a("function");
    setter?.call(input, value);
    input.dispatchEvent(new Event("input", { bubbles: true }));
  });
}

function applyAtPath(
  root: TemplateV2ToolbarRecord,
  path: Array<string | number>,
  value: unknown
): TemplateV2ToolbarRecord {
  const result = { ...root };
  let cursor: Record<string | number, unknown> | unknown[] = result;
  path.forEach((segment, index) => {
    if (index === path.length - 1) {
      cursor[segment] = value;
      return;
    }
    const current = cursor[segment];
    const next = Array.isArray(current)
      ? [...current]
      : current && typeof current === "object"
        ? { ...(current as Record<string, unknown>) }
        : typeof path[index + 1] === "number"
          ? []
          : {};
    cursor[segment] = next;
    cursor = next;
  });
  return result;
}

function StatefulChartToolbar({
  initialElement,
  observedPatch,
}: {
  initialElement: TemplateV2ToolbarRecord;
  observedPatch: (
    patches: TemplateV2ToolbarPatch[],
    options?: PatchOptions
  ) => void;
}) {
  const [element, setElement] = useState(initialElement);

  function onPatch(
    patches: TemplateV2ToolbarPatch[],
    options?: PatchOptions
  ) {
    observedPatch(patches, options);
    setElement((current) =>
      patches.reduce(
        (next, patch) => applyAtPath(next, patch.path, patch.value),
        current
      )
    );
  }

  return <ChartToolbar element={element} onPatch={onPatch} />;
}

function StatefulTableToolbar({
  initialElement,
  observedPatch,
}: {
  initialElement: TemplateV2ToolbarRecord;
  observedPatch: (
    patches: TemplateV2ToolbarPatch[],
    options?: PatchOptions
  ) => void;
}) {
  const [element, setElement] = useState(initialElement);

  function onPatch(
    patches: TemplateV2ToolbarPatch[],
    options?: PatchOptions
  ) {
    observedPatch(patches, options);
    setElement((current) =>
      patches.reduce(
        (next, patch) => applyAtPath(next, patch.path, patch.value),
        current
      )
    );
  }

  return <TableToolbar element={element} onPatch={onPatch} />;
}

const chart: TemplateV2ToolbarRecord = {
  type: "chart",
  chart_type: "bar",
  categories: ["January", "February"],
  series: [
    { name: "Revenue", values: [10, 20] },
    { name: "Cost", values: [4, 8] },
  ],
  x_axis: true,
  axis_color: "#334155",
  data_labels: "top",
  opacity: 1,
};

const table: TemplateV2ToolbarRecord = {
  type: "table",
  columns: [
    { runs: [{ text: "Account" }] },
    { runs: [{ text: "Amount" }] },
  ],
  rows: [
    [
      { runs: [{ text: "Cash" }] },
      { runs: [{ text: "100" }] },
    ],
  ],
  min_columns: 1,
  max_columns: 3,
  min_rows: 0,
  max_rows: 2,
  opacity: 1,
};

describe("Chart and table toolbar contract", () => {
  it("renders chart controls and emits coalescible data patches while retaining selectors", () => {
    const patch = cy.spy().as("chartPatch");
    cy.mount(
      <StatefulChartToolbar
        initialElement={chart}
        observedPatch={patch}
      />
    );

    cy.get('select[aria-label="Chart type"]').should("have.value", "bar");
    cy.get('select[aria-label="Chart category"]').select("1");
    cy.get('select[aria-label="Chart series"]').select("1");

    setReactInputValue("Chart category label", "February revised");
    cy.get("@chartPatch").should(
      "have.been.calledWith",
      [{ path: ["categories", 1], value: "February revised" }],
      {
        historyKey: "toolbar:chart-category-1",
        continuous: true,
      }
    );
    cy.get('select[aria-label="Chart category"]').should("have.value", "1");
    cy.get('select[aria-label="Chart series"]').should("have.value", "1");
    cy.get('input[aria-label="Chart category label"]').should(
      "have.value",
      "February revised"
    );

    setReactInputValue("Chart series name", "Operating cost");
    cy.get("@chartPatch").should(
      "have.been.calledWith",
      [{ path: ["series", 1, "name"], value: "Operating cost" }],
      {
        historyKey: "toolbar:chart-series-name-1",
        continuous: true,
      }
    );
    cy.get('select[aria-label="Chart series"]').should("have.value", "1");

    setReactInputValue("Chart series value", "9.5");
    cy.get("@chartPatch").should(
      "have.been.calledWith",
      [{ path: ["series", 1, "values", 1], value: 9.5 }],
      {
        historyKey: "toolbar:chart-series-value-1-1",
        continuous: true,
      }
    );
    cy.get('select[aria-label="Chart category"]').should("have.value", "1");
    cy.get('select[aria-label="Chart series"]').should("have.value", "1");

    cy.get('select[aria-label="Chart data labels"]').select("outside");
    cy.get("@chartPatch").should(
      "have.been.calledWith",
      [{ path: ["data_labels"], value: "outside" }],
      {
        historyKey: "toolbar:chart-data-labels",
        continuous: false,
      }
    );

    cy.get('button[aria-label="Add chart category"]').click();
    cy.get("@chartPatch").then((subject) => {
      const spy = subject as unknown as {
        lastCall: { args: [TemplateV2ToolbarPatch[], PatchOptions] };
      };
      const [patches, options] = spy.lastCall.args;
      expect(patches).to.deep.equal([
        {
          path: ["categories"],
          value: ["January", "February revised", "Category 3"],
        },
        { path: ["series", 0, "values"], value: [10, 20, 0] },
        { path: ["series", 1, "values"], value: [4, 9.5, 0] },
      ]);
      expect(options).to.deep.equal({
        historyKey: "toolbar:chart-categories",
        continuous: false,
      });
    });
    cy.get('select[aria-label="Chart category"]').should("have.value", "1");
    cy.get('select[aria-label="Chart series"]').should("have.value", "1");

    cy.get('button[aria-label="Add chart series"]').click();
    cy.get("@chartPatch").then((subject) => {
      const spy = subject as unknown as {
        lastCall: { args: [TemplateV2ToolbarPatch[], PatchOptions] };
      };
      const [patches, options] = spy.lastCall.args;
      expect(patches).to.have.length(1);
      expect(patches[0].path).to.deep.equal(["series"]);
      expect((patches[0].value as Array<Record<string, unknown>>)[2]).to.deep.equal({
        name: "Series 3",
        values: [0, 0, 0],
      });
      expect(options).to.deep.equal({
        historyKey: "toolbar:chart-series",
        continuous: false,
      });
    });
    cy.get('select[aria-label="Chart series"]').should("have.value", "1");

    cy.get('select[aria-label="Chart type"]').select("line");
    cy.get("@chartPatch").should(
      "have.been.calledWith",
      [{ path: ["chart_type"], value: "line" }],
      {
        historyKey: "toolbar:chart-type",
        continuous: false,
      }
    );
    cy.get('select[aria-label="Chart type"]').should("have.value", "line");
    cy.get('select[aria-label="Chart category"]').should("have.value", "1");
    cy.get('select[aria-label="Chart series"]').should("have.value", "1");
  });

  it("patches table text and bounded dimensions while retaining the cell selector", () => {
    const patch = cy.spy().as("tablePatch");
    cy.mount(
      <StatefulTableToolbar
        initialElement={table}
        observedPatch={patch}
      />
    );

    cy.get('select[aria-label="Table text target"]').select("3");
    setReactInputValue("Table cell text", "125");
    cy.get("@tablePatch").should(
      "have.been.calledWith",
      [{ path: ["rows", 0, 1, "runs", 0, "text"], value: "125" }],
      {
        historyKey: "toolbar:table-text-3",
        continuous: true,
      }
    );
    cy.get('select[aria-label="Table text target"]').should("have.value", "3");
    cy.get('input[aria-label="Table cell text"]').should("have.value", "125");

    setReactInputValue("Table cell font size", "20");
    cy.get("@tablePatch").should(
      "have.been.calledWith",
      [{ path: ["rows", 0, 1, "font", "size"], value: 20 }],
      {
        historyKey: "toolbar:table-font-size-3",
        continuous: true,
      }
    );
    setReactInputValue("Table cell font color", "#dc2626");
    cy.get("@tablePatch").should(
      "have.been.calledWith",
      [{ path: ["rows", 0, 1, "font", "color"], value: "#dc2626" }],
      {
        historyKey: "toolbar:table-font-color-3",
        continuous: true,
      }
    );
    cy.get('button[aria-label="Toggle table cell bold"]').click();
    cy.get("@tablePatch").should(
      "have.been.calledWith",
      [{ path: ["rows", 0, 1, "font", "bold"], value: true }],
      {
        historyKey: "toolbar:table-font-bold-3",
        continuous: false,
      }
    );
    cy.get('select[aria-label="Table text target"]').should("have.value", "3");

    cy.get('select[aria-label="Table cell alignment"]').select("right");
    cy.get("@tablePatch").should(
      "have.been.calledWith",
      [{ path: ["rows", 0, 1, "alignment"], value: "right" }],
      {
        historyKey: "toolbar:table-alignment-3",
        continuous: false,
      }
    );
    cy.get('select[aria-label="Table cell alignment"]')
      .should("have.value", "right");

    cy.get('button[aria-label="Add table row"]').click();
    cy.get("@tablePatch").then((subject) => {
      const spy = subject as unknown as {
        lastCall: { args: [TemplateV2ToolbarPatch[], PatchOptions] };
      };
      const [patches, options] = spy.lastCall.args;
      expect(patches).to.have.length(1);
      expect(patches[0].path).to.deep.equal(["rows"]);
      expect(patches[0].value).to.deep.equal([
        [
          { runs: [{ text: "Cash" }] },
          {
            runs: [{ text: "125" }],
            font: { size: 20, color: "#dc2626", bold: true },
            alignment: "right",
          },
        ],
        [
          { runs: [{ text: "" }] },
          { runs: [{ text: "" }] },
        ],
      ]);
      expect(options).to.deep.equal({
        historyKey: "toolbar:table-rows",
        continuous: false,
      });
    });
    cy.get('button[aria-label="Add table row"]').should("be.disabled");
    cy.get('select[aria-label="Table text target"]').should("have.value", "3");

    cy.get('button[aria-label="Remove table row"]').click();
    cy.get("@tablePatch").then((subject) => {
      const spy = subject as unknown as {
        lastCall: { args: [TemplateV2ToolbarPatch[], PatchOptions] };
      };
      const [patches, options] = spy.lastCall.args;
      expect(patches[0].path).to.deep.equal(["rows"]);
      expect(patches[0].value).to.have.length(1);
      expect(options).to.deep.equal({
        historyKey: "toolbar:table-rows",
        continuous: false,
      });
    });

    cy.get('button[aria-label="Add table column"]').click();
    cy.get("@tablePatch").then((subject) => {
      const spy = subject as unknown as {
        lastCall: { args: [TemplateV2ToolbarPatch[], PatchOptions] };
      };
      const [patches, options] = spy.lastCall.args;
      expect(patches.map((item) => item.path)).to.deep.equal([
        ["columns"],
        ["rows"],
      ]);
      expect(patches[0].value).to.have.length(3);
      expect(patches[1].value).to.have.length(1);
      expect((patches[1].value as unknown[][])[0]).to.have.length(3);
      expect(options).to.deep.equal({
        historyKey: "toolbar:table-columns",
        continuous: false,
      });
    });
    cy.get('button[aria-label="Add table column"]').should("be.disabled");
    cy.get('select[aria-label="Table text target"]').should("have.value", "3");

    cy.get('button[aria-label="Remove table column"]').click();
    cy.get("@tablePatch").then((subject) => {
      const spy = subject as unknown as {
        lastCall: { args: [TemplateV2ToolbarPatch[], PatchOptions] };
      };
      const [patches, options] = spy.lastCall.args;
      expect(patches.map((item) => item.path)).to.deep.equal([
        ["columns"],
        ["rows"],
      ]);
      expect(patches[0].value).to.have.length(2);
      expect((patches[1].value as unknown[][])[0]).to.have.length(2);
      expect(options).to.deep.equal({
        historyKey: "toolbar:table-columns",
        continuous: false,
      });
    });
    cy.get('select[aria-label="Table text target"]').should("have.value", "3");
  });

  it("disables all dimension changes for imported exact-bound tables", () => {
    cy.mount(
      <TableToolbar
        element={{
          ...table,
          min_columns: 2,
          max_columns: 2,
          min_rows: 1,
          max_rows: 1,
        }}
        onPatch={() => {
          throw new Error("exact-bound controls must not patch");
        }}
      />
    );

    cy.get('button[aria-label="Add table row"]').should("be.disabled");
    cy.get('button[aria-label="Remove table row"]').should("be.disabled");
    cy.get('button[aria-label="Add table column"]').should("be.disabled");
    cy.get('button[aria-label="Remove table column"]').should("be.disabled");
  });
});
