// Regression test for the adaptive block-id edit binding (P4 / R2).
// Run: node lib/adaptiveBlockEdit.test.mjs   (Node >=23 strips TS types natively)
import assert from "node:assert/strict";
import {
  setAdaptiveBlockText,
  setAdaptiveChartBlock,
  getAdaptiveBlockText,
  locateUnit,
  deleteAdaptiveUnit,
  moveAdaptiveUnit,
  addAdaptiveUnit,
} from "./adaptiveBlockEdit.ts";

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

// --- P4b CRUD --- //

check("locateUnit finds top-level block and nested item", () => {
  const d = deck();
  assert.equal(locateUnit(d.blocks, "s1").array, d.blocks); // top-level
  assert.equal(locateUnit(d.blocks, "b1").array, d.blocks[1].items); // nested item
  assert.equal(locateUnit(d.blocks, "nope"), null);
});

check("deleteAdaptiveUnit removes a nested item only", () => {
  const d = deck();
  assert.equal(deleteAdaptiveUnit(d.blocks, "b2"), true);
  assert.equal(d.blocks[1].items.length, 1);
  assert.equal(d.blocks[1].items[0].id, "b1");
});

check("deleteAdaptiveUnit removes a top-level block (stat)", () => {
  const d = deck();
  const before = d.blocks.length;
  assert.equal(deleteAdaptiveUnit(d.blocks, "s2"), true);
  assert.equal(d.blocks.length, before - 1);
  assert.ok(!d.blocks.some((b) => b.id === "s2"));
});

check("deleteAdaptiveUnit keeps the last sibling in an array", () => {
  const d = { blocks: [{ id: "bl", type: "bullets", items: [{ id: "x1", text: "only" }] }] };
  assert.equal(deleteAdaptiveUnit(d.blocks, "x1"), false);
  assert.equal(d.blocks[0].items.length, 1);
});

check("moveAdaptiveUnit swaps within items[] and within blocks[]", () => {
  const d = deck();
  assert.equal(moveAdaptiveUnit(d.blocks, "b1", 1), true);
  assert.equal(d.blocks[1].items.map((i) => i.id).join(","), "b2,b1");
  const s1Idx = d.blocks.findIndex((b) => b.id === "s1");
  assert.equal(moveAdaptiveUnit(d.blocks, "s1", 1), true);
  assert.equal(d.blocks[s1Idx].id, "s2"); // s1 moved past s2
  assert.equal(moveAdaptiveUnit(d.blocks, "title", -1), false); // out of bounds
});

check("addAdaptiveUnit inserts a blank sibling with a fresh unique id", () => {
  const d = deck();
  const id = addAdaptiveUnit(d.blocks, "b1");
  assert.ok(id && id !== "b1" && id !== "b2");
  const items = d.blocks[1].items;
  assert.equal(items.length, 3);
  assert.equal(items[1].id, id); // inserted after b1
  assert.equal(items[1].text, ""); // blank
});

check("addAdaptiveUnit clones a card shape blank (type kept, fields blank, no icon)", () => {
  const d = { blocks: [{ id: "card1", type: "card", title: "T", text: "X", icon: { __icon_url__: "u", __icon_query__: "q" } }] };
  const id = addAdaptiveUnit(d.blocks, "card1");
  const nu = d.blocks[1];
  assert.equal(nu.id, id);
  assert.equal(nu.type, "card");
  assert.equal(nu.title, "");
  assert.equal(nu.text, "");
  assert.ok(!("icon" in nu)); // object field dropped
});

check("CRUD on unknown id is a safe no-op", () => {
  const d = deck();
  assert.equal(deleteAdaptiveUnit(d.blocks, "nope"), false);
  assert.equal(moveAdaptiveUnit(d.blocks, "nope", 1), false);
  assert.equal(addAdaptiveUnit(d.blocks, "nope"), null);
});

check("setAdaptiveChartBlock merges chart type/data/series by id", () => {
  const blocks = [
    { id: "c", type: "chart", chartType: "bar", data: [{ name: "a", value: 1 }] },
  ];
  const ok = setAdaptiveChartBlock(blocks, "c", {
    chartType: "line",
    data: [{ name: "x", value: 9 }],
    series: ["A", "B"],
  });
  assert.equal(ok, true);
  assert.equal(blocks[0].chartType, "line");
  assert.deepEqual(blocks[0].data, [{ name: "x", value: 9 }]);
  assert.deepEqual(blocks[0].series, ["A", "B"]);
});

check("setAdaptiveChartBlock applies only the provided fields", () => {
  const blocks = [{ id: "c", type: "chart", chartType: "bar", data: [{ name: "a", value: 1 }] }];
  setAdaptiveChartBlock(blocks, "c", { chartType: "area" });
  assert.equal(blocks[0].chartType, "area");
  assert.deepEqual(blocks[0].data, [{ name: "a", value: 1 }]); // untouched
});

check("setAdaptiveChartBlock on unknown id is a safe no-op", () => {
  const blocks = [{ id: "c", type: "chart", chartType: "bar", data: [] }];
  assert.equal(setAdaptiveChartBlock(blocks, "nope", { chartType: "line" }), false);
  assert.equal(blocks[0].chartType, "bar");
});

console.log(`\n${passed} checks passed`);
