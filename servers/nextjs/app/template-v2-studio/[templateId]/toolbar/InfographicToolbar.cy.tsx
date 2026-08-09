import React, { useState } from "react";

import type { TemplateV2ToolbarRecord } from "@/lib/template-v2-toolbar";
import { mountWithStudioI18n } from "../studioI18nTestUtils.cy";
import InfographicToolbar from "./InfographicToolbar";
import type {
  TemplateV2ToolbarPatch,
  TemplateV2ToolbarPatchOptions,
} from "./types";

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

function applyLeafPatch(
  element: TemplateV2ToolbarRecord,
  patch: TemplateV2ToolbarPatch
): TemplateV2ToolbarRecord {
  if (
    patch.path.length !== 2 ||
    patch.path[0] !== "data" ||
    patch.path[1] !== "value"
  ) {
    throw new Error("infographic toolbar emitted a non-leaf patch");
  }
  const data = element.data as Record<string, unknown>;
  return {
    ...element,
    data: {
      ...data,
      value: patch.value,
    },
  };
}

function StatefulInfographicToolbar({
  initialElement,
  observedPatch,
}: {
  initialElement: TemplateV2ToolbarRecord;
  observedPatch: (
    patches: TemplateV2ToolbarPatch[],
    options?: TemplateV2ToolbarPatchOptions
  ) => void;
}) {
  const [element, setElement] = useState(initialElement);

  function onPatch(
    patches: TemplateV2ToolbarPatch[],
    options?: TemplateV2ToolbarPatchOptions
  ) {
    observedPatch(patches, options);
    setElement((current) =>
      patches.reduce(
        (next, patch) => applyLeafPatch(next, patch),
        current
      )
    );
  }

  return (
    <>
      <InfographicToolbar element={element} onPatch={onPatch} />
      <output data-testid="persisted-infographic">
        {JSON.stringify(element)}
      </output>
    </>
  );
}

const infographic: TemplateV2ToolbarRecord = {
  type: "infographic",
  position: { x: 10, y: 20 },
  size: { width: 300, height: 40 },
  data: {
    type: "progress_bar",
    min_value: -20,
    max_value: 80,
    value: 30,
    future_format: "percent",
  },
  colors: ["#E5E7EB", "#2563EB"],
  upstream_element_extension: { retain: "exactly" },
};

describe("InfographicToolbar", () => {
  it("edits only the bounded value leaf and preserves unknown persisted JSON", () => {
    const patch = cy.spy().as("infographicPatch");
    mountWithStudioI18n(
      <StatefulInfographicToolbar
        initialElement={infographic}
        observedPatch={patch}
      />
    );

    cy.contains("Progress");
    cy.get('input[aria-label="Infographic value"]')
      .should("have.attr", "min", "-20")
      .and("have.attr", "max", "80")
      .and("have.value", "30");
    cy.get('input[aria-label="Infographic value slider"]')
      .should("have.attr", "min", "-20")
      .and("have.attr", "max", "80")
      .and("have.value", "30");

    setReactInputValue("Infographic value", "64");
    cy.get("@infographicPatch").should(
      "have.been.calledWith",
      [{ path: ["data", "value"], value: 64 }],
      {
        historyKey: "toolbar:infographic-value",
        continuous: true,
      }
    );
    cy.get('input[aria-label="Infographic value"]').should("have.value", "64");
    cy.get('[data-testid="persisted-infographic"]')
      .invoke("text")
      .then((text) => {
        const persisted = JSON.parse(text);
        expect(persisted.data).to.deep.equal({
          type: "progress_bar",
          min_value: -20,
          max_value: 80,
          value: 64,
          future_format: "percent",
        });
        expect(persisted.colors).to.deep.equal(["#E5E7EB", "#2563EB"]);
        expect(persisted.upstream_element_extension).to.deep.equal({
          retain: "exactly",
        });
      });

    setReactInputValue("Infographic value", "81");
    cy.get("@infographicPatch").should("have.callCount", 1);
    cy.get('input[aria-label="Infographic value"]').should("have.value", "64");

    setReactInputValue("Infographic value slider", "55");
    cy.get("@infographicPatch").should("have.callCount", 2);
    cy.get("@infographicPatch").then((subject) => {
      const observed = subject as unknown as {
        lastCall: {
          args: [
            TemplateV2ToolbarPatch[],
            TemplateV2ToolbarPatchOptions,
          ];
        };
      };
      expect(observed.lastCall.args).to.deep.equal([
        [{ path: ["data", "value"], value: 55 }],
        {
          historyKey: "toolbar:infographic-value",
          continuous: true,
        },
      ]);
    });
  });

  it("renders no controls when called with an invalid contract", () => {
    mountWithStudioI18n(
      <InfographicToolbar
        element={{
          ...infographic,
          data: {
            type: "gauge",
            min_value: 100,
            max_value: 0,
            value: 50,
          },
        }}
        onPatch={() => {
          throw new Error("invalid infographic must stay fail-closed");
        }}
      />
    );

    cy.get('input[aria-label="Infographic value"]').should("not.exist");
    cy.get('input[aria-label="Infographic value slider"]').should("not.exist");
  });
});
