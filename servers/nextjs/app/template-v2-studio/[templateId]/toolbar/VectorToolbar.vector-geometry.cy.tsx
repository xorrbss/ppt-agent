import React, { useState } from "react";

import type { TemplateV2ToolbarRecord } from "@/lib/template-v2-toolbar";
import VectorToolbar from "./VectorToolbar";
import type { TemplateV2ToolbarPatch } from "./types";

type PatchOptions = { historyKey?: string; continuous?: boolean };

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

function StatefulVectorToolbar({
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

  return (
    <>
      <VectorToolbar element={element} onPatch={onPatch} />
      <output data-testid="persisted-vector">{JSON.stringify(element)}</output>
    </>
  );
}

const pinnedUpstreamVector: TemplateV2ToolbarRecord = {
  type: "vector",
  shape: "polygon",
  points: [{ x: 0, y: 0 }, { x: 100, y: 80 }],
  closed: true,
  upstream_element_extension: "vector-vNext",
};

describe("VectorToolbar proven upstream edit contract", () => {
  it("exposes only the pinned add-after operation and closes after one edit", () => {
    const patch = cy.spy().as("patch");
    cy.mount(
      <StatefulVectorToolbar
        initialElement={pinnedUpstreamVector}
        observedPatch={patch}
      />
    );

    cy.get(
      'button[aria-label="Add wrap vector point after last"]'
    ).should("exist");
    cy.get('button[aria-label="Add vector point after"]').click();
    cy.get("@patch").should(
      "have.been.calledWith",
      [{
        path: ["points"],
        value: [{ x: 0, y: 0 }, { x: 50, y: 40 }, { x: 100, y: 80 }],
      }],
      { historyKey: "toolbar:vector-point-add-after" }
    );
    cy.get("@patch").should("have.been.calledOnce");
    cy.get('button[aria-label="Add vector point after"]').should("not.exist");
    cy.get(
      'button[aria-label="Add wrap vector point after last"]'
    ).should("not.exist");
    cy.get('[data-testid="persisted-vector"]')
      .invoke("text")
      .then((serialized) => {
        expect(JSON.parse(serialized)).to.deep.equal({
          ...pinnedUpstreamVector,
          points: [{ x: 0, y: 0 }, { x: 50, y: 40 }, { x: 100, y: 80 }],
        });
      });

    [
      "Shape fill color",
      "Shape stroke color",
      "Shape stroke width",
      "Shape opacity",
      "Vector point X",
      "Vector point Y",
      "Closed vector path",
      "Vector corner radius",
      "Smooth vector curve",
      "Curve tension",
      "Curve segments",
    ].forEach((label) => {
      cy.get(`[aria-label="${label}"]`).should("not.exist");
    });
    cy.get('select[aria-label="Vector shape"]').should("not.exist");
    cy.get('button[aria-label="Delete vector point"]').should("not.exist");
    cy.get('button[aria-label="Move vector point earlier"]').should("not.exist");
    cy.get('button[aria-label="Move vector point later"]').should("not.exist");
  });

  it("applies only the witnessed last-to-first wrap midpoint edit", () => {
    const patch = cy.spy().as("wrapPatch");
    cy.mount(
      <StatefulVectorToolbar
        initialElement={pinnedUpstreamVector}
        observedPatch={patch}
      />
    );

    cy.get(
      'button[aria-label="Add wrap vector point after last"]'
    ).click();
    cy.get("@wrapPatch").should(
      "have.been.calledWith",
      [{
        path: ["points"],
        value: [{ x: 0, y: 0 }, { x: 100, y: 80 }, { x: 50, y: 40 }],
      }],
      { historyKey: "toolbar:vector-point-add-after-last-wrap" }
    );
    cy.get("@wrapPatch").should("have.been.calledOnce");
    cy.get('button[aria-label="Add vector point after"]').should("not.exist");
    cy.get(
      'button[aria-label="Add wrap vector point after last"]'
    ).should("not.exist");
    cy.get(
      'button[aria-label="Remove proven wrap vector midpoint"]'
    ).click();
    cy.get("@wrapPatch").should(
      "have.been.calledWith",
      [{
        path: ["points"],
        value: [{ x: 0, y: 0 }, { x: 100, y: 80 }],
      }],
      { historyKey: "toolbar:vector-point-remove-proven-wrap" }
    );
    cy.get("@wrapPatch").should("have.been.calledTwice");
    cy.get('button[aria-label="Add vector point after"]').should("exist");
    cy.get(
      'button[aria-label="Add wrap vector point after last"]'
    ).should("exist");
    cy.get(
      'button[aria-label="Remove proven wrap vector midpoint"]'
    ).should("not.exist");
    cy.get('[data-testid="persisted-vector"]')
      .invoke("text")
      .then((serialized) => {
        expect(JSON.parse(serialized)).to.deep.equal(pinnedUpstreamVector);
      });
  });

  it("fails closed for every unproven vector variant", () => {
    const unsupported: TemplateV2ToolbarRecord[] = [
      { ...pinnedUpstreamVector, shape: "ellipse" },
      { ...pinnedUpstreamVector, closed: false },
      {
        ...pinnedUpstreamVector,
        points: [
          ...(pinnedUpstreamVector.points as unknown[]),
          { x: 50, y: 40 },
        ],
      },
      {
        ...pinnedUpstreamVector,
        curve: { type: "smooth", tension: 0.5, segments: 8 },
      },
      { ...pinnedUpstreamVector, corner_radii: [0, 0] },
      { ...pinnedUpstreamVector, raw_path: "M0 0 L100 80" },
      { ...pinnedUpstreamVector, fill: { color: "#123456" } },
      {
        ...pinnedUpstreamVector,
        points: [{ x: 0, y: 0, future_point: true }, { x: 100, y: 80 }],
      },
      {
        ...pinnedUpstreamVector,
        upstream_element_extension: "vector-future",
      },
    ];

    cy.mount(
      <div>
        {unsupported.map((element, index) => (
          <VectorToolbar
            key={index}
            element={element}
            onPatch={() => {
              throw new Error("unsupported vector edit was activated");
            }}
          />
        ))}
      </div>
    );
    cy.get('button[aria-label="Add vector point after"]').should("not.exist");
    cy.get(
      'button[aria-label="Add wrap vector point after last"]'
    ).should("not.exist");
    cy.get(
      'button[aria-label="Remove proven wrap vector midpoint"]'
    ).should("not.exist");
  });
});
