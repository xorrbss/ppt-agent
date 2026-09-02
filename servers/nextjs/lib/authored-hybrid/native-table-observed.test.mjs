import assert from "node:assert/strict";
import test from "node:test";

import {
  prepareObservedNativeTables,
  tablePayloadFromEvidence,
} from "./native-table-observed.ts";
import { serializeNativeTableFrame } from "./native-table.ts";
import { inspectPptxNativeElementReadback } from "./pptx-quality-inspection.ts";
import { readPptxArchive, writePptxArchive } from "./pptx-archive.ts";

const border = { color: "#D0D5DD", widthPx: 1 };
const cellStyle = (horizontalAlign = "left") => ({
  horizontalAlign,
  verticalAlign: "middle",
  borders: { bottom: border },
});

function kpiEvidence(overrides = {}) {
  return {
    rows: [
      [
        { text: "지표", fill: "#F2F4F7", textColor: "#101828", bold: true, fontFamily: "Pretendard", fontSizePt: 12 },
        { text: "Before", fill: "#F2F4F7", textColor: "#101828", bold: true, fontFamily: "Pretendard", fontSizePt: 12 },
        { text: "After", fill: "#F2F4F7", textColor: "#101828", bold: true, fontFamily: "Pretendard", fontSizePt: 12 },
      ],
      [
        { text: "처리 시간", textColor: "#344054", bold: false, fontFamily: "Pretendard", fontSizePt: 11 },
        { text: "48h", textColor: "#344054", bold: false, fontFamily: "Pretendard", fontSizePt: 11 },
        { text: "6h\n(-87%)", textColor: "#027A48", bold: true, fontFamily: "Pretendard", fontSizePt: 11 },
      ],
    ],
    columnWidthsPx: [200, 120, 120],
    rowHeightsPx: [40, 48],
    cellStyles: [
      [cellStyle(), cellStyle("center"), cellStyle("center")],
      [cellStyle(), cellStyle("center"), cellStyle("center")],
    ],
    rootFill: "#FFFFFF",
    ...overrides,
  };
}

function tableElement(overrides = {}) {
  return {
    id: "table.kpi",
    domPath: "body > section:nth-of-type(1) > table:nth-of-type(1)",
    tagName: "table",
    sourceIndex: 4,
    zOrder: 4,
    cssZIndex: null,
    bounds: {
      px: { x: 80, y: 200, width: 440, height: 88 },
      inches: { x: 80 / 96, y: 200 / 96, width: 440 / 96, height: 88 / 96 },
    },
    rotationDeg: 0,
    opacity: 1,
    classification: { mode: "raster", candidateKind: "complex", reasons: ["complex-table"] },
    tableEvidence: kpiEvidence(),
    nativeHint: "table",
    ...overrides,
  };
}

function cellText(id, domPathSuffix, text) {
  return {
    id,
    domPath: `body > section:nth-of-type(1) > table:nth-of-type(1) > ${domPathSuffix}`,
    tagName: "td",
    sourceIndex: 9,
    zOrder: 9,
    cssZIndex: null,
    bounds: { px: { x: 0, y: 0, width: 10, height: 10 }, inches: { x: 0, y: 0, width: 1, height: 1 } },
    rotationDeg: 0,
    opacity: 1,
    classification: { mode: "native", kind: "text", confidence: "safe" },
    text: { role: "body", plainText: text, paragraphs: [text], style: {}, runs: [] },
  };
}

function skeletonPptx() {
  return writePptxArchive(new Map([
    ["[Content_Types].xml", Buffer.from('<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="xml" ContentType="application/xml"/><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/></Types>')],
    ["ppt/presentation.xml", Buffer.from('<?xml version="1.0"?><p:presentation xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"><p:sldSz cx="12192000" cy="6858000"/></p:presentation>')],
    ["ppt/_rels/presentation.xml.rels", Buffer.from('<?xml version="1.0"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"/>')],
    ["ppt/slides/slide1.xml", Buffer.from('<?xml version="1.0"?><p:sld xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships" xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"><p:cSld><p:spTree><p:nvGrpSpPr><p:cNvPr id="1" name=""/><p:cNvGrpSpPr/><p:nvPr/></p:nvGrpSpPr><p:grpSpPr/></p:spTree></p:cSld></p:sld>')],
    ["ppt/slides/_rels/slide1.xml.rels", Buffer.from('<?xml version="1.0"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/slideLayout" Target="../slideLayouts/slideLayout1.xml"/></Relationships>')],
  ]));
}

test("browser table evidence becomes a bounded native table payload", () => {
  const payload = tablePayloadFromEvidence(kpiEvidence(), { embeddedTypefaceFamilies: ["Pretendard"] });
  assert.ok(payload);
  assert.equal(payload.rows.length, 2);
  assert.equal(payload.rows[1][0].fill, "#FFFFFF", "root fill applies to cells without their own fill");
  assert.equal(payload.rows[0][0].fill, "#F2F4F7");
  assert.equal(payload.rows[0][0].fontFamily, "Pretendard");
  assert.deepEqual(payload.columnWidths, [200, 120, 120]);
  assert.deepEqual(payload.rowHeightsPt, [30, 36]);
  assert.equal(payload.cellStyles[0][1].horizontalAlign, "center");
  assert.deepEqual(payload.cellStyles[1][2].borders, { bottom: { color: "#D0D5DD", widthPt: 0.75 } });
});

test("unembedded cell typefaces map to the central PowerPoint substitute", () => {
  const payload = tablePayloadFromEvidence(kpiEvidence());
  assert.equal(payload.rows[0][0].fontFamily, "Malgun Gothic");
});

test("tables with replaced content, unsupported paint, or ragged evidence stay raster", () => {
  assert.equal(tablePayloadFromEvidence(kpiEvidence({ containsReplacedContent: true })), null);
  assert.equal(tablePayloadFromEvidence(kpiEvidence({ unsupportedPaint: true })), null);
  assert.equal(tablePayloadFromEvidence(kpiEvidence({ columnWidthsPx: [1, 2] })), null);
  assert.equal(
    tablePayloadFromEvidence(kpiEvidence({ cellStyles: [[cellStyle({ bottom: { color: "#000000", widthPx: 40 } })]] })),
    null,
  );
});

test("observed admission requires the data-native opt-in and pure table CSS", () => {
  const admitted = prepareObservedNativeTables([tableElement()], undefined);
  assert.equal(admitted.tables.length, 1);
  assert.equal(admitted.tables[0].classification.kind, "table");
  assert.equal(admitted.rejections.size, 0);

  const noHint = prepareObservedNativeTables([tableElement({ nativeHint: undefined })], undefined);
  assert.equal(noHint.tables.length, 0);
  assert.equal(noHint.rejections.size, 0, "untagged tables keep today's raster behaviour silently");

  const rotated = prepareObservedNativeTables([tableElement({ rotationDeg: 3 })], undefined);
  assert.equal(rotated.rejections.get("table.kpi"), "unsupported-table-css");

  const filtered = prepareObservedNativeTables(
    [tableElement({ classification: { mode: "raster", candidateKind: "complex", reasons: ["complex-table", "filter"] } })],
    undefined,
  );
  assert.equal(filtered.rejections.get("table.kpi"), "unsupported-table-css");

  const noEvidence = prepareObservedNativeTables([tableElement({ tableEvidence: undefined })], undefined);
  assert.equal(noEvidence.rejections.get("table.kpi"), "unsupported-native-table");
});

test("compatibility mode hands cell text candidates to the admitted table", () => {
  const cells = [
    cellText("text.a", "tbody:nth-of-type(1) > tr:nth-of-type(1) > td:nth-of-type(1)", "지표"),
    cellText("text.b", "tbody:nth-of-type(1) > tr:nth-of-type(2) > td:nth-of-type(3)", "6h"),
  ];
  const unrelated = { ...cellText("text.c", "x", "outside"), domPath: "body > section:nth-of-type(2) > p:nth-of-type(1)" };
  const result = prepareObservedNativeTables([tableElement(), ...cells, unrelated], undefined);
  assert.deepEqual([...result.ownedDescendantIds].sort(), ["text.a", "text.b"]);
});

test("declared slides admit only leaf tables with table-only features", () => {
  const declared = (overrides) => [{
    elementId: "table.kpi",
    kind: "complexIsland",
    parentId: null,
    childIds: [],
    sourceDigest: "sha256:" + "0".repeat(64),
    requiredFeatures: ["table"],
    ...overrides,
  }];
  assert.equal(prepareObservedNativeTables([tableElement()], declared({})).tables.length, 1);
  assert.equal(
    prepareObservedNativeTables([tableElement()], declared({ childIds: ["cell.1"] })).rejections.get("table.kpi"),
    "table-has-declared-children",
  );
  assert.equal(
    prepareObservedNativeTables([tableElement()], declared({ requiredFeatures: ["table", "filter"] })).rejections.get("table.kpi"),
    "unsupported-native-table",
  );
  assert.equal(
    prepareObservedNativeTables([tableElement()], declared({ kind: "text" })).rejections.get("table.kpi"),
    "unsupported-native-table",
  );
  assert.equal(
    prepareObservedNativeTables([tableElement()], []).rejections.get("table.kpi"),
    "unsupported-native-table",
  );
  assert.equal(
    prepareObservedNativeTables([tableElement()], declared({})).ownedDescendantIds.size,
    0,
    "declared graphs own descendants explicitly, never by DOM path",
  );
});

test("multi-line cell text round-trips through DrawingML paragraphs", () => {
  const result = prepareObservedNativeTables([tableElement()], undefined, { embeddedTypefaceFamilies: ["Pretendard"] });
  const xml = serializeNativeTableFrame(result.tables[0], 7);
  assert.match(xml, /<a:gridCol w="1905000"\/>/, "column widths follow the browser geometry");
  assert.equal((xml.match(/<a:tr\b/g) ?? []).length, 2);
  const archive = readPptxArchive(skeletonPptx());
  const slidePath = "ppt/slides/slide1.xml";
  archive.set(slidePath, Buffer.from(archive.get(slidePath).toString("utf8").replace("</p:spTree>", `${xml}</p:spTree>`)));
  const readback = inspectPptxNativeElementReadback(writePptxArchive(archive));
  const table = readback.find((entry) => entry.elementId === "table.kpi")?.table;
  assert.ok(table);
  assert.deepEqual(table.cellTexts, ["지표", "Before", "After", "처리 시간", "48h", "6h\n(-87%)"]);
  assert.equal(table.cellStyles[0][1].horizontalAlign, "center");
  assert.deepEqual(table.cellStyles[1][2].borders.bottom, { color: "#D0D5DD", widthPt: 0.75 });
  assert.match(xml, /typeface="Pretendard"/);
});
