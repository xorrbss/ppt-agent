import assert from "node:assert/strict";
import test from "node:test";

import { createPowerPointQidExpectation } from "./powerpoint-qid-expectation.ts";

const digest = (letter) => `sha256:${letter.repeat(64)}`;

test("builds a PPTX-bound generic QID expectation from active and OOXML receipts", () => {
  const expectation = createPowerPointQidExpectation({
    mode: "hybrid",
    slides: [
      {
        slideNumber: 1,
        qidCoverage: [
          { elementId: "s01-title", kind: "text", logical: "native" },
          { elementId: "decor", kind: "shape", logical: "raster" },
        ],
        nativeElementReadback: [{
          slideNumber: 1,
          elementId: "s01-title",
          kind: "text",
          capabilityId: "editable.text.advanced.v1",
          autoFit: "shrink",
          textRunMap: [{ runIndex: 0, runId: "r0", utf16Start: 0, utf16Length: 5, textDigest: digest("c") }],
          textRunStyles: [{ runIndex: 0, runId: "r0", fontFamily: "Arial", eastAsianTypeface: "Arial", fontSizePt: 12, bold: false, italic: false, underline: false, color: "#000000" }],
          advancedTextDigest: digest("d"),
        }],
      },
      {
        slideNumber: 2,
        qidCoverage: [{ elementId: "sales", kind: "table", logical: "native" }],
        nativeElementReadback: [{
          slideNumber: 2,
          elementId: "sales",
          kind: "table",
          capabilityId: "editable.table.advanced.v1",
          table: {
            rows: 1,
            columns: 2,
            cellTexts: ["A", "10"],
            rowHeightsPt: [24],
            columnWidthsPt: [120, 80],
            mergedCells: [],
            cellStyles: [[{ fill: "#FFFFFF", horizontalAlign: "left", verticalAlign: "top", borders: { top: null, right: null, bottom: null, left: null } }, { fill: null, horizontalAlign: "right", verticalAlign: "middle", borders: { top: null, right: null, bottom: null, left: null } }]],
            tableStyleDigest: digest("e"),
          },
        }],
      },
    ],
  }, {
    presentationId: "deck-1",
    presentationRevision: 7,
    pptxSha256: "f".repeat(64),
    editableHybridProfileId: "authored-editable-hybrid-ko-en-basic-v1",
    activeBinding: {
      canonicalMode: "bundleGraph",
      canonicalDigest: digest("a"),
      sceneDigest: digest("b"),
      slides: [
        { slideId: "slide-1", declaredElements: [{ elementId: "s01-title", kind: "text" }, { elementId: "decor", kind: "shape" }] },
        { slideId: "slide-2", declaredElements: [{ elementId: "sales", kind: "table" }] },
      ],
    },
  });

  assert.ok(expectation);
  assert.equal(expectation.slideCount, 2);
  assert.equal(expectation.expectedCount, 3);
  assert.equal(expectation.pptxSha256, digest("f"));
  assert.deepEqual(expectation.entries.map((entry) => [entry.qid, entry.hybridDisposition, entry.capabilityId]), [
    ["s01-title", "native", "editable.text.advanced.v1"],
    ["s01-decor", "raster", "raster.owner.v1"],
    ["s02-sales", "native", "editable.table.advanced.v1"],
  ]);
  assert.deepEqual(expectation.entries[2].tableExpectation?.cells, [["A", "10"]]);
  assert.deepEqual(expectation.entries[2].tableExpectation?.rowHeightsMicroPoint, [24_000_000]);
  assert.match(expectation.entries[2].tableExpectation?.tableExpectationDigest ?? "", /^sha256:[0-9a-f]{64}$/);
});

test("rejects a pre-prefixed QID from another slide", () => {
  assert.throws(() => createPowerPointQidExpectation({
    mode: "hybrid",
    slides: [{
      slideNumber: 1,
      qidCoverage: [{ elementId: "s02-title", kind: "text", logical: "native" }],
      nativeElementReadback: [{
        slideNumber: 1,
        elementId: "s02-title",
        kind: "text",
        capabilityId: "editable.text.v1",
      }],
    }],
  }, {
    presentationId: "deck-1",
    presentationRevision: 7,
    pptxSha256: "f".repeat(64),
    editableHybridProfileId: "authored-editable-hybrid-ko-en-basic-v1",
    activeBinding: {
      canonicalMode: "bundleGraph",
      canonicalDigest: digest("a"),
      sceneDigest: digest("b"),
      slides: [{ slideId: "slide-1", declaredElements: [{ elementId: "s02-title", kind: "text" }] }],
    },
  }), /authored_powerpoint_qid_expectation_invalid:qid_slide_prefix/);
});

test("declines authority sidecars outside the bounded native profile", () => {
  assert.equal(createPowerPointQidExpectation({}, {
    presentationId: "deck-1",
    presentationRevision: 1,
    pptxSha256: "a".repeat(64),
    editableHybridProfileId: undefined,
    activeBinding: { canonicalMode: "legacyHtml", canonicalDigest: null, sceneDigest: null, slides: [] },
  }), null);
});
