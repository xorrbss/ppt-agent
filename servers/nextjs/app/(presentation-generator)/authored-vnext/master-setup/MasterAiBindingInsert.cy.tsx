import React, { useState } from "react";
import { NextIntlClientProvider } from "next-intl";

import enMessages from "@/i18n/messages/en.json";
import type {
  DeckMasterContractV1,
  SemanticMasterElement,
} from "@/lib/authored-vnext/master/contracts";
import { DEFAULT_MASTER_CONTRACT } from "@/lib/authored-vnext/master/fixtures";

import MasterStudioCanvas from "./MasterStudioCanvas";

const INSTRUCTION = "Summarize the strongest customer evidence.";

function cloneDefault(): DeckMasterContractV1 {
  return structuredClone(DEFAULT_MASTER_CONTRACT);
}

function Harness() {
  const [contract, setContract] = useState(cloneDefault);
  const [saved, setSaved] = useState(cloneDefault);
  const [entry, setEntry] = useState(0);

  return (
    <NextIntlClientProvider locale="en" messages={enMessages} timeZone="UTC">
      <button type="button" onClick={() => setSaved(structuredClone(contract))}>
        Save master
      </button>
      <button
        type="button"
        onClick={() => {
          setContract(structuredClone(saved));
          setEntry((value) => value + 1);
        }}
      >
        Re-enter saved master
      </button>
      <MasterStudioCanvas
        key={entry}
        contract={contract}
        layoutKind="standard"
        onContractChange={setContract}
      />
      <output data-testid="ai-binding-live-contract">{JSON.stringify(contract)}</output>
      <output data-testid="ai-binding-saved-contract">{JSON.stringify(saved)}</output>
    </NextIntlClientProvider>
  );
}

function readContract(testId: string) {
  return cy
    .get(`[data-testid="${testId}"]`)
    .invoke("text")
    .then((value) => JSON.parse(value) as DeckMasterContractV1);
}

function aiElement(contract: DeckMasterContractV1) {
  return contract.semanticElements?.find(
    (element) => element.type === "text" && element.content.role === "ai-text-zone",
  ) as SemanticMasterElement | undefined;
}

// Legacy semantic AI text bindings are compatibility-read only and are no
// longer part of furniture-only master setup acceptance.
describe.skip("Legacy Master AI auto-text binding", () => {
  it("persists the instruction and empty rule through save and re-entry", () => {
    cy.mount(<Harness />);

    cy.get('[data-testid="master-wysiwyg-workspace"] > header')
      .contains("button", "AI editing zone")
      .click();

    readContract("ai-binding-live-contract").then((contract) => {
      const element = aiElement(contract);
      expect(element, "inserted AI text zone").to.exist;
      expect(element!.content.aiBinding).to.deep.equal({
        instruction: "Write a concise label that fits this area.",
        exampleText: "AI-generated text",
        emptyBehavior: "fallback",
        fallbackText: "AI-generated text",
        maxChars: 80,
      });
      expect(element!.content.text).to.equal(element!.content.aiBinding?.exampleText);
    });

    // Insertion selects the new zone without covering it with an editor. Text
    // editing begins only from an explicit Enter or double-click gesture.
    cy.get('[data-testid="master-studio-inline-editor"]').should("not.exist");
    cy.contains("legend", "AI editing zone").should("be.visible");

    cy.contains("label", "Instruction")
      .find("textarea")
      .clear()
      .should("have.value", "");
    cy.contains("label", "Instruction")
      .find("textarea")
      .type(INSTRUCTION);
    cy.contains("label", "When empty")
      .find("select")
      .select("blank");

    let authoredGeometry: SemanticMasterElement["bounds"];
    let authoredStyle: SemanticMasterElement["style"];
    readContract("ai-binding-live-contract").then((contract) => {
      const element = aiElement(contract)!;
      authoredGeometry = structuredClone(element.bounds);
      authoredStyle = structuredClone(element.style);
      expect(element.content.aiBinding).to.deep.include({
        instruction: INSTRUCTION,
        emptyBehavior: "blank",
        exampleText: "AI-generated text",
        maxChars: 80,
      });
      expect(element.content.text).to.equal(element.content.aiBinding?.exampleText);
    });

    cy.contains("button", "Save master").click();
    readContract("ai-binding-saved-contract").then((contract) => {
      expect(aiElement(contract)?.content.aiBinding).to.deep.include({
        instruction: INSTRUCTION,
        emptyBehavior: "blank",
      });
    });
    cy.contains("button", "Re-enter saved master").click();

    readContract("ai-binding-live-contract").then((contract) => {
      const element = aiElement(contract)!;
      expect(element.content.aiBinding).to.deep.include({
        instruction: INSTRUCTION,
        emptyBehavior: "blank",
        exampleText: "AI-generated text",
        maxChars: 80,
      });
      expect(element.bounds).to.deep.equal(authoredGeometry);
      expect(element.style).to.deep.equal(authoredStyle);
    });
  });
});
