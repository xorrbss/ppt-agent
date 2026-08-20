import React, { useState } from "react";
import Konva from "konva";

import type { ElementPath, ElementGeometry, TemplateV2Scene } from "@/lib/template-v2-studio";

import TemplateV2Canvas from "./TemplateV2Canvas";
import { mountWithStudioI18n } from "./studioI18nTestUtils.cy";

const scene: TemplateV2Scene = {
  layout: { id: "guide-layout" },
  component: { id: "guide-component", position: { x: 0, y: 0 } },
  elements: [
    {
      type: "text",
      name: "moving-title",
      decorative: false,
      position: { x: 120, y: 96 },
      size: { width: 260, height: 84 },
      font: { family: "Inter", size: 28, color: "#0f172a", bold: true },
      runs: [{ text: "Editable title" }],
    },
    {
      type: "text",
      name: "alignment-target",
      decorative: false,
      position: { x: 520, y: 96 },
      size: { width: 260, height: 84 },
      font: { family: "Inter", size: 28, color: "#0f172a", bold: true },
      runs: [{ text: "Guide target" }],
    },
  ],
};

function Harness() {
  const [selectedPaths, setSelectedPaths] = useState<ElementPath[]>([[0]]);
  const [geometry, setGeometry] = useState<ElementGeometry[]>([]);
  return (
    <>
      <TemplateV2Canvas
        scene={scene}
        selectedPaths={selectedPaths}
        lockedPaths={[]}
        onSelect={(path) => setSelectedPaths(path ? [path] : [])}
        onGeometryBatch={setGeometry}
      />
      <output data-testid="template-v2-guide-geometry">
        {JSON.stringify(geometry)}
      </output>
    </>
  );
}

function findMovingNode() {
  let found: Konva.Node | undefined;
  cy.get('[role="application"]').then(($surface) => {
    const content = $surface.find(".konvajs-content")[0];
    const stage = Konva.stages.find((candidate) => candidate.getContent() === content);
    expect(stage, "Template V2 Konva stage").to.exist;
    found = stage!.find("Group").find(
      (node) => node.draggable() && node.x() === 120 && node.y() === 96,
    );
    expect(found, "moving Template V2 element").to.exist;
  });
  return cy.then(() => found!);
}

describe("Template V2 Canvas guide visual evidence", () => {
  it("renders the shared guide halo at fit, zoomed-out, and zoomed-in scales", () => {
    mountWithStudioI18n(<Harness />);
    cy.then(findMovingNode).then((node) => {
      node.fire("dragstart", {
        target: node,
        currentTarget: node,
        evt: new MouseEvent("mousedown"),
      }, true);
      // The target starts at x=520. Move the right edge to 518 so it snaps
      // to the target's left edge without overlapping the two text frames.
      node.position({ x: 258, y: 96 });
      node.fire("dragmove", {
        target: node,
        currentTarget: node,
        evt: new MouseEvent("mousemove"),
      }, true);
    });
    cy.get('[role="application"]').should(($surface) => {
      const content = $surface.find(".konvajs-content")[0];
      const stage = Konva.stages.find((candidate) => candidate.getContent() === content);
      const guideLines = stage!
        .find("Line")
        .filter((candidate) => candidate.stroke() === "#7A5AF8");
      expect(guideLines, "purple Template V2 guides").to.have.length(2);
      guideLines.forEach((line) => {
        expect(line.dash(), "scale-stable guide dash").to.have.length(2);
        expect(line.lineCap(), "rounded guide cap").to.equal("round");
        expect(line.strokeWidth(), "visible guide stroke").to.be.greaterThan(0);
        expect(line.points(), "full guide line").to.have.length(4);
      });
    });
    cy.get('[role="application"]').screenshot("template-v2-guide-fit");
    cy.get('[aria-label="Zoom out"]').click();
    cy.get('[role="application"]').screenshot("template-v2-guide-zoom-out");
    cy.get('[aria-label="Zoom in"]').click().click().click();
    cy.get('[role="application"]').screenshot("template-v2-guide-zoom-in");
  });
});
