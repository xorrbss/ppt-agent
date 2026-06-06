// Regression test for the adaptive block-id edit binding (P4 / R2).
// Run: node lib/adaptiveBlockEdit.test.mjs   (Node >=23 strips TS types natively)
import assert from "node:assert/strict";
import { setAdaptiveBlockText, getAdaptiveBlockText } from "./adaptiveBlockEdit.ts";

const deck = () => ({
  archetype: "x",
  blocks: [
    { id: "title", type: "title", text: "T0" },
    { id: "bullets", type: "bullets", items: [{ id: "b1", text: "B1" }, { id: "b2", text: "B2" }] },
    { id: "s1", type: "stat", value: "Q1", label: "L1" },
    { id: "s2", type: "stat", value: "Q1", label: "L2" },
    { id: "col1", type: "column", heading: "H1", items: [{ id: "col1.1", text: "C11" }, { id: "col1.2", text: "C12" }] },
    { id: "card1", type: "card", title: "CT", text: "CX" },
    { id: "statement", type: "quote", text: "QT", attribution: "A" },
  ],
});

let passed = 0;
const check = (name, fn) => { fn(); passed++; console.log("ok -", name); };

check("flat block id updates block.text", () => {
  const d = deck();
  assert.equal(setAdaptiveBlockText(d.blocks, "title", "NEW"), true);
  assert.equal(d.blocks[0].text, "NEW");
});

check("nested item id updates only that item", () => {
  const d = deck();
  setAdaptiveBlockText(d.blocks, "b2", "NB2");
  assert.equal(d.blocks[1].items[1].text, "NB2");
  assert.equal(d.blocks[1].items[0].text, "B1");
});

check("dotted field updates the block field", () => {
  const d = deck();
  setAdaptiveBlockText(d.blocks, "s1.value", "42%");
  assert.equal(d.blocks[2].value, "42%");
});

check("dotted-looking comparison item id resolves to the item (not a field)", () => {
  const d = deck();
  setAdaptiveBlockText(d.blocks, "col1.1", "NEWC");
  assert.equal(d.blocks[4].items[0].text, "NEWC");
  assert.equal(d.blocks[4].heading, "H1");
  assert.ok(!("1" in d.blocks[4]), "must not create a stray '1' field on the column");
});

check("dotted heading/title/attribution fields", () => {
  const d = deck();
  setAdaptiveBlockText(d.blocks, "col1.heading", "H2");
  setAdaptiveBlockText(d.blocks, "card1.title", "CT2");
  setAdaptiveBlockText(d.blocks, "statement.attribution", "A2");
  assert.equal(d.blocks[4].heading, "H2");
  assert.equal(d.blocks[5].title, "CT2");
  assert.equal(d.blocks[6].attribution, "A2");
});

check("NO MISBIND: identical text in s1/s2 — editing s1.value leaves s2.value intact", () => {
  const d = deck();
  assert.equal(d.blocks[2].value, "Q1");
  assert.equal(d.blocks[3].value, "Q1"); // duplicate text on purpose
  setAdaptiveBlockText(d.blocks, "s1.value", "CHANGED");
  assert.equal(d.blocks[2].value, "CHANGED");
  assert.equal(d.blocks[3].value, "Q1"); // the duplicate is untouched
});

check("getAdaptiveBlockText reads every id form", () => {
  const d = deck();
  assert.equal(getAdaptiveBlockText(d, "title"), "T0");
  assert.equal(getAdaptiveBlockText(d, "b1"), "B1");
  assert.equal(getAdaptiveBlockText(d, "s1.value"), "Q1");
  assert.equal(getAdaptiveBlockText(d, "col1.2"), "C12");
  assert.equal(getAdaptiveBlockText(d, "statement.attribution"), "A");
});

check("unknown id is a safe no-op", () => {
  const d = deck();
  assert.equal(setAdaptiveBlockText(d.blocks, "nope", "x"), false);
  assert.equal(getAdaptiveBlockText(d, "nope"), undefined);
  assert.equal(setAdaptiveBlockText(undefined, "title", "x"), false);
});

console.log(`\n${passed} checks passed`);
