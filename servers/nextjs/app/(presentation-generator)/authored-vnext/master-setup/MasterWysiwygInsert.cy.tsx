import React, { useState } from "react";
import { NextIntlClientProvider } from "next-intl";

import enMessages from "@/i18n/messages/en.json";
import type {
  BrandAsset,
  DeckMasterContractV1,
  SemanticMasterElement,
} from "@/lib/authored-vnext/master/contracts";
import { DEFAULT_MASTER_CONTRACT } from "@/lib/authored-vnext/master/fixtures";

import MasterStudioCanvas from "./MasterStudioCanvas";

const KOREAN_TEXT = "고객 성장 전략";
const SHARED_LOGO: BrandAsset = {
  assetId: "asset:component-shared-logo",
  digest: `sha256:${"f".repeat(64)}`,
  altText: "Component shared logo",
};

function cloneContract(): DeckMasterContractV1 {
  return structuredClone(DEFAULT_MASTER_CONTRACT);
}

function Harness({ initialContract }: { initialContract?: DeckMasterContractV1 } = {}) {
  const [contract, setContract] = useState(() => initialContract ?? cloneContract());
  const [entry, setEntry] = useState(0);

  return (
    <NextIntlClientProvider locale="en" messages={enMessages} timeZone="UTC">
      <button type="button" onClick={() => setEntry((value) => value + 1)}>
        Re-enter WYSIWYG
      </button>
      <MasterStudioCanvas
        key={entry}
        contract={contract}
        layoutKind="standard"
        onUploadAsset={async () => SHARED_LOGO}
        onContractChange={setContract}
      />
      <output data-testid="wysiwyg-contract-snapshot">
        {JSON.stringify(contract)}
      </output>
    </NextIntlClientProvider>
  );
}

function snapshot() {
  return cy
    .get('[data-testid="wysiwyg-contract-snapshot"]')
    .invoke("text")
    .then((value) => JSON.parse(value) as DeckMasterContractV1);
}

function pressClipboardShortcut(key: "c" | "v") {
  cy.get('[data-testid="master-studio-canvas"]').then(($surface) => {
    const surface = $surface[0] as HTMLElement;
    surface.focus();
    const event = new KeyboardEvent("keydown", {
      key,
      code: key === "c" ? "KeyC" : "KeyV",
      ctrlKey: true,
      bubbles: true,
      cancelable: true,
    });
    surface.dispatchEvent(event);
    expect(event.defaultPrevented, `${key} shortcut default`).to.equal(true);
  });
}

describe("Master WYSIWYG semantic inserts", () => {
  it("pastes copied furniture into another layout and after editor re-entry", () => {
    cy.clearLocalStorage();
    cy.mount(<Harness />);

    cy.get('[data-testid="master-wysiwyg-workspace"] > header')
      .contains("button", "Fixed copy")
      .click();
    pressClipboardShortcut("c");

    cy.get('nav[aria-label="Layouts"]')
      .contains("button", "Section")
      .click();
    pressClipboardShortcut("v");
    snapshot().then((contract) => {
      const copies = (contract.semanticElements ?? []).filter(
        (element) => element.content.role === "fixed-copy",
      );
      expect(copies).to.have.length(2);
      expect(copies.some((element) =>
        element.scope.kind === "layout" &&
        element.scope.layoutId === contract.layouts.section.id,
      )).to.equal(true);
    });

    cy.contains("button", "Re-enter WYSIWYG").click();
    cy.get('nav[aria-label="Layouts"]')
      .contains("button", "Cover")
      .click();
    pressClipboardShortcut("v");
    snapshot().then((contract) => {
      const copies = (contract.semanticElements ?? []).filter(
        (element) => element.content.role === "fixed-copy",
      );
      expect(copies).to.have.length(3);
      expect(copies.some((element) =>
        element.scope.kind === "layout" &&
        element.scope.layoutId === contract.layouts.cover.id,
      )).to.equal(true);
      expect(new Set(copies.map((element) => element.id)).size).to.equal(3);
    });
  });

  it("inserts the default all-body shared logo as one undoable history item", () => {
    const initial = cloneContract();
    initial.brandAssets.logo = {
      assetId: "asset:legacy-master-logo",
      digest: `sha256:${"e".repeat(64)}`,
      altText: "Legacy master logo",
    };
    initial.furniture.logo.assetId = initial.brandAssets.logo.assetId;

    cy.mount(<Harness initialContract={initial} />);
    cy.get('[data-testid="shared-logo-scope"] select')
      .first()
      .should("have.value", "allBody");
    cy.get('[data-testid="shared-logo-scope"]')
      .should("contain.text", "All body layouts (recommended)")
      .and("contain.text", "Applies to 2 layouts")
      .and("contain.text", "Undo restores it");

    cy.contains("button", "Insert company logo").click();
    cy.get('[data-testid="master-wysiwyg-workspace"] input[type="file"]')
      .selectFile({
        contents: Cypress.Buffer.from("shared-logo"),
        fileName: "company-logo.png",
        mimeType: "image/png",
      }, { force: true });

    cy.get('[data-testid="wysiwyg-contract-snapshot"]').should(($output) => {
      const contract = JSON.parse($output.text()) as DeckMasterContractV1;
      const members = (contract.semanticElements ?? []).filter(
        (element) => element.content.sharedGroupId,
      );
      expect(members).to.have.length(2);
      expect(members.map((element) => element.scope)).to.deep.equal([
        { kind: "layout", layoutId: contract.layouts.longTitle.id },
        { kind: "layout", layoutId: contract.layouts.standard.id },
      ]);
      expect(new Set(members.map((element) => element.content.sharedGroupId)).size)
        .to.equal(1);
      expect(members.every((element) => element.content.assetId === SHARED_LOGO.assetId))
        .to.equal(true);
      expect(members.every((element) => element.content.digest === SHARED_LOGO.digest))
        .to.equal(true);
      expect(contract.brandAssets.logo).to.equal(null);
      expect(contract.furniture.logo.assetId).to.equal(undefined);
    });

    cy.contains("button", "Undo").click();
    cy.get('[data-testid="wysiwyg-contract-snapshot"]').should(($output) => {
      const contract = JSON.parse($output.text()) as DeckMasterContractV1;
      expect(Boolean(contract.semanticElements?.some((element) => element.content.sharedGroupId)))
        .to.equal(false);
      expect(contract.brandAssets.logo?.assetId).to.equal("asset:legacy-master-logo");
      expect(contract.furniture.logo.assetId).to.equal("asset:legacy-master-logo");
    });

    cy.contains("button", "Redo").click();
    cy.get('[data-testid="wysiwyg-contract-snapshot"]').should(($output) => {
      const contract = JSON.parse($output.text()) as DeckMasterContractV1;
      expect(
        contract.semanticElements?.filter((element) => element.content.sharedGroupId),
      ).to.have.length(2);
      expect(contract.brandAssets.logo).to.equal(null);
      expect(contract.furniture.logo.assetId).to.equal(undefined);
    });
  });

  it("inserts fixed copy and a page-number furniture field, then preserves both across re-entry", () => {
    cy.mount(<Harness />);
    cy.get('[data-testid="master-wysiwyg-workspace"] > header')
      .contains("button", "Image")
      .should("not.exist");
    cy.get('[data-testid="master-wysiwyg-workspace"] > header')
      .contains("button", "SVG")
      .should("not.exist");

    cy.get('[data-testid="master-wysiwyg-workspace"] > header')
      .contains("button", "Fixed copy")
      .click();

    snapshot().then((contract) => {
      const text = contract.semanticElements?.find(
        (element): element is SemanticMasterElement => element.type === "text",
      );
      expect(text, "inserted fixed-copy furniture").to.exist;
      expect(text!.content.role).to.equal("fixed-copy");
      expect(text!.scope).to.deep.equal({
        kind: "layout",
        layoutId: contract.layouts.standard.id,
      });
    });
    cy.get('[data-testid="master-studio-inline-editor"]').should("not.exist");
    cy.get('[data-testid="master-studio-canvas"]')
      .focus()
      .type("{enter}");
    cy.get('[data-testid="master-studio-inline-editor"]')
      .trigger("compositionstart")
      .then(($editor) => {
        const editor = $editor[0] as HTMLTextAreaElement;
        editor.value = KOREAN_TEXT;
        editor.dispatchEvent(new InputEvent("input", {
          bubbles: true,
          data: KOREAN_TEXT,
          inputType: "insertCompositionText",
        }));
      })
      .trigger("compositionend", { data: KOREAN_TEXT })
      .type("{ctrl}{enter}");

    snapshot()
      .its("semanticElements")
      .then((elements: SemanticMasterElement[]) => {
        expect(elements.find((element) => element.type === "text")?.content.text)
          .to.equal(KOREAN_TEXT);
      });

    cy.get('[data-testid="master-wysiwyg-workspace"] > header')
      .contains("button", "Page number")
      .click();
    snapshot().then((contract) => {
        const elements = contract.semanticElements ?? [];
        const pageNumber = elements.find((element) => element.type === "pageNumber");
        expect(pageNumber, "semantic slide-number element").to.exist;
        expect(pageNumber!.content.role).to.equal("slide-number");
        expect(pageNumber!.content.text).to.equal("");
        expect(pageNumber!.bounds).to.deep.equal({
          x: Math.round((contract.canvas.widthPx - 160) / 2),
          y: Math.round(
            contract.canvas.safeArea.y + contract.canvas.safeArea.height - 48,
          ),
          width: 160,
          height: 48,
        });
        expect(pageNumber!.style).to.include({
          fontSize: 28,
          fontWeight: 700,
          textAlign: "center",
          verticalAlign: "middle",
        });
      });

    cy.contains("button", "Undo").click();
    snapshot()
      .its("semanticElements")
      .then((elements: SemanticMasterElement[]) => {
        expect(elements.some((element) => element.type === "pageNumber")).to.equal(false);
      });
    cy.contains("button", "Redo").click();

    cy.contains("button", "Re-enter WYSIWYG").click();
    snapshot()
      .its("semanticElements")
      .then((elements: SemanticMasterElement[]) => {
        expect(elements.find((element) => element.type === "text")?.content.text)
          .to.equal(KOREAN_TEXT);
        expect(elements.filter((element) => element.type === "pageNumber"))
          .to.have.length(1);
      });
  });
});
