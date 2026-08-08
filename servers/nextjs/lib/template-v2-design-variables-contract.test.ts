import assert from "node:assert/strict";
import test from "node:test";

import { inspectTemplateV2DesignVariablesContract } from "./template-v2-design-variables-contract.ts";

test("design-variable contract inventories nested declarations without interpreting or losing JSON", () => {
  const slide = {
    id: "bound",
    ui: {
      future_layout: { retain: true },
      components: [
        {
          elements: [
            {
              type: "group",
              design_variables: [],
              child: {
                type: "text",
                design_variables: [
                  {
                    name: "title_color",
                    options: ["brand", { future_option: 7 }],
                    effect: [
                      {
                        path: "font.color",
                        effect: "set",
                        future_effect: { retain: "exactly" },
                      },
                    ],
                    future_binding: ["retain", "exactly"],
                  },
                ],
              },
              children: [
                {
                  type: "shape",
                  design_variables: [{ unknown_shape: { nested: true } }],
                },
              ],
            },
          ],
        },
      ],
    },
  };

  const contract = inspectTemplateV2DesignVariablesContract(slide);

  assert.equal(contract.activation, "retention-only");
  assert.equal(contract.hasBindings, true);
  assert.equal(contract.toolbarSafe, false);
  assert.deepEqual(
    contract.declarations.map(({ path }) => path),
    [
      ["ui", "components", 0, "elements", 0, "design_variables"],
      ["ui", "components", 0, "elements", 0, "child", "design_variables"],
      [
        "ui",
        "components",
        0,
        "elements",
        0,
        "children",
        0,
        "design_variables",
      ],
    ],
  );
  assert.deepEqual(
    contract.declarations[1]!.bindings,
    slide.ui.components[0]!.elements[0]!.child.design_variables,
  );

  const clonedBinding = contract.declarations[1]!.bindings[0] as {
    future_binding: string[];
  };
  clonedBinding.future_binding[0] = "changed";
  assert.equal(
    slide.ui.components[0]!.elements[0]!.child.design_variables[0]!
      .future_binding[0],
    "retain",
  );
});

test("empty declarations are retained but do not block unrelated toolbar edits", () => {
  const contract = inspectTemplateV2DesignVariablesContract({
    ui: {
      components: [
        {
          elements: [
            { type: "text", design_variables: [] },
            {
              type: "container",
              child: null,
              design_variables: null,
            },
          ],
        },
      ],
    },
  });

  assert.equal(contract.activation, "none");
  assert.equal(contract.hasBindings, false);
  assert.equal(contract.toolbarSafe, true);
  assert.equal(contract.declarations.length, 1);
  assert.deepEqual(contract.declarations[0]!.bindings, []);
});

test("design-variable contract requires strict slide.ui and fails closed on malformed declarations", () => {
  assert.throws(
    () =>
      inspectTemplateV2DesignVariablesContract({
        components: [{ elements: [] }],
      }),
    /template_v2_design_variables_slide_ui_required/,
  );
  assert.throws(
    () =>
      inspectTemplateV2DesignVariablesContract({
        ui: {
          components: [
            {
              elements: [
                { type: "text", design_variables: { name: "unsafe" } },
              ],
            },
          ],
        },
      }),
    /template_v2_design_variables_array_required/,
  );
  assert.throws(
    () =>
      inspectTemplateV2DesignVariablesContract({
        ui: {
          components: [
            {
              elements: [
                {
                  type: "text",
                  design_variables: [{ fallback: Number.NaN }],
                },
              ],
            },
          ],
        },
      }),
    /template_v2_design_variables_non_json/,
  );
});
