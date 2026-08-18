import { createHash } from "node:crypto";

import { xmlEscape } from "./package-ops.ts";

const XML_HEADER = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>';
const P_NS = "http://schemas.openxmlformats.org/presentationml/2006/main";
const A_NS = "http://schemas.openxmlformats.org/drawingml/2006/main";
const R_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships";
const REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships";
const VNEXT_NS = "urn:presenton:ooxml-vnext:semantic-metadata:v1";
const VNEXT_EXT_URI = "{7D786E45-7874-4D65-7461-646174613A31}";
const BODY_GRAMMAR_VALUE_KEY = "__bodyGrammar";
const BODY_GRAMMAR_V1_SCHEMA_VERSION = "authored-body-grammar/v1";
const BODY_GRAMMAR_V2_SCHEMA_VERSION = "authored-body-grammar/v2";
const BODY_GRAMMAR_V3_SCHEMA_VERSION = "authored-body-grammar/v3";
const BODY_GRAMMAR_V1_KINDS: ReadonlySet<string> = new Set([
  "evidence-stack",
  "process-grid",
  "content-cards",
]);
const BODY_GRAMMAR_V1_BLOCK_KINDS: ReadonlySet<string> = new Set([
  "evidence-card",
  "process-step",
  "content-card",
]);
const BODY_GRAMMAR_V2_KINDS: ReadonlySet<string> = new Set([
  "cover-statement",
  "section-bridge",
  "content-cards",
  "data-metrics",
  "timeline",
  "architecture",
  "process-grid",
  "closing",
]);
const BODY_GRAMMAR_V3_KINDS: ReadonlySet<string> = new Set([
  "cover",
  "cover-statement",
  "section",
  "section-bridge",
  "content",
  "content-cards",
  "data",
  "data-metrics",
  "timeline",
  "architecture",
  "process",
  "process-grid",
  "closing",
]);
const BODY_GRAMMAR_V2_BLOCK_KINDS: ReadonlySet<string> = new Set([
  "cover-summary",
  "section-summary",
  "content-card",
  "metric",
  "timeline-step",
  "architecture-layer",
  "process-step",
  "closing-message",
]);
const V2_BLOCK_KIND_BY_ARCHETYPE: Readonly<Record<string, string>> = {
  "cover-statement": "cover-summary",
  "section-bridge": "section-summary",
  "content-cards": "content-card",
  "data-metrics": "metric",
  timeline: "timeline-step",
  architecture: "architecture-layer",
  "process-grid": "process-step",
  closing: "closing-message",
};
const V3_ARCHETYPE_BY_KIND: Readonly<Record<string, string>> = {
  cover: "cover",
  "cover-statement": "cover",
  section: "section",
  "section-bridge": "section",
  content: "content",
  "content-cards": "content",
  data: "data",
  "data-metrics": "data",
  timeline: "timeline",
  architecture: "architecture",
  process: "process",
  "process-grid": "process",
  closing: "closing",
};
const MAX_BODY_GRAMMAR_PX = 100_000;
const RESOLVED_SNAPSHOT_EVIDENCE_VALUE_KEY = "__resolvedSnapshotEvidence";

export interface MaterializedPlaceholderDefinition {
  role: string;
  type: "title" | "body" | "ctrTitle" | "subTitle" | "obj";
  idx: number;
  fontFamily: string;
  fontSizePt: number;
  bold?: boolean;
}

export interface MaterializedPageNumberDefinition {
  idx: number;
  bounds: BodyGrammarBounds;
  fontFamily: string;
  fontSizePt: number;
  fill: string;
}

export interface MaterializedTextRun {
  readonly text: string;
  readonly bold?: boolean;
  readonly italic?: boolean;
  readonly fontFamily?: string;
}

export interface MaterializedRichText {
  readonly runs: readonly MaterializedTextRun[];
}

export type MaterializedTextValue = string | MaterializedRichText;

export interface MaterializeSlideRequest {
  slideId: string;
  layoutId: string;
  layoutPartName: string;
  placeholders: readonly MaterializedPlaceholderDefinition[];
  values: Readonly<Record<string, MaterializedTextValue>>;
  pageNumberFurniture?: MaterializedPageNumberDefinition;
  masterDigest: string;
  contractVersion: string;
}

export interface MaterializedSlideParts {
  slideXml: Buffer;
  relationshipsXml: Buffer;
  binaryParts: readonly {
    partName: string;
    bytes: Buffer;
    contentType: string;
  }[];
  semanticMetadata: {
    slideId: string;
    layoutId: string;
    masterDigest: string;
    placeholderRoleDigest: string;
    bodyGrammarDigest?: string;
    bodyGrammarSchemaVersion?: string;
    bodyGrammarArchetype?: string;
    bodyGrammarSlideRole?: string;
    referenceLayoutId?: string;
    referenceLayoutDigest?: string;
    referenceSourceDigest?: string;
    nativeSceneDigest?: string;
    resolvedTemplateId?: string;
    resolvedCompositionDigest?: string;
    resolvedRuleDigest?: string;
    pageNumber?: number;
    slideCount?: number;
    semanticRolesUsed?: readonly string[];
    layoutRolesUsed?: readonly string[];
    documentMetadata?: {
      kind: "rfp" | "documentNumber";
      value: string;
      visible: boolean;
    };
    roles: readonly { role: string; type: string; idx: number }[];
  };
}

function stableDigest(value: unknown): string {
  return createHash("sha256").update(JSON.stringify(value)).digest("hex");
}

function canonicalJson(value: unknown): string {
  if (Array.isArray(value)) return `[${value.map(canonicalJson).join(",")}]`;
  if (value !== null && typeof value === "object") {
    return `{${Object.entries(value as Record<string, unknown>)
      .sort(([left], [right]) => (left < right ? -1 : left > right ? 1 : 0))
      .map(([key, item]) => `${JSON.stringify(key)}:${canonicalJson(item)}`)
      .join(",")}}`;
  }
  const encoded = JSON.stringify(value);
  if (encoded === undefined) throw new Error("Body grammar is not canonical JSON");
  return encoded;
}

function canonicalDigest(value: unknown): string {
  return `sha256:${createHash("sha256").update(canonicalJson(value), "utf8").digest("hex")}`;
}

interface BodyGrammarBounds {
  x: number;
  y: number;
  width: number;
  height: number;
}

interface BodyGrammarBlock {
  id: string;
  kind: string;
  semanticRole?: string;
  sequence: number;
  text: string;
  bounds: BodyGrammarBounds;
  textInsets: { top: number; right: number; bottom: number; left: number };
  fontPt: number;
  lineHeight: number;
  fill: string;
  stroke: string;
  textColor: string;
  accent: string;
  shape?: "rect" | "roundRect" | "ellipse" | "line";
  strokeWidth?: number;
  zIndex?: number;
  textStyle?: BodyGrammarTextStyle;
}

interface BodyGrammarTextStyle {
  fontFamily: string;
  fontPt: number;
  fontWeight: number;
  color: string;
  alignment: "left" | "center" | "right";
  lineHeight: number;
  verticalAlignment: "top" | "middle" | "bottom";
}

interface BodyGrammarTitle {
  bounds: BodyGrammarBounds;
  textStyle: BodyGrammarTextStyle;
  text: string;
  lines: readonly string[];
  accent?: BodyGrammarDecoration;
}

interface BodyGrammarDecoration {
  id: string;
  semanticRole: "background-decoration" | "process-connector" | "template-accent";
  kind: "rect" | "roundRect" | "ellipse" | "line";
  bounds: BodyGrammarBounds;
  fill?: string;
  stroke?: string;
  strokeWidth?: number;
  zIndex: number;
}

interface RetainedNativeRelationship {
  id: string;
  type: "image";
  target: string;
  external: false;
  targetDigest: string;
  targetMediaType: string;
  targetBytesBase64: string;
}

interface RetainedNativeElement {
  sourceShapeId: string;
  sourceIndex: number;
  zIndex: number;
  kind: "shape" | "text" | "connector" | "image" | "table" | "chart" | "group" | "unsupported";
  materialization: "native-ooxml" | "element-raster-fallback";
  bounds: BodyGrammarBounds | null;
  fragmentDigest: string;
  fragmentXml: string;
  relationshipIds: readonly string[];
  relationships: readonly RetainedNativeRelationship[];
}

interface RetainedNativeScene {
  schemaVersion: "retained-native-scene/v1";
  sourcePackageDigest: string;
  sourceCanvas: { width: number; height: number };
  targetCanvas: { width: number; height: number };
  layers: readonly {
    kind: "master" | "layout" | "slide";
    sourcePart: string;
    elements: readonly RetainedNativeElement[];
  }[];
  fullSlideRasterAllowed: false;
  digest: string;
}

interface BodyGrammarSnapshot {
  schemaVersion:
    | typeof BODY_GRAMMAR_V1_SCHEMA_VERSION
    | typeof BODY_GRAMMAR_V2_SCHEMA_VERSION
    | typeof BODY_GRAMMAR_V3_SCHEMA_VERSION;
  styleId: string;
  kind: string;
  archetype?: string;
  slideRole?: "cover" | "section" | "content" | "closing";
  bodyRole: string;
  sourceTextDigest: string;
  bodySlot: BodyGrammarBounds;
  contentRect?: BodyGrammarBounds;
  referenceLayoutId?: string;
  referenceLayoutDigest?: string;
  referenceSourceDigest?: string;
  title?: BodyGrammarTitle;
  decorations?: readonly BodyGrammarDecoration[];
  semanticRolesUsed?: readonly string[];
  nativeScene?: RetainedNativeScene;
  nativeSceneDigest?: string;
  documentMetadata?: {
    kind: "rfp" | "documentNumber";
    value: string;
    visible: boolean;
  } | null;
  blocks: BodyGrammarBlock[];
  digest: string;
}

interface ResolvedSnapshotEvidence {
  schemaVersion: "resolved-snapshot-evidence/v1";
  resolvedTemplateId: string;
  resolvedReferenceLayoutDigest: string;
  resolvedCompositionDigest: string;
  resolvedRuleDigest: string;
  pageNumber: number;
  slideCount: number;
  semanticRolesUsed: readonly string[];
  layoutRolesUsed: readonly string[];
  bodyGrammarDigest: string;
}

function textValue(value: MaterializedTextValue): string {
  return typeof value === "string" ? value : value.runs.map(({ text }) => text).join("");
}

function canonicalTitleText(value: string): string {
  return value.trim().replace(/\s+/gu, " ");
}

function requireBounds(value: unknown, name: string): BodyGrammarBounds {
  if (value === null || typeof value !== "object" || Array.isArray(value)) {
    throw new Error(`${name} must be bounds`);
  }
  const record = value as Record<string, unknown>;
  const bounds = Object.fromEntries(["x", "y", "width", "height"].map((key) => {
    const number = record[key];
    if (
      typeof number !== "number"
      || !Number.isFinite(number)
      || number < 0
      || number > MAX_BODY_GRAMMAR_PX
      || (key !== "x" && key !== "y" && number <= 0)
    ) {
      throw new Error(`${name}.${key} is invalid`);
    }
    return [key, number];
  })) as unknown as BodyGrammarBounds;
  return bounds;
}

function requireInsets(value: unknown, name: string): BodyGrammarBlock["textInsets"] {
  if (value === null || typeof value !== "object" || Array.isArray(value)) {
    throw new Error(name + " must be text insets");
  }
  const record = value as Record<string, unknown>;
  return Object.fromEntries(["top", "right", "bottom", "left"].map((key) => {
    const number = record[key];
    if (
      typeof number !== "number"
      || !Number.isFinite(number)
      || number < 0
      || number > MAX_BODY_GRAMMAR_PX
    ) {
      throw new Error(name + "." + key + " is invalid");
    }
    return [key, number];
  })) as unknown as BodyGrammarBlock["textInsets"];
}

function requireColor(value: unknown, name: string): string {
  if (typeof value !== "string" || !/^#[0-9A-Fa-f]{6}$/.test(value)) {
    throw new Error(`${name} is not an RGB color`);
  }
  return value.toUpperCase();
}

function optionalColor(value: unknown, name: string): string | undefined {
  return value === undefined ? undefined : requireColor(value, name);
}

function requireDigest(value: unknown, name: string): string {
  if (typeof value !== "string" || !/^sha256:[0-9a-f]{64}$/.test(value)) {
    throw new Error(`${name} is not a canonical digest`);
  }
  return value;
}

function requirePositiveNumber(value: unknown, name: string, maximum: number): number {
  if (typeof value !== "number" || !Number.isFinite(value) || value <= 0 || value > maximum) {
    throw new Error(`${name} is invalid`);
  }
  return value;
}

function parseRetainedNativeScene(value: unknown, expectedDigest: unknown): RetainedNativeScene {
  if (value === null || typeof value !== "object" || Array.isArray(value)) {
    throw new Error("Retained native scene must be an object");
  }
  const scene = value as Record<string, unknown>;
  if (
    scene.schemaVersion !== "retained-native-scene/v1"
    || scene.fullSlideRasterAllowed !== false
    || !Array.isArray(scene.layers)
    || scene.layers.length !== 3
  ) {
    throw new Error("Retained native scene contract is invalid");
  }
  const digest = requireDigest(scene.digest, "nativeScene.digest");
  if (requireDigest(expectedDigest, "nativeSceneDigest") !== digest) {
    throw new Error("Retained native scene digest pin mismatch");
  }
  const unsigned = { ...scene };
  delete unsigned.digest;
  if (canonicalDigest(unsigned) !== digest) {
    throw new Error("Retained native scene digest mismatch");
  }
  const parseCanvas = (raw: unknown, name: string, maximum: number) => {
    if (raw === null || typeof raw !== "object" || Array.isArray(raw)) {
      throw new Error(`${name} is invalid`);
    }
    const canvas = raw as Record<string, unknown>;
    return {
      width: requirePositiveNumber(canvas.width, `${name}.width`, maximum),
      height: requirePositiveNumber(canvas.height, `${name}.height`, maximum),
    };
  };
  const sourceCanvas = parseCanvas(scene.sourceCanvas, "nativeScene.sourceCanvas", 100_000_000);
  const targetCanvas = parseCanvas(scene.targetCanvas, "nativeScene.targetCanvas", MAX_BODY_GRAMMAR_PX);
  const expectedLayerKinds = ["master", "layout", "slide"] as const;
  const layers = scene.layers.map((rawLayer, layerIndex) => {
    if (rawLayer === null || typeof rawLayer !== "object" || Array.isArray(rawLayer)) {
      throw new Error("Retained native scene layer is invalid");
    }
    const layer = rawLayer as Record<string, unknown>;
    if (
      layer.kind !== expectedLayerKinds[layerIndex]
      || typeof layer.sourcePart !== "string"
      || !/^ppt\/(?:slideMasters|slideLayouts|slides)\/[A-Za-z0-9_.-]+\.xml$/.test(layer.sourcePart)
      || !Array.isArray(layer.elements)
      || layer.elements.length > 2_000
    ) {
      throw new Error("Retained native scene layer contract is invalid");
    }
    let previousSourceIndex = -1;
    const elements = layer.elements.map((rawElement, elementIndex): RetainedNativeElement => {
      if (rawElement === null || typeof rawElement !== "object" || Array.isArray(rawElement)) {
        throw new Error("Retained native scene element is invalid");
      }
      const element = rawElement as Record<string, unknown>;
      const kinds = new Set(["shape", "text", "connector", "image", "table", "chart", "group", "unsupported"]);
      if (
        typeof element.sourceShapeId !== "string"
        || element.sourceShapeId.length === 0
        || !Number.isInteger(element.sourceIndex)
        || (element.sourceIndex as number) < 2
        || (element.sourceIndex as number) <= previousSourceIndex
        || element.zIndex !== elementIndex
        || !kinds.has(String(element.kind))
        || !["native-ooxml", "element-raster-fallback"].includes(String(element.materialization))
        || typeof element.fragmentXml !== "string"
        || element.fragmentXml.length === 0
        || element.fragmentXml.length > 2_000_000
        || /<\?xml|<!DOCTYPE|<!ENTITY/i.test(element.fragmentXml)
      ) {
        throw new Error("Retained native scene element identity is invalid");
      }
      previousSourceIndex = element.sourceIndex as number;
      const fragmentDigest = requireDigest(element.fragmentDigest, "nativeScene.element.fragmentDigest");
      const actualFragmentDigest = `sha256:${createHash("sha256").update(element.fragmentXml, "utf8").digest("hex")}`;
      if (actualFragmentDigest !== fragmentDigest) {
        throw new Error("Retained native scene element fragment digest mismatch");
      }
      if (!Array.isArray(element.relationshipIds) || !Array.isArray(element.relationships)) {
        throw new Error("Retained native scene element relationships are invalid");
      }
      const relationships = element.relationships.map((rawRelationship): RetainedNativeRelationship => {
        if (rawRelationship === null || typeof rawRelationship !== "object" || Array.isArray(rawRelationship)) {
          throw new Error("Retained native scene relationship is invalid");
        }
        const relationship = rawRelationship as Record<string, unknown>;
        if (
          typeof relationship.id !== "string"
          || !/^[A-Za-z][A-Za-z0-9._-]{0,127}$/.test(relationship.id)
          || relationship.type !== "image"
          || relationship.external !== false
          || typeof relationship.target !== "string"
          || typeof relationship.targetMediaType !== "string"
          || typeof relationship.targetBytesBase64 !== "string"
        ) {
          throw new Error("Only digest-pinned retained image relationships are supported");
        }
        const targetDigest = requireDigest(relationship.targetDigest, "nativeScene.relationship.targetDigest");
        const bytes = Buffer.from(relationship.targetBytesBase64, "base64");
        if (
          bytes.byteLength === 0
          || bytes.byteLength > 10_000_000
          || bytes.toString("base64") !== relationship.targetBytesBase64
          || `sha256:${createHash("sha256").update(bytes).digest("hex")}` !== targetDigest
        ) {
          throw new Error("Retained native scene relationship asset digest mismatch");
        }
        return relationship as unknown as RetainedNativeRelationship;
      });
      const relationshipIds = element.relationshipIds as unknown[];
      if (
        relationshipIds.some((id) => typeof id !== "string")
        || relationshipIds.length !== relationships.length
        || relationshipIds.some((id) => !relationships.some((relationship) => relationship.id === id))
      ) {
        throw new Error("Retained native scene relationship mapping is incomplete");
      }
      const bounds = element.bounds === null
        ? null
        : (() => {
            if (element.bounds === undefined) return null;
            const raw = element.bounds as Record<string, unknown>;
            return {
              x: Number(raw.x), y: Number(raw.y), width: Number(raw.width), height: Number(raw.height),
            };
          })();
      if (
        element.kind === "image"
        && element.materialization === "element-raster-fallback"
        && bounds
        && bounds.width * bounds.height >= sourceCanvas.width * sourceCanvas.height * 0.9
      ) {
        throw new Error("Full-slide retained raster elements are forbidden");
      }
      return {
        sourceShapeId: element.sourceShapeId,
        sourceIndex: element.sourceIndex as number,
        zIndex: element.zIndex as number,
        kind: element.kind as RetainedNativeElement["kind"],
        materialization: element.materialization as RetainedNativeElement["materialization"],
        bounds,
        fragmentDigest,
        fragmentXml: element.fragmentXml,
        relationshipIds: relationshipIds as string[],
        relationships,
      };
    });
    return {
      kind: layer.kind as "master" | "layout" | "slide",
      sourcePart: layer.sourcePart,
      elements,
    };
  });
  return {
    schemaVersion: "retained-native-scene/v1",
    sourcePackageDigest: requireDigest(scene.sourcePackageDigest, "nativeScene.sourcePackageDigest"),
    sourceCanvas,
    targetCanvas,
    layers,
    fullSlideRasterAllowed: false,
    digest,
  };
}

function requireTextStyle(value: unknown, name: string): BodyGrammarTextStyle {
  if (value === null || typeof value !== "object" || Array.isArray(value)) {
    throw new Error(`${name} must be text style`);
  }
  const item = value as Record<string, unknown>;
  const fontPt = item.fontPt;
  const fontWeight = item.fontWeight;
  const lineHeight = item.lineHeight;
  if (
    typeof item.fontFamily !== "string"
    || item.fontFamily.trim().length === 0
    || typeof fontPt !== "number"
    || !Number.isFinite(fontPt)
    || fontPt < 1
    || fontPt > 400
    || typeof fontWeight !== "number"
    || !Number.isFinite(fontWeight)
    || fontWeight < 100
    || fontWeight > 1000
    || typeof lineHeight !== "number"
    || !Number.isFinite(lineHeight)
    || lineHeight < 0.5
    || lineHeight > 5
    || !["left", "center", "right"].includes(String(item.alignment))
    || !["top", "middle", "bottom"].includes(String(item.verticalAlignment))
  ) {
    throw new Error(`${name} is invalid`);
  }
  return {
    fontFamily: item.fontFamily,
    fontPt,
    fontWeight,
    color: requireColor(item.color, `${name}.color`),
    alignment: item.alignment as BodyGrammarTextStyle["alignment"],
    lineHeight,
    verticalAlignment: item.verticalAlignment as BodyGrammarTextStyle["verticalAlignment"],
  };
}

function requireDecoration(value: unknown, index: number): BodyGrammarDecoration {
  if (value === null || typeof value !== "object" || Array.isArray(value)) {
    throw new Error(`Body grammar decoration ${index} is invalid`);
  }
  const item = value as Record<string, unknown>;
  if (
    typeof item.id !== "string"
    || item.id.trim().length === 0
    || !["background-decoration", "process-connector", "template-accent"].includes(
      String(item.semanticRole),
    )
    || !["rect", "roundRect", "ellipse", "line"].includes(String(item.kind))
    || typeof item.zIndex !== "number"
    || !Number.isInteger(item.zIndex)
    || item.zIndex < -100_000
    || item.zIndex > 100_000
  ) {
    throw new Error(`Body grammar decoration ${index} identity is invalid`);
  }
  const strokeWidth = item.strokeWidth === undefined ? 1 : item.strokeWidth;
  if (
    typeof strokeWidth !== "number"
    || !Number.isFinite(strokeWidth)
    || strokeWidth < 0
    || strokeWidth > 1_000
  ) {
    throw new Error(`Body grammar decoration ${index} strokeWidth is invalid`);
  }
  return {
    id: item.id,
    semanticRole: item.semanticRole as BodyGrammarDecoration["semanticRole"],
    kind: item.kind as BodyGrammarDecoration["kind"],
    bounds: requireBounds(item.bounds, `body grammar decoration ${index}`),
    fill: optionalColor(item.fill, `body grammar decoration ${index}.fill`),
    stroke: optionalColor(item.stroke, `body grammar decoration ${index}.stroke`),
    strokeWidth,
    zIndex: item.zIndex,
  };
}

function parseBodyGrammar(values: Readonly<Record<string, MaterializedTextValue>>): BodyGrammarSnapshot | null {
  const encoded = values[BODY_GRAMMAR_VALUE_KEY];
  if (encoded === undefined) return null;
  if (typeof encoded !== "string") throw new Error("Body grammar must be canonical JSON text");
  let value: unknown;
  try {
    value = JSON.parse(encoded);
  } catch {
    throw new Error("Body grammar is not valid JSON");
  }
  if (value === null || typeof value !== "object" || Array.isArray(value)) {
    throw new Error("Body grammar must be an object");
  }
  const record = value as Record<string, unknown>;
  const styleId = record.styleId;
  const schemaVersion = record.schemaVersion;
  const kind = record.kind;
  const bodyRole = record.bodyRole;
  const isV1 = schemaVersion === BODY_GRAMMAR_V1_SCHEMA_VERSION;
  const isV2 = schemaVersion === BODY_GRAMMAR_V2_SCHEMA_VERSION;
  const isV3 = schemaVersion === BODY_GRAMMAR_V3_SCHEMA_VERSION;
  if (!isV1 && !isV2 && !isV3) throw new Error("Unsupported body grammar schema");
  if (typeof styleId !== "string" || styleId.length === 0) throw new Error("Body grammar styleId is required");
  if (
    typeof kind !== "string"
    || !(isV1
      ? BODY_GRAMMAR_V1_KINDS
      : isV2
        ? BODY_GRAMMAR_V2_KINDS
        : BODY_GRAMMAR_V3_KINDS).has(kind)
  ) {
    throw new Error("Unsupported body grammar kind");
  }
  if (
    (isV2 && record.archetype !== kind)
    || (isV3 && record.archetype !== V3_ARCHETYPE_BY_KIND[kind])
  ) {
    throw new Error("Body grammar archetype must match kind");
  }
  if (
    (isV2 || isV3)
    && !["cover", "section", "content", "closing"].includes(
      String(record.slideRole),
    )
  ) {
    throw new Error("Body grammar slideRole is invalid");
  }
  if (typeof bodyRole !== "string" || !(bodyRole in values)) throw new Error("Body grammar role is missing");
  if (!Array.isArray(record.blocks) || record.blocks.length === 0 || record.blocks.length > 64) {
    throw new Error("Body grammar blocks are invalid");
  }
  const blocks = record.blocks.map((item, index): BodyGrammarBlock => {
    if (item === null || typeof item !== "object" || Array.isArray(item)) throw new Error("Body grammar block is invalid");
    const block = item as Record<string, unknown>;
    if (
      typeof block.id !== "string"
      || block.id.trim().length === 0
      || (!isV3 && block.id !== `body-block-${index + 1}`)
      || (!isV3 && block.sequence !== index + 1)
      || typeof block.text !== "string"
    ) {
      throw new Error("Body grammar block identity is invalid");
    }
    const allowedBlockKinds = isV1
      ? BODY_GRAMMAR_V1_BLOCK_KINDS
      : BODY_GRAMMAR_V2_BLOCK_KINDS;
    if (!isV3 && (typeof block.kind !== "string" || !allowedBlockKinds.has(block.kind))) {
      throw new Error("Body grammar block kind is invalid");
    }
    if (
      isV2
      && (
        block.kind !== V2_BLOCK_KIND_BY_ARCHETYPE[kind]
        || typeof block.semanticRole !== "string"
        || block.semanticRole.trim().length === 0
      )
    ) {
      throw new Error("Body grammar v2 block semantic role is invalid");
    }
    const textStyle = isV3
      ? requireTextStyle(block.textStyle, `body grammar block ${index}.textStyle`)
      : undefined;
    if (isV3 && (typeof block.semanticRole !== "string" || block.semanticRole.trim().length === 0)) {
      throw new Error("Body grammar v3 block semantic role is invalid");
    }
    const shape = isV3 ? String(block.shape) : undefined;
    if (isV3 && !["rect", "roundRect", "ellipse", "line"].includes(shape!)) {
      throw new Error("Body grammar v3 block shape is invalid");
    }
    const strokeWidth = isV3
      ? block.strokeWidth === undefined
        ? 1
        : block.strokeWidth
      : 1;
    const zIndex = isV3 ? block.zIndex : index;
    if (
      typeof strokeWidth !== "number"
      || !Number.isFinite(strokeWidth)
      || strokeWidth < 0
      || strokeWidth > 1_000
      || typeof zIndex !== "number"
      || !Number.isInteger(zIndex)
      || zIndex < -100_000
      || zIndex > 100_000
    ) {
      throw new Error("Body grammar v3 block layer values are invalid");
    }
    const fittedFontPt = typeof block.fontPt === "number"
      && Number.isFinite(block.fontPt)
      && block.fontPt >= 1
      && block.fontPt <= 400
      ? block.fontPt
      : (() => { throw new Error("Body grammar fontPt is invalid"); })();
    return {
      id: block.id,
      kind: isV3 ? String(block.semanticRole) : block.kind as string,
      ...((isV2 || isV3) ? { semanticRole: block.semanticRole as string } : {}),
      sequence: isV3 ? index + 1 : block.sequence as number,
      text: block.text,
      bounds: requireBounds(block.bounds, `body grammar block ${index}`),
      textInsets: requireInsets(block.textInsets, `body grammar block ${index}`),
      fontPt: fittedFontPt,
      lineHeight: typeof (isV3 ? textStyle!.lineHeight : block.lineHeight) === "number" && Number.isFinite(isV3 ? textStyle!.lineHeight : block.lineHeight) && (isV3 ? textStyle!.lineHeight : block.lineHeight as number) >= 0.5 && (isV3 ? textStyle!.lineHeight : block.lineHeight as number) <= 5
        ? (isV3 ? textStyle!.lineHeight : block.lineHeight as number)
        : (() => { throw new Error("Body grammar lineHeight is invalid"); })(),
      fill: isV3
        ? optionalColor(block.fill, "body grammar fill") ?? "#FFFFFF"
        : requireColor(block.fill, "body grammar fill"),
      stroke: isV3
        ? optionalColor(block.stroke, "body grammar stroke") ?? "#FFFFFF"
        : requireColor(block.stroke, "body grammar stroke"),
      textColor: isV3
        ? textStyle!.color
        : requireColor(block.textColor, "body grammar text color"),
      accent: isV3
        ? optionalColor(block.accent, "body grammar accent") ?? textStyle!.color
        : requireColor(block.accent, "body grammar accent"),
      ...(isV3
        ? {
            shape: shape as BodyGrammarBlock["shape"],
            strokeWidth,
            zIndex,
            textStyle: { ...textStyle!, fontPt: fittedFontPt },
          }
        : {}),
    };
  });
  const bodySlot = requireBounds(record.bodySlot, "body grammar slot");
  const contentRect = (isV2 || isV3)
    ? requireBounds(record.contentRect, "body grammar contentRect")
    : undefined;
  let documentMetadata: BodyGrammarSnapshot["documentMetadata"];
  if (isV2 || isV3) {
    const metadata = record.documentMetadata;
    if (metadata === null) {
      documentMetadata = null;
    } else if (
      typeof metadata === "object"
      && metadata !== null
      && !Array.isArray(metadata)
    ) {
      const metadataRecord = metadata as Record<string, unknown>;
      const kind = metadataRecord.kind;
      const value = metadataRecord.value;
      if (
        (kind !== "rfp" && kind !== "documentNumber")
        || typeof value !== "string"
        || value.trim().length === 0
        || value.length > 512
        || typeof metadataRecord.visible !== "boolean"
      ) {
        throw new Error("Body grammar v2 documentMetadata is invalid");
      }
      documentMetadata = { kind, value, visible: metadataRecord.visible };
    } else {
      throw new Error("Body grammar documentMetadata is required");
    }
  }
  let title: BodyGrammarTitle | undefined;
  let decorations: readonly BodyGrammarDecoration[] | undefined;
  let semanticRolesUsed: readonly string[] | undefined;
  let referenceLayoutId: string | undefined;
  let referenceLayoutDigest: string | undefined;
  let referenceSourceDigest: string | undefined;
  let nativeScene: RetainedNativeScene | undefined;
  let nativeSceneDigest: string | undefined;
  if (isV3) {
    const rawTitle = record.title;
    if (rawTitle === null || typeof rawTitle !== "object" || Array.isArray(rawTitle)) {
      throw new Error("Body grammar v3 title is invalid");
    }
    const titleRecord = rawTitle as Record<string, unknown>;
    const titleLines = titleRecord.lines;
    if (
      typeof titleRecord.text !== "string"
      || !Array.isArray(titleLines)
      || titleLines.length < 1
      || titleLines.length > 3
      || titleLines.some((line) => typeof line !== "string" || line.trim().length === 0)
      || canonicalTitleText(titleRecord.text)
        !== canonicalTitleText((titleLines as string[]).join(" "))
    ) {
      throw new Error("Body grammar v3 title text is invalid");
    }
    const accent = titleRecord.accent === undefined
      ? undefined
      : requireDecoration(
          {
            ...(titleRecord.accent as Record<string, unknown>),
            id: "title-accent",
            semanticRole: "template-accent",
            zIndex: (titleRecord.accent as Record<string, unknown>).zIndex ?? 0,
          },
          -1,
        );
    title = {
      bounds: requireBounds(titleRecord.bounds, "body grammar title.bounds"),
      textStyle: requireTextStyle(titleRecord.textStyle, "body grammar title.textStyle"),
      text: titleRecord.text,
      lines: titleLines as string[],
      ...(accent ? { accent } : {}),
    };
    if (!Array.isArray(record.decorations) || record.decorations.length > 128) {
      throw new Error("Body grammar v3 decorations are invalid");
    }
    decorations = record.decorations.map(requireDecoration);
    if (
      !Array.isArray(record.semanticRolesUsed)
      || record.semanticRolesUsed.length === 0
      || record.semanticRolesUsed.some((role) => typeof role !== "string" || role.trim().length === 0)
    ) {
      throw new Error("Body grammar v3 semanticRolesUsed is invalid");
    }
    semanticRolesUsed = [...new Set(record.semanticRolesUsed as string[])];
    if (typeof record.referenceLayoutId !== "string" || record.referenceLayoutId.trim().length === 0) {
      throw new Error("Body grammar v3 referenceLayoutId is invalid");
    }
    referenceLayoutId = record.referenceLayoutId;
    referenceLayoutDigest = requireDigest(record.referenceLayoutDigest, "referenceLayoutDigest");
    referenceSourceDigest = requireDigest(record.referenceSourceDigest, "referenceSourceDigest");
    if (record.nativeScene !== null && record.nativeScene !== undefined) {
      nativeScene = parseRetainedNativeScene(record.nativeScene, record.nativeSceneDigest);
      nativeSceneDigest = nativeScene.digest;
      if (nativeScene.sourcePackageDigest !== referenceSourceDigest) {
        throw new Error("Retained native scene source digest mismatch");
      }
    }
  }
  const expectedBlockKind = isV2
    ? V2_BLOCK_KIND_BY_ARCHETYPE[kind]
    : kind === "evidence-stack"
    ? "evidence-card"
    : kind === "process-grid"
      ? "process-step"
      : "content-card";
  const bounds = contentRect ?? bodySlot;
  for (const block of blocks) {
    const right = block.bounds.x + block.bounds.width;
    const bottom = block.bounds.y + block.bounds.height;
    if (
      (!isV3 && block.kind !== expectedBlockKind)
      || block.bounds.x < bounds.x
      || block.bounds.y < bounds.y
      || right > bounds.x + bounds.width
      || bottom > bounds.y + bounds.height
      || block.textInsets.left + block.textInsets.right >= block.bounds.width
      || block.textInsets.top + block.textInsets.bottom >= block.bounds.height
      || (!isV3 && block.kind === "evidence-card" && block.bounds.x - 6 < 0)
    ) {
      throw new Error("Body grammar block must remain inside its body slot");
    }
  }
  const bodyText = textValue(values[bodyRole]);
  const composedText = blocks.map(({ text }) => text).join("");
  const sourceTextDigest = `sha256:${createHash("sha256").update(bodyText, "utf8").digest("hex")}`;
  if (composedText !== bodyText || record.sourceTextDigest !== sourceTextDigest) {
    throw new Error("Body grammar must preserve source text exactly once");
  }
  if (typeof record.digest !== "string") throw new Error("Body grammar digest is required");
  const unsigned = { ...record };
  delete unsigned.digest;
  if (canonicalDigest(unsigned) !== record.digest) throw new Error("Body grammar digest mismatch");
  return {
    schemaVersion,
    styleId,
    kind,
    ...((isV2 || isV3)
      ? {
          archetype: record.archetype as string,
          slideRole: record.slideRole as BodyGrammarSnapshot["slideRole"],
          contentRect: contentRect!,
          documentMetadata,
        }
      : {}),
    ...(isV3
      ? {
          referenceLayoutId: referenceLayoutId!,
          referenceLayoutDigest: referenceLayoutDigest!,
          referenceSourceDigest: referenceSourceDigest!,
          title: title!,
          decorations: decorations!,
          semanticRolesUsed: semanticRolesUsed!,
          ...(nativeScene ? { nativeScene, nativeSceneDigest } : {}),
        }
      : {}),
    bodyRole,
    sourceTextDigest,
    bodySlot,
    blocks,
    digest: record.digest,
  };
}

function parseResolvedSnapshotEvidence(
  values: Readonly<Record<string, MaterializedTextValue>>,
): ResolvedSnapshotEvidence | null {
  const encoded = values[RESOLVED_SNAPSHOT_EVIDENCE_VALUE_KEY];
  if (encoded === undefined) return null;
  if (typeof encoded !== "string") {
    throw new Error("Resolved snapshot evidence must be canonical JSON text");
  }
  let parsed: unknown;
  try {
    parsed = JSON.parse(encoded);
  } catch {
    throw new Error("Resolved snapshot evidence is not valid JSON");
  }
  if (parsed === null || typeof parsed !== "object" || Array.isArray(parsed)) {
    throw new Error("Resolved snapshot evidence must be an object");
  }
  const item = parsed as Record<string, unknown>;
  if (
    item.schemaVersion !== "resolved-snapshot-evidence/v1"
    || typeof item.resolvedTemplateId !== "string"
    || item.resolvedTemplateId.trim().length === 0
    || !Number.isInteger(item.pageNumber)
    || !Number.isInteger(item.slideCount)
    || (item.pageNumber as number) < 1
    || (item.slideCount as number) < 1
    || (item.pageNumber as number) > (item.slideCount as number)
    || !Array.isArray(item.semanticRolesUsed)
    || !Array.isArray(item.layoutRolesUsed)
    || (item.semanticRolesUsed as unknown[]).some((role) => typeof role !== "string")
    || (item.layoutRolesUsed as unknown[]).some((role) => typeof role !== "string")
  ) {
    throw new Error("Resolved snapshot evidence is invalid");
  }
  return {
    schemaVersion: "resolved-snapshot-evidence/v1",
    resolvedTemplateId: item.resolvedTemplateId,
    resolvedReferenceLayoutDigest: requireDigest(
      item.resolvedReferenceLayoutDigest,
      "resolvedReferenceLayoutDigest",
    ),
    resolvedCompositionDigest: requireDigest(
      item.resolvedCompositionDigest,
      "resolvedCompositionDigest",
    ),
    resolvedRuleDigest: requireDigest(item.resolvedRuleDigest, "resolvedRuleDigest"),
    pageNumber: item.pageNumber as number,
    slideCount: item.slideCount as number,
    semanticRolesUsed: [...new Set(item.semanticRolesUsed as string[])],
    layoutRolesUsed: [...new Set(item.layoutRolesUsed as string[])],
    bodyGrammarDigest: requireDigest(item.bodyGrammarDigest, "bodyGrammarDigest"),
  };
}

function pxToEmu(value: number): number {
  const emu = Math.round(value * 9525);
  if (
    !Number.isFinite(value)
    || value < 0
    || value > MAX_BODY_GRAMMAR_PX
    || !Number.isSafeInteger(emu)
  ) {
    throw new Error("Body grammar coordinate is outside the safe OOXML range");
  }
  return emu;
}

function rgb(value: string): string {
  return value.slice(1).toUpperCase();
}

function bodyBlockTextXml(
  text: string,
  fontFamily: string,
  fontSizePt: number,
  lineHeight: number,
  color: string,
  alignment: "left" | "center" | "right" = "left",
  bold = false,
): string {
  const alignmentValue = alignment === "center" ? "ctr" : alignment === "right" ? "r" : "l";
  return text.split("\n").map((line) => {
    const preserve = /^\s|\s$|\s{2,}/.test(line) ? ' xml:space="preserve"' : "";
    return `<a:p><a:pPr algn="${alignmentValue}"><a:lnSpc><a:spcPct val="${Math.round(lineHeight * 100000)}"/></a:lnSpc></a:pPr><a:r><a:rPr lang="ko-KR" sz="${Math.round(fontSizePt * 100)}"${bold ? ' b="1"' : ""}><a:solidFill><a:srgbClr val="${rgb(color)}"/></a:solidFill><a:latin typeface="${xmlEscape(fontFamily)}"/><a:ea typeface="${xmlEscape(fontFamily)}"/><a:cs typeface="${xmlEscape(fontFamily)}"/></a:rPr><a:t${preserve}>${xmlEscape(line)}</a:t></a:r><a:endParaRPr lang="ko-KR" sz="${Math.round(fontSizePt * 100)}"/></a:p>`;
  }).join("");
}

function solidFillXml(value: string | undefined): string {
  return value ? `<a:solidFill><a:srgbClr val="${rgb(value)}"/></a:solidFill>` : "<a:noFill/>";
}

function lineXml(value: string | undefined, widthPt = 1): string {
  return value
    ? `<a:ln w="${Math.round(widthPt * 12700)}"><a:solidFill><a:srgbClr val="${rgb(value)}"/></a:solidFill></a:ln>`
    : "<a:ln><a:noFill/></a:ln>";
}

function retainedDecorationXml(
  item: BodyGrammarDecoration,
  shapeId: number,
  referenceLayoutDigest: string,
): string {
  const bounds = item.bounds;
  const transform = `<a:xfrm><a:off x="${pxToEmu(bounds.x)}" y="${pxToEmu(bounds.y)}"/><a:ext cx="${pxToEmu(bounds.width)}" cy="${pxToEmu(bounds.height)}"/></a:xfrm>`;
  const metadata = `<p:extLst><p:ext uri="${VNEXT_EXT_URI}"><v:referenceLayer xmlns:v="${VNEXT_NS}" id="${xmlEscape(item.id)}" semanticRole="${item.semanticRole}" referenceLayoutDigest="${xmlEscape(referenceLayoutDigest)}"/></p:ext></p:extLst>`;
  if (item.kind === "line") {
    return `<p:cxnSp><p:nvCxnSpPr><p:cNvPr id="${shapeId}" name="${xmlEscape(`retained:${item.id}`)}" descr="vnext:reference-layout=${xmlEscape(referenceLayoutDigest)}"/><p:cNvCxnSpPr/><p:nvPr>${metadata}</p:nvPr></p:nvCxnSpPr><p:spPr>${transform}<a:prstGeom prst="line"><a:avLst/></a:prstGeom>${lineXml(item.stroke ?? item.fill, item.strokeWidth)}</p:spPr></p:cxnSp>`;
  }
  return `<p:sp><p:nvSpPr><p:cNvPr id="${shapeId}" name="${xmlEscape(`retained:${item.id}`)}" descr="vnext:reference-layout=${xmlEscape(referenceLayoutDigest)}"/><p:cNvSpPr/><p:nvPr>${metadata}</p:nvPr></p:nvSpPr><p:spPr>${transform}<a:prstGeom prst="${item.kind}"><a:avLst/></a:prstGeom>${solidFillXml(item.fill)}${lineXml(item.stroke, item.strokeWidth)}</p:spPr></p:sp>`;
}

function retainedTextShapeXml(
  {
    shapeId,
    name,
    semanticRole,
    bounds,
    text,
    textStyle,
    textInsets,
    shape,
    fill,
    stroke,
    strokeWidth,
    referenceLayoutDigest,
  }: {
  shapeId: number;
  name: string;
  semanticRole: string;
  bounds: BodyGrammarBounds;
  text: string;
  textStyle: BodyGrammarTextStyle;
  textInsets: BodyGrammarBlock["textInsets"];
  shape: "rect" | "roundRect" | "ellipse";
  fill?: string;
  stroke?: string;
  strokeWidth?: number;
  referenceLayoutDigest: string;
}): string {
  const anchor = textStyle.verticalAlignment === "middle"
    ? "ctr"
    : textStyle.verticalAlignment === "bottom"
      ? "b"
      : "t";
  return `<p:sp><p:nvSpPr><p:cNvPr id="${shapeId}" name="${xmlEscape(name)}" descr="vnext:reference-layout=${xmlEscape(referenceLayoutDigest)};semantic-role=${xmlEscape(semanticRole)}"/><p:cNvSpPr txBox="1"/><p:nvPr><p:extLst><p:ext uri="${VNEXT_EXT_URI}"><v:referenceLayer xmlns:v="${VNEXT_NS}" id="${xmlEscape(name)}" semanticRole="${xmlEscape(semanticRole)}" referenceLayoutDigest="${xmlEscape(referenceLayoutDigest)}"/></p:ext></p:extLst></p:nvPr></p:nvSpPr><p:spPr><a:xfrm><a:off x="${pxToEmu(bounds.x)}" y="${pxToEmu(bounds.y)}"/><a:ext cx="${pxToEmu(bounds.width)}" cy="${pxToEmu(bounds.height)}"/></a:xfrm><a:prstGeom prst="${shape}"><a:avLst/></a:prstGeom>${solidFillXml(fill)}${lineXml(stroke, strokeWidth)}</p:spPr><p:txBody><a:bodyPr wrap="square" anchor="${anchor}" lIns="${pxToEmu(textInsets.left)}" tIns="${pxToEmu(textInsets.top)}" rIns="${pxToEmu(textInsets.right)}" bIns="${pxToEmu(textInsets.bottom)}"><a:normAutofit/></a:bodyPr><a:lstStyle/>${bodyBlockTextXml(text, textStyle.fontFamily, textStyle.fontPt, textStyle.lineHeight, textStyle.color, textStyle.alignment, textStyle.fontWeight >= 600)}</p:txBody></p:sp>`;
}

function retainedAssetExtension(contentType: string): string {
  const extension = new Map([
    ["image/png", "png"],
    ["image/jpeg", "jpeg"],
    ["image/gif", "gif"],
    ["image/svg+xml", "svg"],
    ["image/x-emf", "emf"],
    ["image/x-wmf", "wmf"],
  ]).get(contentType);
  if (!extension) throw new Error(`Unsupported retained image media type: ${contentType}`);
  return extension;
}

function rewriteRetainedShapeIds(fragmentXml: string, firstShapeId: number): { xml: string; count: number } {
  let count = 0;
  const xml = fragmentXml.replace(
    /(<(?:[A-Za-z_][\w.-]*:)?cNvPr\b[^>]*\bid=")\d+("[^>]*>)/g,
    (_match, prefix: string, suffix: string) => `${prefix}${firstShapeId + count++}${suffix}`,
  );
  if (count === 0) throw new Error("Retained native fragment has no cNvPr identity");
  return { xml, count };
}

function retainedNativeSceneXml(
  scene: RetainedNativeScene,
  firstShapeId: number,
): {
  xml: string;
  binaryParts: MaterializedSlideParts["binaryParts"];
  relationships: readonly string[];
  shapeCount: number;
} {
  const binaryParts = new Map<string, MaterializedSlideParts["binaryParts"][number]>();
  const relationships: string[] = [];
  let nextShapeId = firstShapeId;
  let nextRelationship = 1000;
  const layerXml = scene.layers.map((layer, layerIndex) => {
    const fragments = layer.elements
      .slice()
      .sort((left, right) => left.zIndex - right.zIndex || left.sourceIndex - right.sourceIndex)
      .map((element) => {
        if (!/^<(?:[A-Za-z_][\w.-]*:)?(?:sp|cxnSp|pic|graphicFrame|grpSp)\b/.test(element.fragmentXml)) {
          throw new Error("Retained native fragment root is not a presentation shape");
        }
        let fragment = element.fragmentXml;
        for (const relationship of element.relationships) {
          const relationshipId = `rIdNative${nextRelationship++}`;
          const digestHex = relationship.targetDigest.slice("sha256:".length);
          const extension = retainedAssetExtension(relationship.targetMediaType);
          const partName = `ppt/media/retained-${digestHex}.${extension}`;
          const bytes = Buffer.from(relationship.targetBytesBase64, "base64");
          const existing = binaryParts.get(partName);
          if (existing && !existing.bytes.equals(bytes)) {
            throw new Error("Retained native asset part collision");
          }
          binaryParts.set(partName, { partName, bytes, contentType: relationship.targetMediaType });
          relationships.push(
            `<Relationship Id="${relationshipId}" Type="${R_NS}/image" Target="../media/${partName.slice("ppt/media/".length)}"/>`,
          );
          fragment = fragment.split(`"${relationship.id}"`).join(`"${relationshipId}"`);
        }
        if (element.relationshipIds.some((id) => fragment.includes(`"${id}"`))) {
          throw new Error("Retained native fragment relationship was not rewritten");
        }
        const rewritten = rewriteRetainedShapeIds(fragment, nextShapeId);
        nextShapeId += rewritten.count;
        return rewritten.xml;
      })
      .join("");
    const groupShapeId = nextShapeId++;
    const targetWidth = pxToEmu(scene.targetCanvas.width);
    const targetHeight = pxToEmu(scene.targetCanvas.height);
    return `<p:grpSp><p:nvGrpSpPr><p:cNvPr id="${groupShapeId}" name="retained-native:${layer.kind}" descr="vnext:native-scene=${xmlEscape(scene.digest)}"/><p:cNvGrpSpPr/><p:nvPr><p:extLst><p:ext uri="${VNEXT_EXT_URI}"><v:nativeSceneLayer xmlns:v="${VNEXT_NS}" kind="${layer.kind}" digest="${xmlEscape(scene.digest)}" sourcePart="${xmlEscape(layer.sourcePart)}"/></p:ext></p:extLst></p:nvPr></p:nvGrpSpPr><p:grpSpPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="${targetWidth}" cy="${targetHeight}"/><a:chOff x="0" y="0"/><a:chExt cx="${Math.round(scene.sourceCanvas.width)}" cy="${Math.round(scene.sourceCanvas.height)}"/></a:xfrm></p:grpSpPr>${fragments}</p:grpSp>`;
  }).join("");
  return {
    xml: layerXml,
    binaryParts: [...binaryParts.values()],
    relationships,
    shapeCount: nextShapeId - firstShapeId,
  };
}

function retainedLayoutShapeXml(
  grammar: BodyGrammarSnapshot,
  firstShapeId: number,
): {
  xml: string;
  binaryParts: MaterializedSlideParts["binaryParts"];
  relationships: readonly string[];
  shapeCount: number;
} {
  if (!grammar.title || !grammar.referenceLayoutDigest) {
    return { xml: "", binaryParts: [], relationships: [], shapeCount: 0 };
  }
  const native = grammar.nativeScene
    ? retainedNativeSceneXml(grammar.nativeScene, firstShapeId)
    : { xml: "", binaryParts: [] as MaterializedSlideParts["binaryParts"], relationships: [] as string[], shapeCount: 0 };
  const semanticFirstShapeId = firstShapeId + native.shapeCount;
  const layers: { zIndex: number; order: number; render: (shapeId: number) => string }[] = [];
  for (const [index, item] of (grammar.decorations ?? []).entries()) {
    layers.push({
      zIndex: item.zIndex,
      order: index,
      render: (shapeId) => retainedDecorationXml(item, shapeId, grammar.referenceLayoutDigest!),
    });
  }
  if (grammar.title.accent) {
    const accent = grammar.title.accent;
    layers.push({
      zIndex: accent.zIndex,
      order: -1,
      render: (shapeId) => retainedDecorationXml(accent, shapeId, grammar.referenceLayoutDigest!),
    });
  }
  for (const [index, block] of grammar.blocks.entries()) {
    layers.push({
      zIndex: block.zIndex ?? index,
      order: 1_000 + index,
      render: (shapeId) => block.shape === "line"
        ? retainedDecorationXml(
            {
              id: block.id,
              semanticRole: "process-connector",
              kind: "line",
              bounds: block.bounds,
              stroke: block.stroke,
              strokeWidth: block.strokeWidth,
              zIndex: block.zIndex ?? index,
            },
            shapeId,
            grammar.referenceLayoutDigest!,
          )
        : retainedTextShapeXml({
            shapeId,
            name: block.id,
            semanticRole: block.semanticRole!,
            bounds: block.bounds,
            text: block.text,
            textStyle: block.textStyle!,
            textInsets: block.textInsets,
            shape: block.shape ?? "rect",
            fill: block.fill,
            stroke: block.stroke,
            strokeWidth: block.strokeWidth,
            referenceLayoutDigest: grammar.referenceLayoutDigest!,
          }),
    });
  }
  layers.push({
    zIndex: 100_000,
    order: 100_000,
    render: (shapeId) => retainedTextShapeXml({
      shapeId,
      name: "retained:title",
      semanticRole: "title",
      bounds: grammar.title!.bounds,
      text: grammar.title!.lines.join("\n"),
      textStyle: grammar.title!.textStyle,
      textInsets: { top: 0, right: 0, bottom: 0, left: 0 },
      shape: "rect",
      referenceLayoutDigest: grammar.referenceLayoutDigest!,
    }),
  });
  const semanticXml = layers
    .sort((left, right) => left.zIndex - right.zIndex || left.order - right.order)
    .map((layer, index) => layer.render(semanticFirstShapeId + index))
    .join("");
  return {
    xml: native.xml + semanticXml,
    binaryParts: native.binaryParts,
    relationships: native.relationships,
    shapeCount: native.shapeCount + layers.length,
  };
}

function bodyGrammarShapeXml(
  grammar: BodyGrammarSnapshot,
  definition: MaterializedPlaceholderDefinition,
  firstShapeId: number,
): string {
  return grammar.blocks.map((block, index) => {
    const shapeId = firstShapeId + index * 2;
    const bounds = block.bounds;
    const insets = block.textInsets;
    const geometry = ["evidence-card", "cover-summary", "section-summary", "metric", "closing-message"].includes(block.kind)
      ? "roundRect"
      : "rect";
    const semanticRole = block.semanticRole
      ? ` semanticRole="${xmlEscape(block.semanticRole)}"`
      : "";
    const card = `<p:sp><p:nvSpPr><p:cNvPr id="${shapeId}" name="${xmlEscape(`${grammar.kind}:${block.id}`)}" descr="vnext:body-grammar=${xmlEscape(grammar.digest)}"/><p:cNvSpPr txBox="1"/><p:nvPr><p:extLst><p:ext uri="${VNEXT_EXT_URI}"><v:bodyBlock xmlns:v="${VNEXT_NS}" id="${xmlEscape(block.id)}" kind="${block.kind}" sequence="${block.sequence}"${semanticRole}/></p:ext></p:extLst></p:nvPr></p:nvSpPr><p:spPr><a:xfrm><a:off x="${pxToEmu(bounds.x)}" y="${pxToEmu(bounds.y)}"/><a:ext cx="${pxToEmu(bounds.width)}" cy="${pxToEmu(bounds.height)}"/></a:xfrm><a:prstGeom prst="${geometry}"><a:avLst/></a:prstGeom><a:solidFill><a:srgbClr val="${rgb(block.fill)}"/></a:solidFill><a:ln w="12700"><a:solidFill><a:srgbClr val="${rgb(block.stroke)}"/></a:solidFill></a:ln></p:spPr><p:txBody><a:bodyPr wrap="square" lIns="${pxToEmu(insets.left)}" tIns="${pxToEmu(insets.top)}" rIns="${pxToEmu(insets.right)}" bIns="${pxToEmu(insets.bottom)}"><a:normAutofit/></a:bodyPr><a:lstStyle/>${bodyBlockTextXml(block.text, definition.fontFamily, block.fontPt, block.lineHeight, block.textColor)}</p:txBody></p:sp>`;
    const decorationBounds = block.kind === "evidence-card"
      ? { x: bounds.x - 6, y: bounds.y + 8, width: 18, height: 18, shape: "ellipse" }
      : { x: bounds.x, y: bounds.y, width: bounds.width, height: 5, shape: "rect" };
    const decoration = `<p:sp><p:nvSpPr><p:cNvPr id="${shapeId + 1}" name="${xmlEscape(`${grammar.kind}:accent-${block.sequence}`)}"/><p:cNvSpPr/><p:nvPr/></p:nvSpPr><p:spPr><a:xfrm><a:off x="${pxToEmu(decorationBounds.x)}" y="${pxToEmu(decorationBounds.y)}"/><a:ext cx="${pxToEmu(decorationBounds.width)}" cy="${pxToEmu(decorationBounds.height)}"/></a:xfrm><a:prstGeom prst="${decorationBounds.shape}"><a:avLst/></a:prstGeom><a:solidFill><a:srgbClr val="${rgb(block.accent)}"/></a:solidFill><a:ln><a:noFill/></a:ln></p:spPr></p:sp>`;
    return card + decoration;
  }).join("");
}

function documentMetadataShapeXml(
  grammar: BodyGrammarSnapshot,
  definition: MaterializedPlaceholderDefinition,
  shapeId: number,
): string {
  const metadata = grammar.documentMetadata;
  if (!metadata?.visible || !grammar.contentRect) return "";
  const width = Math.min(280, Math.max(120, grammar.contentRect.width * 0.3));
  const height = Math.min(30, Math.max(18, grammar.contentRect.height * 0.06));
  const bounds = {
    x: grammar.contentRect.x + grammar.contentRect.width - width,
    y: grammar.contentRect.y + Math.min(8, grammar.contentRect.height * 0.02),
    width,
    height,
  };
  const fontPt = Math.max(8, Math.min(12, definition.fontSizePt * 0.72));
  return `<p:sp><p:nvSpPr><p:cNvPr id="${shapeId}" name="semantic:document-id" descr="vnext:semantic-role=document-id"/><p:cNvSpPr txBox="1"/><p:nvPr><p:extLst><p:ext uri="${VNEXT_EXT_URI}"><v:documentMetadata xmlns:v="${VNEXT_NS}" kind="${metadata.kind}" visible="true" value="${xmlEscape(metadata.value)}"/></p:ext></p:extLst></p:nvPr></p:nvSpPr><p:spPr><a:xfrm><a:off x="${pxToEmu(bounds.x)}" y="${pxToEmu(bounds.y)}"/><a:ext cx="${pxToEmu(bounds.width)}" cy="${pxToEmu(bounds.height)}"/></a:xfrm><a:prstGeom prst="rect"><a:avLst/></a:prstGeom><a:noFill/><a:ln><a:noFill/></a:ln></p:spPr><p:txBody><a:bodyPr wrap="none"><a:noAutofit/></a:bodyPr><a:lstStyle/>${bodyBlockTextXml(metadata.value, definition.fontFamily, fontPt, 1, "#667085")}</p:txBody></p:sp>`;
}

function slidePageNumberShapeXml(
  definition: MaterializedPageNumberDefinition,
  pageNumber: number,
  shapeId: number,
): string {
  if (!Number.isInteger(definition.idx) || definition.idx < 0) {
    throw new Error("Page-number furniture placeholder index is invalid");
  }
  const bounds = definition.bounds;
  const fieldId = `{00000000-0000-0000-0000-${String(shapeId).padStart(12, "0")}}`;
  return `<p:sp><p:nvSpPr><p:cNvPr id="${shapeId}" name="furniture:page-number:slide" descr="vnext:page-number=${pageNumber}"/><p:cNvSpPr txBox="1"><a:spLocks noGrp="1" noMove="1" noResize="1" noEditPoints="1" noChangeShapeType="1"/></p:cNvSpPr><p:nvPr><p:ph type="sldNum" idx="${definition.idx}"/></p:nvPr></p:nvSpPr><p:spPr><a:xfrm><a:off x="${pxToEmu(bounds.x)}" y="${pxToEmu(bounds.y)}"/><a:ext cx="${pxToEmu(bounds.width)}" cy="${pxToEmu(bounds.height)}"/></a:xfrm><a:prstGeom prst="rect"><a:avLst/></a:prstGeom><a:noFill/><a:ln><a:noFill/></a:ln></p:spPr><p:txBody><a:bodyPr wrap="none" anchor="ctr"><a:noAutofit/></a:bodyPr><a:lstStyle/><a:p><a:fld id="${fieldId}" type="slidenum"><a:rPr lang="ko-KR" sz="${Math.round(definition.fontSizePt * 100)}"><a:solidFill><a:srgbClr val="${rgb(definition.fill)}"/></a:solidFill><a:latin typeface="${xmlEscape(definition.fontFamily)}"/><a:ea typeface="${xmlEscape(definition.fontFamily)}"/><a:cs typeface="${xmlEscape(definition.fontFamily)}"/></a:rPr><a:t>${pageNumber}</a:t></a:fld><a:endParaRPr lang="ko-KR" sz="${Math.round(definition.fontSizePt * 100)}"/></a:p></p:txBody></p:sp>`;
}

function textShapeXml(
  definition: MaterializedPlaceholderDefinition,
  value: MaterializedTextValue,
  shapeId: number,
): string {
  const type = definition.type === "title" ? "title" : definition.type;
  const role = xmlEscape(definition.role);
  const sourceRuns = typeof value === "string" ? [{ text: value }] : value.runs;
  const paragraphRuns: MaterializedTextRun[][] = [[]];
  for (const sourceRun of sourceRuns) {
    const lines = sourceRun.text.split("\n");
    lines.forEach((text, index) => {
      if (index > 0) paragraphRuns.push([]);
      paragraphRuns.at(-1)!.push({ ...sourceRun, text });
    });
  }
  const paragraphs = paragraphRuns.map((runs) => {
    const xml = runs.map((run) => {
      const preserve = /^\s|\s$|\s{2,}/.test(run.text) ? ' xml:space="preserve"' : "";
      const fontFamily = run.fontFamily ?? definition.fontFamily;
      return `<a:r><a:rPr lang="ko-KR" sz="${Math.round(definition.fontSizePt * 100)}"${(run.bold ?? definition.bold) ? ' b="1"' : ""}${run.italic ? ' i="1"' : ""}><a:latin typeface="${xmlEscape(fontFamily)}"/><a:ea typeface="${xmlEscape(fontFamily)}"/><a:cs typeface="${xmlEscape(fontFamily)}"/></a:rPr><a:t${preserve}>${xmlEscape(run.text)}</a:t></a:r>`;
    }).join("");
    return `<a:p>${xml}<a:endParaRPr lang="ko-KR" sz="${Math.round(definition.fontSizePt * 100)}"/></a:p>`;
  }).join("");
  const autoFit = type === "title" || type === "ctrTitle"
    ? "<a:noAutofit/>"
    : "<a:normAutofit/>";
  return `<p:sp><p:nvSpPr><p:cNvPr id="${shapeId}" name="placeholder:${role}" descr="vnext:role=${role}"/><p:cNvSpPr txBox="1"/><p:nvPr><p:ph type="${type}" idx="${definition.idx}"/><p:extLst><p:ext uri="${VNEXT_EXT_URI}"><v:placeholder xmlns:v="${VNEXT_NS}" role="${role}" idx="${definition.idx}"/></p:ext></p:extLst></p:nvPr></p:nvSpPr><p:spPr/><p:txBody><a:bodyPr wrap="square">${autoFit}</a:bodyPr><a:lstStyle/>${paragraphs}</p:txBody></p:sp>`;
}

function relationshipTarget(layoutPartName: string): string {
  if (!/^ppt\/slideLayouts\/slideLayout\d+\.xml$/.test(layoutPartName)) {
    throw new Error(`Unsafe or unsupported layout part: ${layoutPartName}`);
  }
  return `../slideLayouts/${layoutPartName.slice(layoutPartName.lastIndexOf("/") + 1)}`;
}

export function materializeSlideParts(
  request: MaterializeSlideRequest,
): MaterializedSlideParts {
  if (!request.slideId || !request.layoutId || !request.masterDigest) {
    throw new Error("Slide materialization requires stable slide, layout, and master identities");
  }
  if (request.placeholders.length === 0) {
    throw new Error("Slide materialization requires at least one placeholder");
  }
  const seenRoles = new Set<string>();
  const seenIndexes = new Set<number>();
  for (const placeholder of request.placeholders) {
    if (!placeholder.role || seenRoles.has(placeholder.role)) {
      throw new Error(`Duplicate or empty placeholder role: ${placeholder.role}`);
    }
    if (!Number.isInteger(placeholder.idx) || placeholder.idx < 0 || seenIndexes.has(placeholder.idx)) {
      throw new Error(`Duplicate or invalid placeholder idx: ${placeholder.idx}`);
    }
    if (!(placeholder.role in request.values)) {
      throw new Error(`Missing slide value for placeholder role: ${placeholder.role}`);
    }
    seenRoles.add(placeholder.role);
    seenIndexes.add(placeholder.idx);
  }
  const roles = [...request.placeholders]
    .map(({ role, type, idx }) => ({ role, type, idx }))
    .sort((left, right) => left.idx - right.idx || left.role.localeCompare(right.role));
  const placeholderRoleDigest = stableDigest(roles);
  const bodyGrammar = parseBodyGrammar(request.values);
  const resolvedEvidence = parseResolvedSnapshotEvidence(request.values);
  const isResolvedReference = bodyGrammar?.schemaVersion === BODY_GRAMMAR_V3_SCHEMA_VERSION;
  if (isResolvedReference) {
    const titleDefinition = request.placeholders.find(({ type }) => type === "title");
    if (
      !titleDefinition
      || !bodyGrammar.title
      || canonicalTitleText(bodyGrammar.title.text)
        !== canonicalTitleText(textValue(request.values[titleDefinition.role]))
      || !resolvedEvidence
      || !request.pageNumberFurniture
      || resolvedEvidence.bodyGrammarDigest !== bodyGrammar.digest
      || resolvedEvidence.resolvedReferenceLayoutDigest !== bodyGrammar.referenceLayoutDigest
      || !resolvedEvidence.semanticRolesUsed.every((role) => bodyGrammar.semanticRolesUsed?.includes(role))
    ) {
      throw new Error("Resolved reference layout evidence does not match slide geometry");
    }
  } else if (resolvedEvidence) {
    throw new Error("Resolved snapshot evidence requires body grammar v3");
  }
  const placeholderShapes = isResolvedReference
    ? ""
    : request.placeholders
        .slice()
        .sort((left, right) => left.idx - right.idx || left.role.localeCompare(right.role))
        .map((placeholder, index) =>
          textShapeXml(
            placeholder,
            bodyGrammar?.bodyRole === placeholder.role ? "" : request.values[placeholder.role],
            index + 2,
          ),
        )
        .join("");
  const bodyDefinition = bodyGrammar
    ? request.placeholders.find(({ role }) => role === bodyGrammar.bodyRole)
    : undefined;
  if (bodyGrammar && !bodyDefinition) throw new Error("Body grammar placeholder is missing");
  const retainedShapes = bodyGrammar && bodyDefinition && isResolvedReference
    ? retainedLayoutShapeXml(bodyGrammar, request.placeholders.length + 2)
    : { xml: "", binaryParts: [] as MaterializedSlideParts["binaryParts"], relationships: [] as string[], shapeCount: 0 };
  const grammarShapes = bodyGrammar && bodyDefinition
    ? isResolvedReference
      ? retainedShapes.xml
      : bodyGrammarShapeXml(bodyGrammar, bodyDefinition, request.placeholders.length + 2)
    : "";
  const documentMetadataShape = bodyGrammar && bodyDefinition
    ? documentMetadataShapeXml(
        bodyGrammar,
        bodyDefinition,
        request.placeholders.length + 2
          + (isResolvedReference
            ? retainedShapes.shapeCount
            : bodyGrammar.blocks.length * 2),
      )
    : "";
  const pageNumberShape = isResolvedReference && resolvedEvidence && request.pageNumberFurniture
    ? slidePageNumberShapeXml(
        request.pageNumberFurniture,
        resolvedEvidence.pageNumber,
        900_000 + resolvedEvidence.pageNumber,
      )
    : "";
  const shapes = placeholderShapes + grammarShapes + documentMetadataShape + pageNumberShape;
  const grammarMetadata = bodyGrammar
    ? ` bodyGrammarDigest="${xmlEscape(bodyGrammar.digest)}" bodyGrammarSchemaVersion="${bodyGrammar.schemaVersion}" bodyGrammarArchetype="${xmlEscape(bodyGrammar.archetype ?? bodyGrammar.kind)}"${bodyGrammar.slideRole ? ` bodyGrammarSlideRole="${bodyGrammar.slideRole}"` : ""}${bodyGrammar.referenceLayoutId ? ` referenceLayoutId="${xmlEscape(bodyGrammar.referenceLayoutId)}" referenceLayoutDigest="${xmlEscape(bodyGrammar.referenceLayoutDigest!)}" referenceSourceDigest="${xmlEscape(bodyGrammar.referenceSourceDigest!)}"` : ""}${bodyGrammar.nativeSceneDigest ? ` nativeSceneDigest="${xmlEscape(bodyGrammar.nativeSceneDigest)}"` : ""}`
    : "";
  const resolvedMetadata = resolvedEvidence
    ? ` resolvedTemplateId="${xmlEscape(resolvedEvidence.resolvedTemplateId)}" resolvedCompositionDigest="${xmlEscape(resolvedEvidence.resolvedCompositionDigest)}" resolvedRuleDigest="${xmlEscape(resolvedEvidence.resolvedRuleDigest)}" pageNumber="${resolvedEvidence.pageNumber}" slideCount="${resolvedEvidence.slideCount}" semanticRolesUsed="${xmlEscape(resolvedEvidence.semanticRolesUsed.join("|"))}" layoutRolesUsed="${xmlEscape(resolvedEvidence.layoutRolesUsed.join("|"))}"`
    : "";
  const logicalPlaceholderMetadata = isResolvedReference
    ? roles
        .map(({ role, type, idx }) => `<v:placeholder role="${xmlEscape(role)}" type="${type}" idx="${idx}"/>`)
        .join("")
    : "";
  const slideMetadata = logicalPlaceholderMetadata
    ? `<v:slide xmlns:v="${VNEXT_NS}" slideId="${xmlEscape(request.slideId)}" layoutId="${xmlEscape(request.layoutId)}" contractVersion="${xmlEscape(request.contractVersion)}" masterDigest="${xmlEscape(request.masterDigest)}" placeholderRoleDigest="${placeholderRoleDigest}"${grammarMetadata}${resolvedMetadata}>${logicalPlaceholderMetadata}</v:slide>`
    : `<v:slide xmlns:v="${VNEXT_NS}" slideId="${xmlEscape(request.slideId)}" layoutId="${xmlEscape(request.layoutId)}" contractVersion="${xmlEscape(request.contractVersion)}" masterDigest="${xmlEscape(request.masterDigest)}" placeholderRoleDigest="${placeholderRoleDigest}"${grammarMetadata}${resolvedMetadata}/>`;
  const metadata = `<p:extLst><p:ext uri="${VNEXT_EXT_URI}">${slideMetadata}</p:ext></p:extLst>`;
  const slideXml = `${XML_HEADER}<p:sld xmlns:a="${A_NS}" xmlns:r="${R_NS}" xmlns:p="${P_NS}" xmlns:v="${VNEXT_NS}" showMasterSp="1"><p:cSld name="${xmlEscape(request.slideId)}"><p:spTree><p:nvGrpSpPr><p:cNvPr id="1" name=""/><p:cNvGrpSpPr/><p:nvPr/></p:nvGrpSpPr><p:grpSpPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="0" cy="0"/><a:chOff x="0" y="0"/><a:chExt cx="0" cy="0"/></a:xfrm></p:grpSpPr>${shapes}</p:spTree></p:cSld><p:clrMapOvr><a:masterClrMapping/></p:clrMapOvr>${metadata}</p:sld>`;
  const relationshipsXml = `${XML_HEADER}<Relationships xmlns="${REL_NS}"><Relationship Id="rId1" Type="${R_NS}/slideLayout" Target="${relationshipTarget(request.layoutPartName)}"/>${retainedShapes.relationships.join("")}</Relationships>`;
  return {
    slideXml: Buffer.from(slideXml, "utf8"),
    relationshipsXml: Buffer.from(relationshipsXml, "utf8"),
    binaryParts: retainedShapes.binaryParts,
    semanticMetadata: {
      slideId: request.slideId,
      layoutId: request.layoutId,
      masterDigest: request.masterDigest,
      placeholderRoleDigest,
      ...(bodyGrammar ? { bodyGrammarDigest: bodyGrammar.digest } : {}),
      ...(bodyGrammar
        ? {
            bodyGrammarSchemaVersion: bodyGrammar.schemaVersion,
            bodyGrammarArchetype: bodyGrammar.archetype ?? bodyGrammar.kind,
            ...(bodyGrammar.slideRole
              ? { bodyGrammarSlideRole: bodyGrammar.slideRole }
              : {}),
            ...(bodyGrammar.documentMetadata
              ? { documentMetadata: bodyGrammar.documentMetadata }
              : {}),
            ...(bodyGrammar.referenceLayoutId
              ? {
                  referenceLayoutId: bodyGrammar.referenceLayoutId,
                  referenceLayoutDigest: bodyGrammar.referenceLayoutDigest,
                  referenceSourceDigest: bodyGrammar.referenceSourceDigest,
                  semanticRolesUsed: bodyGrammar.semanticRolesUsed,
                }
              : {}),
            ...(bodyGrammar.nativeSceneDigest
              ? { nativeSceneDigest: bodyGrammar.nativeSceneDigest }
              : {}),
          }
        : {}),
      ...(resolvedEvidence
        ? {
            resolvedTemplateId: resolvedEvidence.resolvedTemplateId,
            resolvedCompositionDigest: resolvedEvidence.resolvedCompositionDigest,
            resolvedRuleDigest: resolvedEvidence.resolvedRuleDigest,
            pageNumber: resolvedEvidence.pageNumber,
            slideCount: resolvedEvidence.slideCount,
            layoutRolesUsed: resolvedEvidence.layoutRolesUsed,
          }
        : {}),
      roles,
    },
  };
}

export function readSlideSemanticMetadata(slideXmlBytes: Buffer): MaterializedSlideParts["semanticMetadata"] {
  const xml = slideXmlBytes.toString("utf8");
  const slide = xml.match(/<v:slide\b([^>]*?)(?:\/>|>)/);
  if (!slide) throw new Error("Slide semantic metadata is missing");
  const attributes = Object.fromEntries(
    [...slide[1].matchAll(/([A-Za-z][\w:-]*)="([^"]*)"/g)].map((match) => [
      match[1],
      match[2],
    ]),
  );
  const roles = [...xml.matchAll(/<v:placeholder\b([^>]*)\/>/g)]
    .map((match) => {
      const role = match[1].match(/\brole="([^"]+)"/)?.[1];
      const idx = Number(match[1].match(/\bidx="(\d+)"/)?.[1]);
      const shapeStart = xml.lastIndexOf("<p:sp>", match.index);
      const shapeEnd = xml.indexOf("</p:sp>", match.index);
      const shape = xml.slice(shapeStart, shapeEnd);
      const type = match[1].match(/\btype="([^"]+)"/)?.[1]
        ?? shape.match(/<p:ph\b[^>]*\btype="([^"]+)"/)?.[1];
      if (!role || !type || !Number.isInteger(idx)) {
        throw new Error("Malformed slide placeholder semantic metadata");
      }
      return { role, type, idx };
    })
    .sort((left, right) => left.idx - right.idx || left.role.localeCompare(right.role));
  if (!attributes.slideId || !attributes.layoutId || !attributes.masterDigest || !attributes.placeholderRoleDigest) {
    throw new Error("Incomplete slide semantic metadata");
  }
  if (stableDigest(roles) !== attributes.placeholderRoleDigest) {
    throw new Error("Slide placeholder role metadata digest does not match the XML mapping");
  }
  const referenceLayers = [...xml.matchAll(/<v:referenceLayer\b([^>]*)\/>/g)].map((match) => ({
    semanticRole: match[1].match(/\bsemanticRole="([^"]+)"/)?.[1],
    referenceLayoutDigest: match[1].match(/\breferenceLayoutDigest="([^"]+)"/)?.[1],
  }));
  const nativeSceneLayers = [...xml.matchAll(/<v:nativeSceneLayer\b([^>]*)\/>/g)].map((match) => ({
    kind: match[1].match(/\bkind="([^"]+)"/)?.[1],
    digest: match[1].match(/\bdigest="([^"]+)"/)?.[1],
  }));
  const semanticRolesUsed = attributes.semanticRolesUsed
    ? attributes.semanticRolesUsed.split("|").filter(Boolean)
    : [];
  const pageNumber = attributes.pageNumber === undefined ? undefined : Number(attributes.pageNumber);
  const slideCount = attributes.slideCount === undefined ? undefined : Number(attributes.slideCount);
  const slideNumberShape = [...xml.matchAll(/<p:sp>([\s\S]*?)<\/p:sp>/g)]
    .map((match) => match[1])
    .find((shape) => /<p:ph\b[^>]*\btype="sldNum"/.test(shape));
  const materializedPageNumber = slideNumberShape
    ? Number(slideNumberShape.match(/<a:fld\b[^>]*\btype="slidenum"[^>]*>[\s\S]*?<a:t>(\d+)<\/a:t>/)?.[1])
    : undefined;
  if (
    attributes.referenceLayoutId
    && (
      attributes.bodyGrammarSchemaVersion !== BODY_GRAMMAR_V3_SCHEMA_VERSION
      || !/^sha256:[0-9a-f]{64}$/.test(attributes.referenceLayoutDigest ?? "")
      || !/^sha256:[0-9a-f]{64}$/.test(attributes.referenceSourceDigest ?? "")
      || !/^sha256:[0-9a-f]{64}$/.test(attributes.resolvedCompositionDigest ?? "")
      || !/^sha256:[0-9a-f]{64}$/.test(attributes.resolvedRuleDigest ?? "")
      || !attributes.resolvedTemplateId
      || referenceLayers.length === 0
      || referenceLayers.some((layer) =>
        !layer.semanticRole
        || layer.referenceLayoutDigest !== attributes.referenceLayoutDigest
      )
      || semanticRolesUsed.some((role) =>
        !referenceLayers.some((layer) => layer.semanticRole === role)
      )
      || !Number.isInteger(pageNumber)
      || !Number.isInteger(slideCount)
      || pageNumber! < 1
      || pageNumber! > slideCount!
      || materializedPageNumber !== pageNumber
      || (attributes.nativeSceneDigest !== undefined
        && (
          !/^sha256:[0-9a-f]{64}$/.test(attributes.nativeSceneDigest)
          || nativeSceneLayers.length !== 3
          || nativeSceneLayers.map(({ kind }) => kind).join("|") !== "master|layout|slide"
          || nativeSceneLayers.some(({ digest }) => digest !== attributes.nativeSceneDigest)
        ))
    )
  ) {
    throw new Error("Malformed resolved reference layout semantic mapping");
  }
  const documentMetadataMatch = xml.match(/<v:documentMetadata\b([^>]*)\/>/);
  const documentMetadata = documentMetadataMatch
    ? {
        kind: documentMetadataMatch[1].match(/\bkind="([^"]+)"/)?.[1],
        value: documentMetadataMatch[1].match(/\bvalue="([^"]*)"/)?.[1],
        visible: documentMetadataMatch[1].match(/\bvisible="([^"]+)"/)?.[1],
      }
    : null;
  if (
    documentMetadata
    && (
      (documentMetadata.kind !== "rfp" && documentMetadata.kind !== "documentNumber")
      || documentMetadata.value === undefined
      || documentMetadata.visible !== "true"
    )
  ) {
    throw new Error("Malformed document metadata semantic mapping");
  }
  return {
    slideId: attributes.slideId,
    layoutId: attributes.layoutId,
    masterDigest: attributes.masterDigest,
    placeholderRoleDigest: attributes.placeholderRoleDigest,
    ...(attributes.bodyGrammarDigest ? { bodyGrammarDigest: attributes.bodyGrammarDigest } : {}),
    ...(attributes.bodyGrammarSchemaVersion
      ? { bodyGrammarSchemaVersion: attributes.bodyGrammarSchemaVersion }
      : {}),
    ...(attributes.bodyGrammarArchetype
      ? { bodyGrammarArchetype: attributes.bodyGrammarArchetype }
      : {}),
    ...(attributes.bodyGrammarSlideRole
      ? { bodyGrammarSlideRole: attributes.bodyGrammarSlideRole }
      : {}),
    ...(attributes.referenceLayoutId
      ? {
          referenceLayoutId: attributes.referenceLayoutId,
          referenceLayoutDigest: attributes.referenceLayoutDigest!,
          referenceSourceDigest: attributes.referenceSourceDigest!,
          ...(attributes.nativeSceneDigest ? { nativeSceneDigest: attributes.nativeSceneDigest } : {}),
          resolvedTemplateId: attributes.resolvedTemplateId!,
          resolvedCompositionDigest: attributes.resolvedCompositionDigest!,
          resolvedRuleDigest: attributes.resolvedRuleDigest!,
          pageNumber: pageNumber!,
          slideCount: slideCount!,
          semanticRolesUsed,
          layoutRolesUsed: attributes.layoutRolesUsed?.split("|").filter(Boolean) ?? [],
        }
      : {}),
    ...(documentMetadata
      ? {
          documentMetadata: {
            kind: documentMetadata.kind as "rfp" | "documentNumber",
            value: documentMetadata.value!,
            visible: true,
          },
        }
      : {}),
    roles,
  };
}
