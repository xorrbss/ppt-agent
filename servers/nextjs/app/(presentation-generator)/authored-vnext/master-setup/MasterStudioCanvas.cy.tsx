import React, { useState } from "react";
import Konva from "konva";
import { NextIntlClientProvider } from "next-intl";

import enMessages from "@/i18n/messages/en.json";
import type { DeckMasterContractV1 } from "@/lib/authored-vnext/master/contracts";
import { DEFAULT_MASTER_CONTRACT } from "@/lib/authored-vnext/master/fixtures";

import MasterStudioCanvas from "./MasterStudioCanvas";

function cloneContract(): DeckMasterContractV1 {
  return structuredClone(DEFAULT_MASTER_CONTRACT);
}

function Harness({ initialContract }: { initialContract?: DeckMasterContractV1 } = {}) {
  const [contract, setContract] = useState(() => initialContract ?? cloneContract());

  return (
    <NextIntlClientProvider locale="en" messages={enMessages} timeZone="UTC">
      <MasterStudioCanvas
        contract={contract}
        layoutKind="standard"
        onContractChange={setContract}
      />
      <output data-testid="master-contract-snapshot">
        {JSON.stringify(contract)}
      </output>
    </NextIntlClientProvider>
  );
}

function findMountedKonvaNode(elementId: string) {
  let found: Konva.Node | undefined;
  cy.get('[data-testid="master-studio-canvas"]').then(($surface) => {
    const content = $surface.find(".konvajs-content")[0];
    expect(content, "mounted Konva stage content").to.exist;
    const stage = Konva.stages.find(
      (candidate) => candidate.getContent() === content,
    );
    expect(stage, "Konva stage for master canvas").to.exist;
    found = stage!.find(".master-canvas-element").find(
      (node) => node.id() === elementId,
    );
    expect(found, `Konva node ${elementId}`).to.exist;
  });
  return cy.then(() => found!);
}

function expectMountedKonvaNodeMissing(elementId: string) {
  return cy.get('[data-testid="master-studio-canvas"]').then(($surface) => {
    const content = $surface.find(".konvajs-content")[0];
    const stage = Konva.stages.find(
      (candidate) => candidate.getContent() === content,
    );
    expect(stage, "Konva stage for master canvas").to.exist;
    const found = stage!.find(".master-canvas-element").find(
      (node) => node.id() === elementId,
    );
    expect(found, `Konva node ${elementId}`).to.eq(undefined);
  });
}

function selectKonvaElement(elementId: string) {
  return findMountedKonvaNode(elementId).then((node) => {
    // Component tests exercise the mounted Konva pointer boundary directly;
    // the Stage resolves browser mouse, pen, and touch input to this handler.
    node.fire("pointerdown", {
      target: node,
      evt: new PointerEvent("pointerdown"),
    }, true);
    node.fire("click", {
      target: node,
      evt: new MouseEvent("click"),
    }, true);
  });
}

function dragKonvaElement(elementId: string, deltaX: number, deltaY: number) {
  return findMountedKonvaNode(elementId).then((node) => {
    const stage = node.getStage();
    expect(stage, "node stage").to.exist;
    expect(stage!.listening(), "stage listening in edit mode").to.eq(true);
    expect(node.listening(), "node listening in edit mode").to.eq(true);
    expect(node.draggable(), "node draggable in edit mode").to.eq(true);
    node.fire("dragstart", {
      target: node,
      evt: new MouseEvent("mousedown"),
    }, true);
    node.position({ x: node.x() + deltaX, y: node.y() + deltaY });
    node.fire("dragend", {
      target: node,
      evt: new MouseEvent("mouseup"),
    }, true);
  });
}

function pressCanvasShortcut(
  key: "y" | "z",
  options: { ctrlKey?: boolean; metaKey?: boolean; shiftKey?: boolean },
) {
  cy.get('[data-testid="master-studio-canvas"]').then(($surface) => {
    const surface = $surface[0] as HTMLElement;
    surface.focus();
    const event = new KeyboardEvent("keydown", {
      key,
      code: key === "z" ? "KeyZ" : "KeyY",
      bubbles: true,
      cancelable: true,
      ...options,
    });
    surface.dispatchEvent(event);
    expect(event.defaultPrevented, `${key} shortcut default`).to.eq(true);
  });
}

describe("MasterStudioCanvas Konva adapter", () => {
  it("removes a selected bound logo while fixed furniture stays protected", () => {
    const contract = cloneContract();
    contract.brandAssets.logo = {
      assetId: "asset:deletable-logo",
      digest: `sha256:${"d".repeat(64)}`,
      altText: "Deletable logo",
    };
    contract.furniture.logo.assetId = contract.brandAssets.logo.assetId;

    cy.mount(<Harness initialContract={contract} />);
    cy.then(() => selectKonvaElement("furniture:logo"));
    cy.get('[data-testid="master-canvas-delete"]')
      .should("be.enabled")
      .then(($button) => {
        // Cypress's synthetic pointer sequence can move focus after this
        // mounted-Konva selection assertion. The native control invocation
        // exercises the same enabled product click handler deterministically.
        ($button[0] as HTMLButtonElement).click();
      });
    cy.get('[data-testid="master-contract-snapshot"]').should(($output) => {
      const updated = JSON.parse($output.text()) as DeckMasterContractV1;
      expect(updated.brandAssets.logo).to.eq(null);
      expect(updated.furniture.logo.assetId).to.eq(undefined);
    });
    expectMountedKonvaNodeMissing("furniture:logo");
    cy.then(() => findMountedKonvaNode("furniture:header")).then((node) => {
      node.fire("click", {
        target: node,
        evt: new MouseEvent("click"),
      }, true);
    });
    cy.get('[data-testid="master-canvas-delete"]')
      .should("be.disabled");
  });

  it("keeps a wheel-zoomed slide centered while it fits in the viewport", () => {
    cy.mount(<Harness />);
    cy.get('[data-testid="master-studio-canvas"]').should(($surface) => {
      const surface = $surface[0] as HTMLElement;
      const scale = Number(surface.dataset.viewportScale);
      expect(Number(surface.dataset.viewportX), "initial centered viewport x").to.be.closeTo(
        (surface.clientWidth - DEFAULT_MASTER_CONTRACT.canvas.widthPx * scale) / 2,
        1,
      );
    });
    cy.get('[data-testid="master-studio-canvas"]').should(($surface) => {
      const surface = $surface[0] as HTMLElement;
      const canvas = $surface.find("canvas")[0];
      const rect = surface.getBoundingClientRect();
      const pointer = { clientX: rect.left + 40, clientY: rect.top + 40 };

      canvas.dispatchEvent(new MouseEvent("mousemove", {
        ...pointer,
        bubbles: true,
        cancelable: true,
      }));
      for (let index = 0; index < 4; index += 1) {
        const wheel = new WheelEvent("wheel", {
          ...pointer,
          bubbles: true,
          cancelable: true,
          deltaY: 100,
        });
        canvas.dispatchEvent(wheel);
        expect(wheel.defaultPrevented, "wheel page scroll").to.eq(true);
      }
    });

    cy.get('[data-testid="master-studio-canvas"]').should(($surface) => {
      const surface = $surface[0] as HTMLElement;
      const scale = Number(surface.dataset.viewportScale);
      const x = Number(surface.dataset.viewportX);
      const y = Number(surface.dataset.viewportY);
      expect(scale, "wheel zoom scale").to.be.lessThan(0.7);
      expect(x, "centered viewport x").to.be.closeTo(
        (surface.clientWidth - DEFAULT_MASTER_CONTRACT.canvas.widthPx * scale) / 2,
        1,
      );
      expect(y, "centered viewport y").to.be.closeTo(
        (surface.clientHeight - DEFAULT_MASTER_CONTRACT.canvas.heightPx * scale) / 2,
        1,
      );
    });
  });

  it("renders the shared guide halo at fit, zoomed-out, and zoomed-in scales", () => {
    let insertedId = "";
    cy.mount(<Harness />);
    cy.contains("button", "AI 편집존").click();
    cy.get('[data-testid="master-contract-snapshot"]').should(($output) => {
      const contract = JSON.parse($output.text()) as DeckMasterContractV1;
      insertedId = contract.aiEditZones?.at(-1)?.id ?? "";
      expect(insertedId, "inserted AI zone id").to.match(/^ai-zone:/);
    });
    cy.then(() => findMountedKonvaNode(insertedId)).then((node) => {
      node.fire("dragstart", {
        target: node,
        currentTarget: node,
        evt: new MouseEvent("mousedown"),
      }, true);
      // Move within the snap threshold of the safe-area corner. The guide
      // remains rendered until dragend, which is exactly the visual state a
      // user sees while aligning an editable envelope.
      node.position({ x: 77, y: 73 });
      node.fire("dragmove", {
        target: node,
        currentTarget: node,
        evt: new MouseEvent("mousemove"),
      }, true);
    });
    cy.get('[data-testid="master-studio-canvas"]').should(($surface) => {
      const content = $surface.find(".konvajs-content")[0];
      const stage = Konva.stages.find((candidate) => candidate.getContent() === content);
      const guideLines = stage!
        .find("Line")
        .filter((candidate) => candidate.stroke() === "#7A5AF8");
      expect(guideLines, "purple guide lines").to.have.length(2);
      guideLines.forEach((line) => {
        expect(line.dash(), "scale-stable guide dash").to.have.length(2);
        expect(line.lineCap(), "rounded guide cap").to.equal("round");
        expect(line.strokeWidth(), "visible guide stroke").to.be.greaterThan(0);
        expect(line.points(), "full guide line").to.have.length(4);
      });
    });
    cy.get('[data-testid="master-studio"]').screenshot("authored-master-guide-fit");
    cy.get('[aria-label="Zoom out"]').click();
    cy.get('[data-testid="master-studio"]').screenshot("authored-master-guide-zoom-out");
    cy.get('[aria-label="Zoom in"]').click().click().click();
    cy.get('[data-testid="master-studio"]').screenshot("authored-master-guide-zoom-in");
  });

  it("keeps AI-zone drag history stable and makes preview genuinely read-only", () => {
    let insertedId = "";
    const insertedBounds = { x: 76, y: 72, width: 360, height: 92 };
    // Snapping is evaluated at drag time. This delta does not cross a guide,
    // so the committed geometry is the safe-area-based insertion plus delta.
    const draggedBounds = { x: 130, y: 104, width: 360, height: 92 };

    cy.mount(<Harness />);
    cy.get('[data-testid="master-studio-canvas"] canvas')
      .filter(":visible")
      .should("exist");

    cy.contains("button", "AI 편집존").click();
    cy.get('[data-testid="master-studio-inline-editor"]').should("not.exist");
    cy.get('[data-testid="master-contract-snapshot"]').should(($output) => {
      const contract = JSON.parse($output.text()) as DeckMasterContractV1;
      const inserted = contract.aiEditZones?.at(-1);
      expect(inserted).to.exist;
      expect(inserted!.bounds).to.deep.equal(insertedBounds);
      insertedId = inserted!.id;
    });

    cy.then(() => selectKonvaElement(insertedId));
    cy.get('select[aria-label="AI 편집존 텍스트 정렬"]')
      .select("right");
    cy.get('[data-testid="master-contract-snapshot"]').should(($output) => {
      const contract = JSON.parse($output.text()) as DeckMasterContractV1;
      expect(contract.aiEditZones?.find((zone) => zone.id === insertedId)?.textAlign)
        .to.equal("right");
    });

    cy.then(() => dragKonvaElement(insertedId, 54, 32));
    cy.get('[data-testid="master-contract-snapshot"]').should(($output) => {
      const contract = JSON.parse($output.text()) as DeckMasterContractV1;
      const dragged = contract.aiEditZones?.find((zone) => zone.id === insertedId);
      expect(dragged, "inserted AI zone after drag").to.exist;
      expect(dragged!.bounds).to.deep.equal(draggedBounds);
    });

    // A completed drag is exactly one history item. The browser command is
    // consumed so it cannot also affect the page or browser history.
    pressCanvasShortcut("z", { ctrlKey: true });
    cy.get('[data-testid="master-contract-snapshot"]').should(($output) => {
      const contract = JSON.parse($output.text()) as DeckMasterContractV1;
      expect(contract.aiEditZones?.find((zone) => zone.id === insertedId)?.bounds)
        .to.deep.equal(insertedBounds);
    });
    pressCanvasShortcut("z", { ctrlKey: true, shiftKey: true });
    cy.get('[data-testid="master-contract-snapshot"]').should(($output) => {
      const contract = JSON.parse($output.text()) as DeckMasterContractV1;
      expect(contract.aiEditZones?.find((zone) => zone.id === insertedId)?.bounds)
        .to.deep.equal(draggedBounds);
    });
    cy.then(() => selectKonvaElement(insertedId));

    // Preview keeps a visible state marker while removing edit chrome and
    // canvas hit handling. The reserved envelope is editor-only and therefore
    // absent from the template/furniture preview.
    cy.get('[data-testid="master-studio-canvas"]')
      .should("have.attr", "data-preview-mode", "false")
      .and("have.attr", "data-editing-chrome", "visible");
    cy.get('[data-testid="master-preview-toggle"]')
      .click()
      .should("have.attr", "aria-pressed", "true");
    cy.get('[data-testid="master-preview-indicator"]').should("be.visible");
    cy.get('[data-testid="master-studio-preview-marker"]').should("be.visible");
    cy.get('[data-testid="master-wysiwyg-canvas-shell"]')
      .should("have.attr", "data-preview-mode", "true");
    cy.get('[data-testid="master-studio-canvas"]')
      .should("have.attr", "data-preview-mode", "true")
      .and("have.attr", "data-editing-chrome", "hidden");
    cy.get('[data-testid="master-studio-inline-editor"]').should("not.exist");

    cy.then(() => expectMountedKonvaNodeMissing(insertedId));
    cy.get('[data-testid="master-contract-snapshot"]').should(($output) => {
      const contract = JSON.parse($output.text()) as DeckMasterContractV1;
      expect(contract.aiEditZones?.find((zone) => zone.id === insertedId)?.bounds)
        .to.deep.equal(draggedBounds);
    });

    cy.get('[data-testid="master-preview-toggle"]')
      .click()
      .should("have.attr", "aria-pressed", "false");
    cy.get('[data-testid="master-preview-indicator"]').should("not.exist");
    cy.get('[data-testid="master-studio-preview-marker"]').should("not.exist");
    cy.get('[data-testid="master-wysiwyg-canvas-shell"]')
      .should("have.attr", "data-preview-mode", "false");
    cy.get('[data-testid="master-studio-canvas"]')
      .should("have.attr", "data-preview-mode", "false")
      .and("have.attr", "data-editing-chrome", "visible");
    cy.then(() => findMountedKonvaNode(insertedId));
    cy.get('[data-testid="master-studio-inline-editor"]').should("not.exist");

    // Undo the drag, then the toolbar Undo must remove the insertion itself;
    // merely opening and closing the unchanged editor must not add history.
    pressCanvasShortcut("z", { ctrlKey: true });
    cy.get('[data-testid="master-contract-snapshot"]').should(($output) => {
      const contract = JSON.parse($output.text()) as DeckMasterContractV1;
      expect(contract.aiEditZones?.find((zone) => zone.id === insertedId)?.bounds)
        .to.deep.equal(insertedBounds);
    });
    cy.contains("button", "Undo").click();
    cy.get('[data-testid="master-contract-snapshot"]').should(($output) => {
      const contract = JSON.parse($output.text()) as DeckMasterContractV1;
      expect(contract.aiEditZones?.find((zone) => zone.id === insertedId)?.textAlign)
        .to.equal("left");
    });
    cy.contains("button", "Undo").click();
    cy.get('[data-testid="master-contract-snapshot"]').should(($output) => {
      const contract = JSON.parse($output.text()) as DeckMasterContractV1;
      expect(
        Boolean(contract.aiEditZones?.some((zone) => zone.id === insertedId)),
      ).to.eq(false);
    });
    cy.get('[data-testid="master-studio-inline-editor"]').should("not.exist");
  });
});
