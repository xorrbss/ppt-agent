import React, { useState } from "react";

import TemplateV2SelectionToolbar from "./TemplateV2SelectionToolbar";
import type {
  TemplateV2SelectionToolbarProps,
  TemplateV2ToolbarPatch,
} from "./types";

const position = {
  left: 20,
  top: 20,
  placement: "above" as const,
  visible: true,
};

const emptyActions = {};

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
  root: Record<string, unknown>,
  path: Array<string | number>,
  value: unknown
): Record<string, unknown> {
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

function StatefulToolbar({
  initialElement,
  observedPatch,
  ...props
}: Omit<TemplateV2SelectionToolbarProps, "element" | "onPatch"> & {
  initialElement: Record<string, unknown>;
  observedPatch: TemplateV2SelectionToolbarProps["onPatch"];
}) {
  const [element, setElement] = useState(initialElement);

  function onPatch(
    patches: TemplateV2ToolbarPatch[],
    options?: Parameters<TemplateV2SelectionToolbarProps["onPatch"]>[1]
  ) {
    observedPatch(patches, options);
    setElement((current) =>
      patches.reduce(
        (next, patch) => applyAtPath(next, patch.path, patch.value),
        current
      )
    );
  }

  return (
    <TemplateV2SelectionToolbar
      {...props}
      element={element}
      onPatch={onPatch}
    />
  );
}

describe("TemplateV2SelectionToolbar", () => {
  it("routes no selection to nothing and renders an accessible multi-selection toolbar", () => {
    cy.mount(
      <TemplateV2SelectionToolbar
        selectionCount={0}
        element={null}
        position={position}
        actions={emptyActions}
        onPatch={() => {}}
      />
    );
    cy.get('[data-testid="template-v2-selection-toolbar"]').should("not.exist");

    const parentPointerDown = cy.spy().as("parentPointerDown");
    const parentMouseDown = cy.spy().as("parentMouseDown");
    const parentClick = cy.spy().as("parentClick");
    const group = cy.spy().as("group");
    const ungroup = cy.spy().as("ungroup");
    const layer = cy.spy().as("layer");
    const align = cy.spy().as("align");
    const distribute = cy.spy().as("distribute");
    const lock = cy.spy().as("lock");
    const duplicate = cy.spy().as("duplicate");
    const remove = cy.spy().as("remove");

    cy.mount(
      <div
        onPointerDown={parentPointerDown}
        onMouseDown={parentMouseDown}
        onClick={parentClick}
      >
        <TemplateV2SelectionToolbar
          selectionCount={3}
          element={null}
          position={position}
          actions={{
            locked: false,
            canGroup: true,
            canUngroup: true,
            onGroup: group,
            onUngroup: ungroup,
            onLayer: layer,
            onAlign: align,
            onDistribute: distribute,
            onLockChange: lock,
            onDuplicate: duplicate,
            onDelete: remove,
          }}
          onPatch={() => {}}
        />
      </div>
    );

    cy.get('[role="toolbar"][aria-label="Selection toolbar"]')
      .should("be.visible")
      .and("have.attr", "data-template-v2-floating-toolbar", "true")
      .and("have.css", "left", "20px")
      .and("have.css", "top", "20px")
      .within(() => {
        cy.contains("3 selected");
        cy.get('button[aria-label="Group selection"]').click();
        cy.get('button[aria-label="Ungroup selection"]').click();
        cy.get('select[aria-label="Layer selection"]').select("front");
        cy.get('select[aria-label="Align selection"]').select("center");
        cy.get('select[aria-label="Distribute selection"]').select(
          "horizontal"
        );
        cy.get('button[aria-label="Lock selection"]')
          .should("have.attr", "aria-pressed", "false")
          .click();
        cy.get('button[aria-label="Duplicate selection"]').click();
        cy.get('button[aria-label="Delete selection"]').click();
      });

    cy.get("@group").should("have.been.calledOnce");
    cy.get("@ungroup").should("have.been.calledOnce");
    cy.get("@layer").should("have.been.calledWith", "front");
    cy.get("@align").should("have.been.calledWith", "center");
    cy.get("@distribute").should("have.been.calledWith", "horizontal");
    cy.get("@lock").should("have.been.calledWith", true);
    cy.get("@duplicate").should("have.been.calledOnce");
    cy.get("@remove").should("have.been.calledOnce");

    cy.get('[role="toolbar"]')
      .trigger("pointerdown")
      .trigger("mousedown")
      .click();
    cy.get("@parentPointerDown").should("not.have.been.called");
    cy.get("@parentMouseDown").should("not.have.been.called");
    cy.get("@parentClick").should("not.have.been.called");

    cy.mount(<div data-testid="replacement">Replacement mount</div>);
    cy.get('[data-testid="template-v2-selection-toolbar"]').should("not.exist");
  });

  it("supports crop reset, cancel, apply, scale below one, and persisted re-entry", () => {
    const patch = cy.spy().as("imagePatch");
    const cropMode = cy.spy().as("cropMode");
    const cropDraft = cy.spy().as("cropDraft");

    cy.mount(
      <StatefulToolbar
        selectionCount={1}
        selectionPath={[0]}
        initialElement={{
          type: "image",
          fit: "cover",
          focus_x: 22,
          focus_y: 77,
          crop_scale: 0.4,
          opacity: 1,
        }}
        position={position}
        actions={emptyActions}
        observedPatch={patch}
        onCropModeChange={cropMode}
        onCropDraftChange={cropDraft}
      />
    );

    cy.get('button[aria-label="Crop image"]').click();
    cy.get("@cropMode").should("have.been.calledWith", [0]);
    cy.get('input[aria-label="Crop scale"]').should("have.value", "0.4");

    cy.get('button[aria-label="Reset image crop"]').click();
    cy.get('input[aria-label="Crop horizontal focus"]').should(
      "have.value",
      "50"
    );
    cy.get('input[aria-label="Crop vertical focus"]').should(
      "have.value",
      "50"
    );
    cy.get('input[aria-label="Crop scale"]').should("have.value", "1");
    cy.get("@cropDraft").should(
      "have.been.calledWith",
      Cypress.sinon.match({ focusX: 50, focusY: 50, cropScale: 1 })
    );

    cy.get('button[aria-label="Cancel image crop"]').click();
    cy.get("@cropMode").should("have.been.calledWith", null);
    cy.get('[data-testid="template-v2-image-crop-controls"]').should(
      "not.exist"
    );

    cy.get('button[aria-label="Crop image"]').click();
    cy.get('input[aria-label="Crop scale"]').should("have.value", "0.4");
    setReactInputValue("Crop horizontal focus", "18");
    setReactInputValue("Crop vertical focus", "82");
    setReactInputValue("Crop scale", "0.6");
    cy.get('button[aria-label="Apply image crop"]').click();

    cy.get("@imagePatch").then((subject) => {
      const observed = subject as unknown as {
        lastCall: {
          args: [
            TemplateV2ToolbarPatch[],
            Record<string, unknown> | undefined,
          ];
        };
      };
      const [patches, options] = observed.lastCall.args;
      expect(patches).to.deep.equal([
        { path: ["fit"], value: "cover" },
        { path: ["focus_x"], value: 18 },
        { path: ["focus_y"], value: 82 },
        { path: ["crop_scale"], value: 0.6 },
      ]);
      expect(options).to.deep.equal({ historyKey: "toolbar:image-crop" });
    });
    cy.get("@cropMode").should("have.been.calledWith", null);

    cy.get('button[aria-label="Crop image"]').click();
    cy.get('input[aria-label="Crop horizontal focus"]').should(
      "have.value",
      "18"
    );
    cy.get('input[aria-label="Crop vertical focus"]').should(
      "have.value",
      "82"
    );
    cy.get('input[aria-label="Crop scale"]').should("have.value", "0.6");
  });

  it("routes icons and text controls to lossless patch callbacks", () => {
    const iconPatch = cy.spy().as("iconPatch");
    const changeIcon = cy.spy().as("changeIcon");

    cy.mount(
      <StatefulToolbar
        selectionCount={1}
        initialElement={{
          type: "image",
          is_icon: true,
          color: "#112233",
          svg_data: "<svg />",
        }}
        position={position}
        actions={emptyActions}
        observedPatch={iconPatch}
        onChangeIcon={changeIcon}
      />
    );
    cy.get('button[aria-label="Crop image"]').should("not.exist");
    cy.get('input[aria-label="Icon color"]')
      .clear()
      .type("#ff00aa")
      .should("have.value", "#ff00aa");
    cy.get('button[aria-label="Change icon"]').click();
    cy.get("@iconPatch").then((subject) => {
      const observed = subject as unknown as {
        lastCall: {
          args: [
            TemplateV2ToolbarPatch[],
            Record<string, unknown> | undefined,
          ];
        };
      };
      expect(observed.lastCall.args[0]).to.deep.equal([
        { path: ["color"], value: "#ff00aa" },
      ]);
      expect(observed.lastCall.args[1]).to.deep.equal({
        historyKey: "toolbar:icon-color",
        continuous: true,
      });
    });
    cy.get("@changeIcon").should("have.been.calledOnce");

    const textPatch = cy.spy().as("textPatch");
    cy.mount(
      <StatefulToolbar
        selectionCount={1}
        initialElement={{
          type: "text-list",
          marker: "bullet",
          font: { family: "Arial", size: 18, color: "#101010" },
          items: [[{ text: "One", font: { family: "Arial", size: 18 } }]],
          alignment: { horizontal: "left" },
          opacity: 1,
        }}
        position={position}
        actions={emptyActions}
        observedPatch={textPatch}
        fontFamilies={["Arial", "Noto Sans KR"]}
      />
    );
    cy.get('select[aria-label="Font family"]').select("Noto Sans KR");
    cy.get('button[aria-label="Bold text"]').click();
    cy.get('select[aria-label="Text alignment"]').select("center");
    cy.get('select[aria-label="List marker"]').select("number");

    cy.get("@textPatch").then((subject) => {
      const observed = subject as unknown as {
        getCalls(): Array<{
          args: [
            TemplateV2ToolbarPatch[],
            Record<string, unknown> | undefined,
          ];
        }>;
      };
      const calls = observed.getCalls().map((call) => call.args);
      expect(calls).to.deep.include([
        [
          { path: ["font", "family"], value: "Noto Sans KR" },
          {
            path: ["items", 0, 0, "font", "family"],
            value: "Noto Sans KR",
          },
        ],
        { historyKey: "toolbar:text-family" },
      ]);
      expect(calls).to.deep.include([
        [
          { path: ["alignment", "horizontal"], value: "center" },
        ],
        { historyKey: "toolbar:text-alignment" },
      ]);
      expect(calls).to.deep.include([
        [{ path: ["marker"], value: "number" }],
        { historyKey: "toolbar:text-marker" },
      ]);
      expect(
        calls.some(
          ([patches]) =>
            Array.isArray(patches) &&
            patches.some(
              (candidate) =>
                candidate.path?.join(".") === "font.bold" &&
                candidate.value === true
            )
        )
      ).to.equal(true);
    });
  });

  it("renders bounded shape, chart, and table controls without replacing nested selections", () => {
    const shapePatch = cy.spy().as("shapePatch");
    cy.mount(
      <StatefulToolbar
        selectionCount={1}
        selectionPath={["children", 2, "children", 1]}
        initialElement={{
          type: "vector",
          shape: "polygon",
          points: [{ x: 0, y: 0 }, { x: 50, y: 50 }],
          fill: { color: "#ffffff", retained: "fill metadata" },
          stroke: { color: "#111111", width: 1, retained: "stroke metadata" },
          opacity: 1,
          retained: { nested: true },
        }}
        position={position}
        actions={emptyActions}
        observedPatch={shapePatch}
      />
    );
    setReactInputValue("Shape fill color", "#334455");
    setReactInputValue("Shape stroke width", "2");
    cy.get("[role=toolbar]").should("contain", "Fill");
    cy.get("@shapePatch").then((subject) => {
      const calls = (subject as unknown as { getCalls(): Array<{ args: [TemplateV2ToolbarPatch[], Record<string, unknown>] }> })
        .getCalls()
        .map((call) => call.args);
      expect(calls).to.deep.include([
        [{ path: ["fill", "color"], value: "#334455" }],
        { historyKey: "toolbar:vector-fill", continuous: true },
      ]);
      expect(calls).to.deep.include([
        [{ path: ["stroke", "width"], value: 2 }],
        { historyKey: "toolbar:vector-stroke-width", continuous: true },
      ]);
    });

    const chartPatch = cy.spy().as("chartPatch");
    cy.mount(
      <StatefulToolbar
        selectionCount={1}
        initialElement={{
          type: "chart",
          chart_type: "bar",
          categories: ["Q1"],
          colors: ["#2563eb"],
          series: [{ name: "Revenue", values: [12], retained: "data stays put" }],
          legend: false,
          title_color: "#111827",
        }}
        position={position}
        actions={emptyActions}
        observedPatch={chartPatch}
      />
    );
    cy.get('button[aria-label="Toggle chart legend"]')
      .should("have.attr", "aria-pressed", "false")
      .click()
      .should("have.attr", "aria-pressed", "true");
    setReactInputValue("Chart grid color", "#e5e7eb");
    cy.get("@chartPatch").then((subject) => {
      const calls = (subject as unknown as { getCalls(): Array<{ args: [TemplateV2ToolbarPatch[], Record<string, unknown>] }> })
        .getCalls()
        .map((call) => call.args);
      expect(calls).to.deep.include([
        [{ path: ["legend"], value: true }],
        { historyKey: "toolbar:chart-legend" },
      ]);
      expect(calls).to.deep.include([
        [{ path: ["grid_color"], value: "#e5e7eb" }],
        { historyKey: "toolbar:chart-grid_color", continuous: true },
      ]);
    });

    const tablePatch = cy.spy().as("tablePatch");
    cy.mount(
      <StatefulToolbar
        selectionCount={1}
        initialElement={{
          type: "table",
          columns: [
            { runs: [{ text: "Name" }], color: { color: "#f1f5f9", retained: true } },
            { runs: [{ text: "Value" }], color: "#f1f5f9" },
          ],
          rows: [[
            { runs: [{ text: "Q1" }], color: "#ffffff" },
            { runs: [{ text: "12" }], color: "#ffffff" },
          ]],
          min_columns: 2,
          max_columns: 2,
          min_rows: 1,
          max_rows: 1,
          opacity: 1,
        }}
        position={position}
        actions={emptyActions}
        observedPatch={tablePatch}
      />
    );
    setReactInputValue("Table header fill color", "#ddeeff");
    cy.get("@tablePatch").then((subject) => {
      const observed = subject as unknown as {
        lastCall: { args: [TemplateV2ToolbarPatch[], Record<string, unknown>] };
      };
      expect(observed.lastCall.args).to.deep.equal([
        [
          { path: ["columns", 0, "color", "color"], value: "#ddeeff" },
          { path: ["columns", 1, "color"], value: "#ddeeff" },
        ],
        { historyKey: "toolbar:table-header-fill", continuous: true },
      ]);
    });
  });

  it("keeps a nested selection path stable across rich patches, command actions, and a persisted remount", () => {
    const path = ["children", 2, "children", 1];
    const patch = cy.spy().as("persistedShapePatch");
    const lock = cy.spy().as("persistedShapeLock");
    const duplicate = cy.spy().as("persistedShapeDuplicate");
    const initialElement = {
      type: "vector",
      shape: "polygon",
      points: [{ x: 0, y: 0 }, { x: 50, y: 50 }],
      fill: { color: "#ffffff", retained: "keep me" },
      stroke: { color: "#111111", width: 1 },
      opacity: 1,
    };

    cy.mount(
      <StatefulToolbar
        selectionCount={1}
        selectionPath={path}
        initialElement={initialElement}
        position={position}
        actions={{ onLockChange: lock, onDuplicate: duplicate }}
        observedPatch={patch}
      />
    );

    cy.get('[role="toolbar"][aria-label="Selection toolbar"]').should("be.visible");
    setReactInputValue("Shape fill color", "#334455");
    cy.get('button[aria-label="Lock selection"]').click();
    cy.get('button[aria-label="Duplicate selection"]').click();
    cy.get("@persistedShapePatch").then((subject) => {
      const calls = (
        subject as unknown as {
          getCalls(): Array<{
            args: [TemplateV2ToolbarPatch[], Record<string, unknown>];
          }>;
        }
      )
        .getCalls()
        .map((call) => call.args);
      expect(calls).to.deep.include([
        [{ path: ["fill", "color"], value: "#334455" }],
        { historyKey: "toolbar:vector-fill", continuous: true },
      ]);
    });
    cy.get("@persistedShapeLock").should("have.been.calledWith", true);
    cy.get("@persistedShapeDuplicate").should("have.been.calledOnce");
    cy.wrap(path).should("deep.equal", ["children", 2, "children", 1]);

    // This mirrors an optimistic save followed by a reload: the controller
    // supplies the same ElementPath and persisted rich element without a
    // toolbar-specific state channel.
    cy.mount(
      <StatefulToolbar
        selectionCount={1}
        selectionPath={path}
        initialElement={{
          ...initialElement,
          fill: { color: "#334455", retained: "keep me" },
        }}
        position={position}
        actions={emptyActions}
        observedPatch={() => {}}
      />
    );
    cy.get('input[aria-label="Shape fill color"]').should(
      "have.value",
      "#334455"
    );
    cy.get('[data-testid="template-v2-selection-toolbar"]').should("exist");
    cy.wrap(path).should("deep.equal", ["children", 2, "children", 1]);
  });

  it("routes a strict infographic leaf edit without losing selection and cleans up its portal", () => {
    const path = ["children", 4];
    const patch = cy.spy().as("infographicSelectionPatch");
    const initialElement = {
      type: "infographic",
      data: {
        type: "progress_bar",
        min_value: 0,
        max_value: 100,
        value: 35,
        retained_data: { source: "upstream" },
      },
      colors: ["#e5e7eb", "#2563eb"],
      retained_root: { owner: "template" },
    };

    cy.mount(
      <StatefulToolbar
        selectionCount={1}
        selectionPath={path}
        initialElement={initialElement}
        position={position}
        actions={emptyActions}
        observedPatch={patch}
      />
    );

    setReactInputValue("Infographic value", "64");
    cy.get("@infographicSelectionPatch").should(
      "have.been.calledWith",
      [{ path: ["data", "value"], value: 64 }],
      {
        historyKey: "toolbar:infographic-value",
        continuous: true,
      }
    );
    cy.get('input[aria-label="Infographic value"]').should("have.value", "64");
    cy.wrap(path).should("deep.equal", ["children", 4]);

    cy.mount(
      <StatefulToolbar
        selectionCount={1}
        selectionPath={path}
        initialElement={{
          ...initialElement,
          data: { ...initialElement.data, value: 64 },
        }}
        position={position}
        actions={emptyActions}
        observedPatch={() => {}}
      />
    );
    cy.get('input[aria-label="Infographic value"]').should("have.value", "64");

    cy.mount(<div data-testid="infographic-replacement">Replacement</div>);
    cy.get('[data-testid="template-v2-selection-toolbar"]').should("not.exist");
  });
});
