import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import test from "node:test";

import {
  materializeSlideParts,
  readSlideSemanticMetadata,
} from "./materializer.ts";

function canonicalJson(value) {
  if (Array.isArray(value)) return `[${value.map(canonicalJson).join(",")}]`;
  if (value !== null && typeof value === "object") {
    return `{${Object.entries(value)
      .sort(([left], [right]) => (left < right ? -1 : left > right ? 1 : 0))
      .map(([key, item]) => `${JSON.stringify(key)}:${canonicalJson(item)}`)
      .join(",")}}`;
  }
  return JSON.stringify(value);
}

function digest(value) {
  return `sha256:${createHash("sha256").update(canonicalJson(value), "utf8").digest("hex")}`;
}

function snapshot(kind) {
  const text = "alpha beta gamma";
  const pieces = ["alpha ", "beta ", "gamma"];
  const blockKind = kind === "evidence-stack" ? "evidence-card" : "process-step";
  const value = {
    schemaVersion: "authored-body-grammar/v1",
    styleId: kind === "evidence-stack" ? "visual-discovery" : "osb-smart-banking",
    kind,
    bodyRole: "standard-body",
    sourceTextDigest: `sha256:${createHash("sha256").update(text, "utf8").digest("hex")}`,
    bodySlot: { x: 90, y: 160, width: 1100, height: 470 },
    blocks: pieces.map((piece, index) => ({
      id: `body-block-${index + 1}`,
      kind: blockKind,
      sequence: index + 1,
      text: piece,
      bounds: kind === "evidence-stack"
        ? { x: 120 + index * 30, y: 180 + index * 145, width: 850 - index * 40, height: 125 }
        : { x: 110 + index * 360, y: 180, width: 330, height: 420 },
      textInsets: kind === "evidence-stack"
        ? { top: 10, right: 14, bottom: 10, left: 36 }
        : { top: 30, right: 12, bottom: 10, left: 12 },
      fontPt: 13,
      lineHeight: 1.12,
      fill: "#FFFFFF",
      stroke: "#B7C8E2",
      textColor: "#182235",
      accent: kind === "evidence-stack" ? "#2457D6" : "#0879B7",
    })),
  };
  return { ...value, digest: digest(value) };
}

function snapshotV2(kind, slideRole, blockKind, semanticRoles) {
  const v1 = snapshot("process-grid");
  const value = {
    ...v1,
    schemaVersion: "authored-body-grammar/v2",
    kind,
    archetype: kind,
    slideRole,
    contentRect: { ...v1.bodySlot },
    documentMetadata: null,
    blocks: v1.blocks.map((block, index) => ({
      ...block,
      kind: blockKind,
      semanticRole: semanticRoles[index],
    })),
  };
  delete value.digest;
  return { ...value, digest: digest(value) };
}

function request(grammar) {
  return {
    slideId: "slide:grammar",
    layoutId: "layout:standard",
    layoutPartName: "ppt/slideLayouts/slideLayout3.xml",
    placeholders: [
      { role: "standard-title", type: "title", idx: 0, fontFamily: "Arial", fontSizePt: 28, bold: true },
      { role: "standard-body", type: "body", idx: 1, fontFamily: "Arial", fontSizePt: 16 },
    ],
    values: {
      "standard-title": grammar.title?.lines.join("\n") ?? "Grammar",
      "standard-body": "alpha beta gamma",
      __bodyGrammar: JSON.stringify(grammar),
    },
    pageNumberFurniture: {
      idx: 6,
      bounds: { x: 610, y: 680, width: 60, height: 22 },
      fontFamily: "Malgun Gothic",
      fontSizePt: 10,
      fill: "#6D7785",
    },
    masterDigest: "sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
    contractVersion: "deckmaster-contract/v1",
  };
}

function snapshotV3() {
  const text = "alpha beta gamma";
  const referenceLayoutDigest = "sha256:" + "b".repeat(64);
  const referenceSourceDigest = "sha256:" + "c".repeat(64);
  const textStyle = {
    fontFamily: "Noto Sans KR",
    fontPt: 16,
    fontWeight: 500,
    color: "#17335C",
    alignment: "left",
    lineHeight: 1.15,
    verticalAlignment: "middle",
  };
  const value = {
    schemaVersion: "authored-body-grammar/v3",
    styleId: "osb-smart-banking",
    kind: "timeline",
    archetype: "timeline",
    slideRole: "content",
    bodyRole: "standard-body",
    sourceTextDigest: `sha256:${createHash("sha256").update(text, "utf8").digest("hex")}`,
    bodySlot: { x: 90, y: 160, width: 1100, height: 470 },
    contentRect: { x: 90, y: 160, width: 1100, height: 470 },
    referenceLayoutId: "osb:timeline:17",
    referenceLayoutDigest,
    referenceSourceDigest,
    title: {
      bounds: { x: 170, y: 54, width: 780, height: 58 },
      textStyle: { ...textStyle, fontPt: 30, fontWeight: 700 },
      text: "Grammar retained",
      lines: ["Grammar", "retained"],
      accent: {
        kind: "ellipse",
        bounds: { x: 120, y: 64, width: 24, height: 24 },
        fill: "#0B66D4",
        zIndex: 4,
      },
    },
    blocks: [
      ["alpha ", "timeline-step", "ellipse", 120],
      ["beta ", "metric", "roundRect", 460],
      ["gamma", "architecture-layer", "rect", 800],
    ].map(([piece, semanticRole, shape, x], index) => ({
      id: `osb-layer-${index + 1}`,
      semanticRole,
      bounds: { x, y: 260, width: 260, height: 180 },
      shape,
      fill: index === 0 ? "#EAF4FF" : "#FFFFFF",
      stroke: "#75A7DB",
      strokeWidth: 1.5,
      textInsets: { top: 16, right: 16, bottom: 16, left: 16 },
      textStyle,
      zIndex: 20 + index,
      text: piece,
      fontPt: 15,
    })),
    decorations: [{
      id: "timeline-connector",
      semanticRole: "process-connector",
      kind: "line",
      bounds: { x: 240, y: 345, width: 680, height: 1 },
      stroke: "#0B66D4",
      strokeWidth: 2,
      zIndex: 10,
    }],
    semanticRolesUsed: ["timeline-step", "metric", "architecture-layer"],
    documentMetadata: null,
  };
  return { ...value, digest: digest(value) };
}

test("materializer renders the pinned grammar and preserves source text exactly once", () => {
  const visual = snapshot("evidence-stack");
  const osb = snapshot("process-grid");
  const visualParts = materializeSlideParts(request(visual));
  const osbParts = materializeSlideParts(request(osb));
  const visualXml = visualParts.slideXml.toString("utf8");
  const osbXml = osbParts.slideXml.toString("utf8");

  assert.equal(
    visual.digest,
    "sha256:861f93b637991a9e9d3e4fa19295bfb710a67aedf5622624dffeeee8c60d09fd",
    "TypeScript canonical digest must match the Python canonical_digest fixture",
  );
  assert.equal(visualParts.semanticMetadata.bodyGrammarDigest, visual.digest);
  assert.equal(readSlideSemanticMetadata(visualParts.slideXml).bodyGrammarDigest, visual.digest);
  assert.equal([...visualXml.matchAll(/<v:bodyBlock\b/g)].length, 3);
  for (const piece of ["alpha ", "beta ", "gamma"]) {
    assert.equal(visualXml.split(piece).length - 1, 1);
    assert.equal(osbXml.split(piece).length - 1, 1);
  }
  assert.match(visualXml, /evidence-stack:body-block-1/);
  assert.match(visualXml, /<a:prstGeom prst="roundRect"/);
  assert.match(osbXml, /process-grid:body-block-1/);
  assert.doesNotMatch(osbXml, /<a:prstGeom prst="roundRect"/);
});

test("materializer strictly materializes v2 archetypes with their semantic roles", () => {
  const metrics = snapshotV2(
    "data-metrics",
    "content",
    "metric",
    ["metric-kpi", "metric-comparison", "metric-trend"],
  );
  const timeline = snapshotV2(
    "timeline",
    "content",
    "timeline-step",
    ["timeline-start", "timeline-milestone", "timeline-outcome"],
  );
  metrics.documentMetadata = {
    kind: "documentNumber",
    value: "IBIZ-RFP-2026-08",
    visible: true,
  };
  const unsignedMetrics = { ...metrics };
  delete unsignedMetrics.digest;
  metrics.digest = digest(unsignedMetrics);
  const metricsParts = materializeSlideParts(request(metrics));
  const timelineParts = materializeSlideParts(request(timeline));
  const metricsXml = metricsParts.slideXml.toString("utf8");
  const timelineXml = timelineParts.slideXml.toString("utf8");

  assert.equal(metricsParts.semanticMetadata.bodyGrammarSchemaVersion, "authored-body-grammar/v2");
  assert.equal(metricsParts.semanticMetadata.bodyGrammarArchetype, "data-metrics");
  assert.equal(metricsParts.semanticMetadata.bodyGrammarSlideRole, "content");
  assert.equal(readSlideSemanticMetadata(timelineParts.slideXml).bodyGrammarArchetype, "timeline");
  assert.match(metricsXml, /kind="metric" sequence="1" semanticRole="metric-kpi"/);
  assert.match(timelineXml, /kind="timeline-step" sequence="3" semanticRole="timeline-outcome"/);
  assert.match(metricsXml, /bodyGrammarArchetype="data-metrics"/);
  assert.match(timelineXml, /bodyGrammarSlideRole="content"/);
  assert.match(metricsXml, /name="semantic:document-id"/);
  assert.match(metricsXml, /v:documentMetadata[^>]*kind="documentNumber"[^>]*value="IBIZ-RFP-2026-08"/);
  assert.deepEqual(readSlideSemanticMetadata(metricsParts.slideXml).documentMetadata, {
    kind: "documentNumber",
    value: "IBIZ-RFP-2026-08",
    visible: true,
  });
});

test("materializer uses retained v3 title, layer geometry, and readback evidence", () => {
  const grammar = snapshotV3();
  const input = request(grammar);
  input.values.__resolvedSnapshotEvidence = JSON.stringify({
    schemaVersion: "resolved-snapshot-evidence/v1",
    resolvedTemplateId: "artifact-template-ai/osb-proposal-1-29",
    resolvedReferenceLayoutDigest: grammar.referenceLayoutDigest,
    resolvedCompositionDigest: "sha256:" + "d".repeat(64),
    resolvedRuleDigest: "sha256:" + "e".repeat(64),
    pageNumber: 2,
    slideCount: 5,
    semanticRolesUsed: grammar.semanticRolesUsed,
    layoutRolesUsed: [grammar.referenceLayoutId, grammar.archetype, grammar.slideRole],
    bodyGrammarDigest: grammar.digest,
  });
  const parts = materializeSlideParts(input);
  const xml = parts.slideXml.toString("utf8");
  const readback = readSlideSemanticMetadata(parts.slideXml);

  assert.equal(
    xml.split("<a:t>Grammar</a:t>").length - 1,
    1,
    "retained title must replace the master placeholder text",
  );
  assert.equal(
    xml.split("<a:t>retained</a:t>").length - 1,
    1,
    "canonical explicit title lines must be emitted as separate OOXML paragraphs",
  );
  assert.match(xml, /name="retained:title"/);
  assert.match(xml, /<a:off x="1619250" y="514350"\/>/);
  assert.match(xml, /<a:prstGeom prst="ellipse"/);
  assert.match(xml, /<a:prstGeom prst="roundRect"/);
  assert.match(xml, /<p:cxnSp>/);
  assert.doesNotMatch(xml, /<p:ph\b[^>]*type="(?:title|body)"/);
  assert.doesNotMatch(xml, /name="placeholder:/);
  assert.match(xml, /name="furniture:page-number:slide"/);
  assert.match(xml, /<p:ph type="sldNum" idx="6"\/>/);
  assert.match(xml, /<a:fld[^>]*type="slidenum"[^>]*>[\s\S]*?<a:t>2<\/a:t>/);
  assert.deepEqual(readback.roles, [
    { role: "standard-title", type: "title", idx: 0 },
    { role: "standard-body", type: "body", idx: 1 },
  ]);
  assert.equal(readback.referenceLayoutId, "osb:timeline:17");
  assert.equal(readback.referenceLayoutDigest, grammar.referenceLayoutDigest);
  assert.equal(readback.resolvedCompositionDigest, "sha256:" + "d".repeat(64));
  assert.equal(readback.pageNumber, 2);
  assert.equal(readback.slideCount, 5);
  assert.deepEqual(readback.semanticRolesUsed, grammar.semanticRolesUsed);
});

test("materializer accepts the retained v3 process archetype render kind", () => {
  const grammar = snapshotV3();
  grammar.kind = "process-grid";
  grammar.archetype = "process";
  const unsigned = { ...grammar };
  delete unsigned.digest;
  grammar.digest = digest(unsigned);
  const input = request(grammar);
  input.values.__resolvedSnapshotEvidence = JSON.stringify({
    schemaVersion: "resolved-snapshot-evidence/v1",
    resolvedTemplateId: "artifact-template-ai/osb-proposal-1-29",
    resolvedReferenceLayoutDigest: grammar.referenceLayoutDigest,
    resolvedCompositionDigest: "sha256:" + "d".repeat(64),
    resolvedRuleDigest: "sha256:" + "e".repeat(64),
    pageNumber: 1,
    slideCount: 1,
    semanticRolesUsed: grammar.semanticRolesUsed,
    layoutRolesUsed: [grammar.referenceLayoutId, grammar.archetype, grammar.slideRole],
    bodyGrammarDigest: grammar.digest,
  });

  const parts = materializeSlideParts(input);
  assert.equal(parts.semanticMetadata.bodyGrammarArchetype, "process");
});

test("materializer inserts digest-pinned retained native layers and element assets", () => {
  const grammar = snapshotV3();
  const png = Buffer.from("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII=", "base64");
  const assetDigest = `sha256:${createHash("sha256").update(png).digest("hex")}`;
  const fragment = `<p:pic xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main" xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><p:nvPicPr><p:cNvPr id="7" name="template-mark"/><p:cNvPicPr/><p:nvPr/></p:nvPicPr><p:blipFill><a:blip r:embed="rImage1"/><a:stretch><a:fillRect/></a:stretch></p:blipFill><p:spPr><a:xfrm><a:off x="100000" y="100000"/><a:ext cx="300000" cy="200000"/></a:xfrm><a:prstGeom prst="rect"><a:avLst/></a:prstGeom></p:spPr></p:pic>`;
  const fragmentDigest = `sha256:${createHash("sha256").update(fragment, "utf8").digest("hex")}`;
  const scenePayload = {
    schemaVersion: "retained-native-scene/v1",
    sourcePackageDigest: grammar.referenceSourceDigest,
    sourceSlideNumber: 7,
    sourceSlidePart: "ppt/slides/slide7.xml",
    sourceCanvas: { width: 10691813, height: 6840538 },
    targetCanvas: { width: 1280, height: 720 },
    transform: { offsetX: 0, offsetY: 0, scaleX: 1280 / 10691813, scaleY: 720 / 6840538 },
    layers: ["master", "layout", "slide"].map((kind) => ({
      kind,
      sourcePart: kind === "master" ? "ppt/slideMasters/slideMaster1.xml" : kind === "layout" ? "ppt/slideLayouts/slideLayout10.xml" : "ppt/slides/slide7.xml",
      sourcePartDigest: "sha256:" + "a".repeat(64),
      elements: kind === "slide" ? [{
        sourceShapeId: "pptx-shape-7",
        sourceIndex: 2,
        zIndex: 0,
        kind: "image",
        materialization: "native-ooxml",
        bounds: { x: 100000, y: 100000, width: 300000, height: 200000 },
        sourceXmlDigest: fragmentDigest,
        fragmentDigest,
        fragmentXml: fragment,
        native: { fragmentXml: fragment },
        relationshipIds: ["rImage1"],
        relationships: [{
          id: "rImage1",
          type: "image",
          target: "ppt/media/image13.png",
          external: false,
          targetDigest: assetDigest,
          targetMediaType: "image/png",
          targetBytesBase64: png.toString("base64"),
        }],
        bindingRole: null,
        textPolicy: "preserve-template-fixed",
      }] : [],
      elementCount: kind === "slide" ? 1 : 0,
      sourceElementCount: kind === "slide" ? 1 : 0,
      excludedBindingShapeIds: [],
      backgroundFragmentXml: null,
      backgroundFragmentDigest: null,
    })),
    partClosure: [],
    bindings: { titleShapeId: "pptx-shape-1", bodyShapeIds: ["pptx-shape-2"] },
    fullSlideRasterAllowed: false,
  };
  const scene = { ...scenePayload, digest: digest(scenePayload) };
  grammar.nativeScene = scene;
  grammar.nativeSceneDigest = scene.digest;
  const unsigned = { ...grammar };
  delete unsigned.digest;
  grammar.digest = digest(unsigned);
  const input = request(grammar);
  input.values.__resolvedSnapshotEvidence = JSON.stringify({
    schemaVersion: "resolved-snapshot-evidence/v1",
    resolvedTemplateId: "artifact-template-ai/osb-proposal-1-29",
    resolvedReferenceLayoutDigest: grammar.referenceLayoutDigest,
    resolvedCompositionDigest: "sha256:" + "d".repeat(64),
    resolvedRuleDigest: "sha256:" + "e".repeat(64),
    pageNumber: 1,
    slideCount: 1,
    semanticRolesUsed: grammar.semanticRolesUsed,
    layoutRolesUsed: [grammar.referenceLayoutId, grammar.archetype, grammar.slideRole],
    bodyGrammarDigest: grammar.digest,
  });

  const parts = materializeSlideParts(input);
  const xml = parts.slideXml.toString("utf8");
  const rels = parts.relationshipsXml.toString("utf8");
  assert.match(xml, /name="retained-native:slide"/);
  assert.match(xml, /name="template-mark"/);
  assert.doesNotMatch(xml, /r:embed="rImage1"/);
  assert.match(rels, /Id="rIdNative1000"[^>]+\/image/);
  assert.equal(parts.binaryParts.length, 1);
  assert.equal(parts.binaryParts[0].bytes.equals(png), true);
});

test("materializer rejects a grammar that drops source text", () => {
  const grammar = snapshot("evidence-stack");
  grammar.blocks[2].text = "gamm";
  const unsigned = { ...grammar };
  delete unsigned.digest;
  grammar.digest = digest(unsigned);

  assert.throws(
    () => materializeSlideParts(request(grammar)),
    /preserve source text exactly once/,
  );
});

test("materializer rejects unsafe and out-of-slot body grammar bounds", () => {
  const unsafe = snapshot("evidence-stack");
  unsafe.blocks[0].bounds.x = 1e300;
  assert.throws(
    () => materializeSlideParts(request(unsafe)),
    /body grammar block 0.x is invalid/,
  );

  const escaped = snapshot("process-grid");
  escaped.blocks[0].bounds.x = escaped.bodySlot.x - 1;
  const unsigned = { ...escaped };
  delete unsigned.digest;
  escaped.digest = digest(unsigned);
  assert.throws(
    () => materializeSlideParts(request(escaped)),
    /remain inside its body slot/,
  );
});
