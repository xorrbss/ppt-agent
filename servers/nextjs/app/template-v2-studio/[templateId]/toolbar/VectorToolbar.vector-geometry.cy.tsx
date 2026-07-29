import React, { useState } from "react";

import { adaptTemplateV2LayoutToSlide } from "@/components/slide-editor/importing/template-v2-import";
import { templateV2UiToHtmlFragment } from "@/lib/template-v2-json-to-html";
import type { TemplateV2ToolbarRecord } from "@/lib/template-v2-toolbar";
import VectorToolbar from "./VectorToolbar";
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

const validVector: TemplateV2ToolbarRecord = {
  type: "vector",
  shape: "polygon",
  points: [
    { x: 10, y: 20, future_point: { pressure: 0.75 } },
    { x: 170, y: 15 },
    { x: 155, y: 130 },
    { x: 25, y: 145 },
  ],
  closed: true,
  corner_radii: [10, 8, 6, 4],
  curve: {
    type: "smooth",
    tension: 0.35,
    segments: 8,
    future_curve: { interpolation: "v3" },
  },
  fill: { color: "#123456" },
  stroke: { color: "#abcdef", width: 2 },
  future_vector: { retained: true },
};

describe("VectorToolbar geometry fidelity", () => {
  it("patches only safe leaves and keeps continuous controls coalescible", () => {
    const patch = cy.spy().as("patch");
    cy.mount(
      <StatefulVectorToolbar
        initialElement={validVector}
        observedPatch={patch}
      />
    );

    setReactInputValue("Vector point X", "48.25");
    setReactInputValue("Vector corner radius", "16");
    setReactInputValue("Curve tension", "0.64");
    setReactInputValue("Curve segments", "24");
    cy.get('input[aria-label="Smooth vector curve"]')
      .should("be.checked")
      .and("be.disabled");
    cy.get('input[aria-label="Closed vector path"]').click();

    cy.get("@patch").should(
      "have.been.calledWith",
      [{ path: ["points", 0, "x"], value: 48.25 }],
      {
        historyKey: "toolbar:vector-point-0-x",
        continuous: true,
      }
    );
    cy.get("@patch").should(
      "have.been.calledWith",
      [{ path: ["corner_radii", 0], value: 16 }],
      {
        historyKey: "toolbar:vector-corner-0",
        continuous: true,
      }
    );
    cy.get("@patch").should(
      "have.been.calledWith",
      [{ path: ["curve", "tension"], value: 0.64 }],
      {
        historyKey: "toolbar:vector-curve-tension",
        continuous: true,
      }
    );
    cy.get("@patch").should(
      "have.been.calledWith",
      [{ path: ["curve", "segments"], value: 24 }],
      {
        historyKey: "toolbar:vector-curve-segments",
        continuous: true,
      }
    );
    cy.get("@patch").should(
      "have.been.calledWith",
      [{ path: ["closed"], value: false }],
      { historyKey: "toolbar:vector-closed" }
    );

    cy.get('[data-testid="persisted-vector"]')
      .invoke("text")
      .then((serialized) => {
        const persisted = JSON.parse(serialized);
        expect(persisted.points[0].future_point).to.deep.equal({
          pressure: 0.75,
        });
        expect(persisted.curve.future_curve).to.deep.equal({
          interpolation: "v3",
        });
        expect(persisted.future_vector).to.deep.equal({ retained: true });
        expect(persisted.points[0].x).to.equal(48.25);
        expect(persisted.corner_radii).to.deep.equal([16, 8, 6, 4]);
        expect(persisted.curve).to.include({ tension: 0.64, segments: 24 });
        expect(persisted.closed).to.equal(false);

        const reentered = adaptTemplateV2LayoutToSlide({
          id: "vector-save-reentry",
          elements: [persisted],
        }).elements[0] as unknown as TemplateV2ToolbarRecord;
        const reenteredPoint = (
          reentered.points as TemplateV2ToolbarRecord[]
        )[0];
        const reenteredCurve = reentered.curve as TemplateV2ToolbarRecord;
        expect(reenteredPoint).to.include({ x: 48.25, y: 20 });
        expect(reenteredPoint.future_point).to.deep.equal({
          pressure: 0.75,
        });
        expect(reentered.corner_radii).to.deep.equal([16, 8, 6, 4]);
        expect(reenteredCurve.future_curve).to.deep.equal({
          interpolation: "v3",
        });
        expect(reentered.future_vector).to.deep.equal({ retained: true });

        const html = templateV2UiToHtmlFragment({ elements: [persisted] });
        expect(html).to.contain("<polyline");
        expect(html).to.contain('fill="none"');
        expect(html).not.to.contain("<polygon");
    });
  });

  it("creates and removes only canonical curves and converts two-point shapes including path", () => {
    const patch = cy.spy().as("patch");
    cy.mount(
      <StatefulVectorToolbar
        initialElement={{
          ...validVector,
          shape: "polygon",
          points: [{ x: 0, y: 0 }, { x: 120, y: 80 }],
          closed: false,
          curve: null,
          corner_radii: undefined,
        }}
        observedPatch={patch}
      />
    );
    cy.get('input[aria-label="Smooth vector curve"]').click();
    cy.get("@patch").should(
      "have.been.calledWith",
      [{
        path: ["curve"],
        value: { type: "smooth", tension: 0.5, segments: 16 },
      }],
      { historyKey: "toolbar:vector-curve" }
    );
    cy.get('input[aria-label="Curve tension"]').should("exist");
    cy.get('input[aria-label="Smooth vector curve"]').click();
    cy.get("@patch").should(
      "have.been.calledWith",
      [{ path: ["curve"], value: null }],
      { historyKey: "toolbar:vector-curve" }
    );
    cy.get('input[aria-label="Curve tension"]').should("not.exist");
    cy.get('input[aria-label="Curve segments"]').should("not.exist");
    cy.get('select[aria-label="Vector shape"]').select("ellipse");
    cy.get("@patch").should(
      "have.been.calledWith",
      [{ path: ["shape"], value: "ellipse" }],
      { historyKey: "toolbar:vector-shape" }
    );
    cy.get('[data-testid="persisted-vector"]')
      .invoke("text")
      .then((serialized) => {
        const persisted = JSON.parse(serialized);
        expect(persisted.shape).to.equal("ellipse");
        expect(persisted.future_vector).to.deep.equal({ retained: true });
        expect(templateV2UiToHtmlFragment({ elements: [persisted] }))
          .to.contain("<ellipse");
      });
    cy.get('select[aria-label="Vector shape"]').select("path");
    cy.get("@patch").should(
      "have.been.calledWith",
      [{ path: ["shape"], value: null }],
      { historyKey: "toolbar:vector-shape" }
    );
    cy.get('[data-testid="persisted-vector"]')
      .invoke("text")
      .then((serialized) => {
        const persisted = JSON.parse(serialized);
        expect(persisted.shape).to.equal(null);
        expect(persisted.future_vector).to.deep.equal({ retained: true });
        expect(templateV2UiToHtmlFragment({ elements: [persisted] }))
          .to.contain("<polyline");

        const reentered = adaptTemplateV2LayoutToSlide({
          id: "vector-path-reentry",
          elements: [persisted],
        }).elements[0] as unknown as TemplateV2ToolbarRecord;
        expect(reentered.shape ?? null).to.equal(null);
        expect(reentered.future_vector).to.deep.equal({ retained: true });
      });
  });

  it("creates canonical radii without replacing point or vector extensions", () => {
    const patch = cy.spy().as("patch");
    cy.mount(
      <StatefulVectorToolbar
        initialElement={{ ...validVector, corner_radii: undefined }}
        observedPatch={patch}
      />
    );
    setReactInputValue("Vector corner radius", "12");
    cy.get("@patch").should(
      "have.been.calledWith",
      [{ path: ["corner_radii"], value: [12, 0, 0, 0] }],
      {
        historyKey: "toolbar:vector-corner-0",
        continuous: true,
      }
    );
    cy.get('[data-testid="persisted-vector"]')
      .invoke("text")
      .then((serialized) => {
        const persisted = JSON.parse(serialized);
        expect(persisted.corner_radii).to.deep.equal([12, 0, 0, 0]);
        expect(persisted.points[0].future_point).to.deep.equal({
          pressure: 0.75,
        });
        expect(persisted.future_vector).to.deep.equal({ retained: true });
      });
  });

  it("adds, reorders, and deletes whole point records with canonical radii", () => {
    const patch = cy.spy().as("patch");
    const originalPoints = [
      { x: 10, y: 20, future_point: { pressure: 0.75 } },
      { x: 170, y: 15 },
      { x: 155, y: 130 },
      { x: 25, y: 145 },
    ];
    const addedPoints = [
      originalPoints[0],
      originalPoints[1],
      { x: 162.5, y: 72.5 },
      originalPoints[2],
      originalPoints[3],
    ];
    cy.mount(
      <StatefulVectorToolbar
        initialElement={validVector}
        observedPatch={patch}
      />
    );

    cy.get('select[aria-label="Vector point"]').select("1");
    cy.get('button[aria-label="Add vector point after"]').click();
    cy.get("@patch").should(
      "have.been.calledWith",
      [
        { path: ["points"], value: addedPoints },
        { path: ["corner_radii"], value: [10, 8, 0, 6, 4] },
      ],
      { historyKey: "toolbar:vector-point-add-after" }
    );
    cy.get('select[aria-label="Vector point"]').should("have.value", "2");

    cy.get('button[aria-label="Move vector point later"]').click();
    cy.get("@patch").should(
      "have.been.calledWith",
      [
        {
          path: ["points"],
          value: [
            originalPoints[0],
            originalPoints[1],
            originalPoints[2],
            { x: 162.5, y: 72.5 },
            originalPoints[3],
          ],
        },
        { path: ["corner_radii"], value: [10, 8, 6, 0, 4] },
      ],
      { historyKey: "toolbar:vector-point-move" }
    );
    cy.get('select[aria-label="Vector point"]').should("have.value", "3");

    cy.get('button[aria-label="Move vector point earlier"]').click();
    cy.get('select[aria-label="Vector point"]').should("have.value", "2");
    cy.get('button[aria-label="Delete vector point"]').click();
    cy.get("@patch").should(
      "have.been.calledWith",
      [
        { path: ["points"], value: originalPoints },
        { path: ["corner_radii"], value: [10, 8, 6, 4] },
      ],
      { historyKey: "toolbar:vector-point-delete" }
    );

    cy.get('[data-testid="persisted-vector"]')
      .invoke("text")
      .then((serialized) => {
        const persisted = JSON.parse(serialized);
        expect(persisted.points).to.deep.equal(originalPoints);
        expect(persisted.corner_radii).to.deep.equal([10, 8, 6, 4]);
        expect(persisted.points[0].future_point).to.deep.equal({
          pressure: 0.75,
        });
        expect(persisted.future_vector).to.deep.equal({ retained: true });
      });
  });

  it("edits point topology without creating an absent canonical radii field", () => {
    const patch = cy.spy().as("patch");
    const originalPoints = validVector.points as TemplateV2ToolbarRecord[];
    const addedPoints = [
      originalPoints[0],
      { x: 90, y: 17.5 },
      ...originalPoints.slice(1),
    ];
    cy.mount(
      <StatefulVectorToolbar
        initialElement={{ ...validVector, corner_radii: undefined }}
        observedPatch={patch}
      />
    );

    cy.get('button[aria-label="Add vector point after"]').click();
    cy.get("@patch").should(
      "have.been.calledWith",
      [{ path: ["points"], value: addedPoints }],
      { historyKey: "toolbar:vector-point-add-after" }
    );
    cy.get('[data-testid="persisted-vector"]')
      .invoke("text")
      .then((serialized) => {
        const persisted = JSON.parse(serialized);
        expect(persisted.points).to.deep.equal(addedPoints);
        expect(persisted).not.to.have.property("corner_radii");
      });
  });

  it("fails closed for malformed geometry and ambiguous legacy radii", () => {
    cy.mount(
      <VectorToolbar
        element={{
          ...validVector,
          curve: { type: "smooth", tension: 0.5, segments: 8.5 },
        }}
        onPatch={() => {}}
      />
    );
    cy.get('input[aria-label="Closed vector path"]').should("not.exist");
    cy.get('input[aria-label="Vector point X"]').should("not.exist");
    cy.get('input[aria-label="Smooth vector curve"]').should("not.exist");
    cy.get('input[aria-label="Curve tension"]').should("not.exist");
    cy.get('input[aria-label="Curve segments"]').should("not.exist");
    cy.get('select[aria-label="Vector shape"]').should("not.exist");
    cy.get('button[aria-label="Add vector point after"]').should("not.exist");

    cy.mount(
      <VectorToolbar
        element={{
          ...validVector,
          shape: "ellipse",
          points: [{ x: 0, y: 0 }, { x: 120, y: 80 }],
          corner_radii: undefined,
        }}
        onPatch={() => {}}
      />
    );
    cy.get('input[aria-label="Closed vector path"]').should("not.exist");
    cy.get('select[aria-label="Vector shape"]')
      .should("have.value", "ellipse")
      .find("option")
      .should("have.length", 1)
      .and("have.value", "ellipse");
    cy.get('input[aria-label="Vector point X"]').should("exist");
    cy.get('input[aria-label="Smooth vector curve"]').should("not.exist");
    cy.get('input[aria-label="Curve tension"]').should("not.exist");
    cy.get('input[aria-label="Curve segments"]').should("not.exist");
    cy.get('button[aria-label="Add vector point after"]').should("not.exist");

    cy.mount(
      <VectorToolbar
        element={{
          ...validVector,
          corner_radii: undefined,
          cornerRadii: [4, 3, 2, 1],
        }}
        onPatch={() => {}}
      />
    );
    cy.get('input[aria-label="Vector point X"]').should("exist");
    cy.get('input[aria-label="Vector corner radius"]').should("not.exist");
    cy.get('button[aria-label="Add vector point after"]').should("not.exist");
  });

  it("canonicalizes imported segment counts without dropping future metadata", () => {
    const slide = adaptTemplateV2LayoutToSlide({
      id: "vector-import-roundtrip",
      elements: [{
        ...validVector,
        curve: {
          type: "smooth",
          tension: 0.4,
          segments: 8.6,
          future_curve: { interpolation: "v3" },
        },
      }],
    });
    const imported = slide.elements[0] as unknown as TemplateV2ToolbarRecord;
    const curve = imported.curve as TemplateV2ToolbarRecord;
    const point = (imported.points as TemplateV2ToolbarRecord[])[0];

    expect(curve.segments).to.equal(9);
    expect(curve.future_curve).to.deep.equal({ interpolation: "v3" });
    expect(point.future_point).to.deep.equal({ pressure: 0.75 });
    expect(imported.future_vector).to.deep.equal({ retained: true });
  });
});
