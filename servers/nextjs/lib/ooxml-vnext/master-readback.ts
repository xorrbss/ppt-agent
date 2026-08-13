import { createHash } from "node:crypto";
import path from "node:path";

import {
  DECK_MASTER_CONTRACT_VERSION,
  DECK_MASTER_REQUIRED_THEME_COLORS,
  normalizeDeckMasterContract,
  type DeckMasterFurniture,
  type DeckMasterLayout,
  type DeckMasterPlaceholder,
  type DeckMasterTokenValue,
} from "./contracts.ts";
import { buildMetadata, compileDeckMasterParts } from "./master-compiler.ts";
import type {
  DeckMasterPartCompiler,
  DeckMasterReadBackReceipt,
  DeckMasterSemanticMetadata,
  SemanticFontFileMapping,
  SemanticFurnitureMapping,
  SemanticLayoutMapping,
  SemanticPlaceholderMapping,
} from "./master-compiler-contracts.ts";

const MASTER_PART = "ppt/slideMasters/slideMaster1.xml";
const MASTER_RELS_PART = "ppt/slideMasters/_rels/slideMaster1.xml.rels";
const THEME_PART = "ppt/theme/theme1.xml";
const MASTER_CONTENT_TYPE = "application/vnd.openxmlformats-officedocument.presentationml.slideMaster+xml";
const LAYOUT_CONTENT_TYPE = "application/vnd.openxmlformats-officedocument.presentationml.slideLayout+xml";
const THEME_CONTENT_TYPE = "application/vnd.openxmlformats-officedocument.theme+xml";
const PNG_CONTENT_TYPE = "image/png";
const PNG_SIGNATURE = Buffer.from("89504e470d0a1a0a", "hex");

function xmlUnescape(value: string): string {
  return value
    .replaceAll("&quot;", '"')
    .replaceAll("&apos;", "'")
    .replaceAll("&gt;", ">")
    .replaceAll("&lt;", "<")
    .replaceAll("&amp;", "&");
}

function attributes(source: string): Record<string, string> {
  const result: Record<string, string> = {};
  for (const match of source.matchAll(/([\w:.-]+)="([^"]*)"/g)) {
    result[match[1]] = xmlUnescape(match[2]);
  }
  return result;
}

function stableValue(value: unknown): unknown {
  if (Array.isArray(value)) return value.map(stableValue);
  if (value && typeof value === "object") {
    return Object.fromEntries(
      Object.entries(value as Record<string, unknown>)
        .sort(([left], [right]) => left.localeCompare(right, "en"))
        .map(([key, child]) => [key, stableValue(child)])
    );
  }
  return value;
}

function stableJson(value: unknown): string {
  return JSON.stringify(stableValue(value));
}

function digest(value: unknown): string {
  const payload = typeof value === "string" ? value : stableJson(value);
  return `sha256:${createHash("sha256").update(payload).digest("hex")}`;
}

function requirePart(parts: ReadonlyMap<string, Uint8Array>, partName: string): string {
  const value = parts.get(partName);
  if (!value) throw new Error(`Deck master read-back is missing ${partName}`);
  return Buffer.from(value).toString("utf8");
}

function parseTokenValue(kind: string, value: string): DeckMasterTokenValue {
  if (kind === "string") return value;
  if (kind === "boolean" && (value === "true" || value === "false")) return value === "true";
  if (kind === "number" && value.trim() !== "" && Number.isFinite(Number(value))) return Number(value);
  throw new Error(`Deck master metadata contains an invalid ${kind} token`);
}

function parseMetadata(masterXml: string): DeckMasterSemanticMetadata {
  const rootMatch = /<(?:[A-Za-z_][\w.-]*:)?deckMaster\b([^>]*)>([\s\S]*?)<\/(?:[A-Za-z_][\w.-]*:)?deckMaster>/.exec(masterXml);
  if (!rootMatch) throw new Error("Deck master semantic extension is missing");
  const root = attributes(rootMatch[1]);
  if (root.version !== DECK_MASTER_CONTRACT_VERSION) {
    throw new Error("Deck master semantic extension version is unsupported");
  }
  const body = rootMatch[2];
  const fontsMatch = /<(?:[A-Za-z_][\w.-]*:)?fonts\b([^>]*)\/>/.exec(body);
  if (!fontsMatch) throw new Error("Deck master semantic font mapping is missing");
  const fontAttrs = attributes(fontsMatch[1]);
  const fontFilesBlock = /<(?:[A-Za-z_][\w.-]*:)?fontFiles\b[^>]*>([\s\S]*?)<\/(?:[A-Za-z_][\w.-]*:)?fontFiles>/.exec(body)?.[1] ?? "";
  const fontFiles: SemanticFontFileMapping[] = [];
  for (const match of fontFilesBlock.matchAll(/<(?:[A-Za-z_][\w.-]*:)?file\b([^>]*)\/>/g)) {
    const item = attributes(match[1]);
    const sizeBytes = Number(item.sizeBytes);
    if (
      !item.style || !item.fileName || !item.version || !Number.isSafeInteger(sizeBytes) || sizeBytes <= 0 ||
      !/^[0-9a-f]{64}$/.test(item.sha256 ?? "") || !/^sha256:[0-9a-f]{64}$/.test(item.sourcePathDigest ?? "")
    ) {
      throw new Error("Deck master semantic font file metadata is invalid");
    }
    fontFiles.push({
      style: item.style,
      fileName: item.fileName,
      version: item.version,
      sizeBytes,
      sha256: item.sha256,
      sourcePathDigest: item.sourcePathDigest,
    });
  }
  const themeColors: Record<string, string> = {};
  for (const match of body.matchAll(/<(?:[A-Za-z_][\w.-]*:)?color\b([^>]*)\/>/g)) {
    const item = attributes(match[1]);
    if (!item.name || !item.value || themeColors[item.name] !== undefined) {
      throw new Error("Deck master semantic color metadata is invalid");
    }
    themeColors[item.name] = item.value;
  }
  const tokens: Record<string, DeckMasterTokenValue> = {};
  for (const match of body.matchAll(/<(?:[A-Za-z_][\w.-]*:)?token\b([^>]*)\/>/g)) {
    const item = attributes(match[1]);
    if (!item.name || !item.kind || item.value === undefined || tokens[item.name] !== undefined) {
      throw new Error("Deck master semantic token metadata is invalid");
    }
    tokens[item.name] = parseTokenValue(item.kind, item.value);
  }
  const furnitureBlock = /<(?:[A-Za-z_][\w.-]*:)?furniture\b[^>]*>([\s\S]*?)<\/(?:[A-Za-z_][\w.-]*:)?furniture>/.exec(body)?.[1] ?? "";
  const furniture: SemanticFurnitureMapping[] = [];
  for (const match of furnitureBlock.matchAll(/<(?:[A-Za-z_][\w.-]*:)?item\b([^>]*)\/>/g)) {
    const item = attributes(match[1]);
    const mapping: SemanticFurnitureMapping = {
      id: item.id,
      role: item.role,
      kind: item.kind as DeckMasterFurniture["kind"],
      shapeId: Number(item.shapeId),
      visibility: item.visibility as SemanticFurnitureMapping["visibility"],
      assetId: item.assetId || null,
      assetSha256: item.assetSha256 || null,
      assetPartName: item.assetPartName || null,
      assetRelationshipId: item.assetRelationshipId || null,
    };
    const assetFields = [
      mapping.assetId,
      mapping.assetSha256,
      mapping.assetPartName,
      mapping.assetRelationshipId,
    ];
    if (
      assetFields.some(Boolean) &&
      (
        assetFields.some((value) => !value) ||
        !/^[0-9a-f]{64}$/.test(mapping.assetSha256 ?? "") ||
        !/^ppt\/media\/[a-z0-9._-]+\.png$/.test(mapping.assetPartName ?? "") ||
        !/^rIdLogo[0-9]+$/.test(mapping.assetRelationshipId ?? "")
      )
    ) {
      throw new Error("Deck master semantic logo asset metadata is invalid");
    }
    furniture.push(mapping);
  }
  const layoutsBlock = /<(?:[A-Za-z_][\w.-]*:)?layouts\b[^>]*>([\s\S]*?)<\/(?:[A-Za-z_][\w.-]*:)?layouts>/.exec(body)?.[1] ?? "";
  const layouts: SemanticLayoutMapping[] = [];
  for (const match of layoutsBlock.matchAll(/<(?:[A-Za-z_][\w.-]*:)?layout\b([^>]*)>([\s\S]*?)<\/(?:[A-Za-z_][\w.-]*:)?layout>/g)) {
    const item = attributes(match[1]);
    const placeholders: SemanticPlaceholderMapping[] = [];
    for (const placeholderMatch of match[2].matchAll(/<(?:[A-Za-z_][\w.-]*:)?placeholder\b([^>]*)\/>/g)) {
      const placeholder = attributes(placeholderMatch[1]);
      placeholders.push({
        id: placeholder.id,
        role: placeholder.role,
        type: placeholder.type as DeckMasterPlaceholder["type"],
        index: Number(placeholder.idx),
        shapeId: Number(placeholder.shapeId),
      });
    }
    layouts.push({
      id: item.id,
      role: item.role,
      name: item.name,
      kind: item.kind as DeckMasterLayout["kind"],
      partName: item.partName,
      relationshipId: item.relationshipId,
      placeholders,
    });
  }
  const base: Omit<DeckMasterSemanticMetadata, "semanticDigest"> = {
    version: DECK_MASTER_CONTRACT_VERSION,
    contractId: root.contractId,
    contractName: root.contractName,
    contractDigest: root.contractDigest,
    masterPartName: root.masterPartName as typeof MASTER_PART,
    themePartName: root.themePartName as typeof THEME_PART,
    themeName: root.themeName,
    themeColors,
    fonts: {
      primary: fontAttrs.primary,
      latin: fontAttrs.latin,
      eastAsian: fontAttrs.eastAsian,
      complexScript: fontAttrs.complexScript,
    },
    fontFiles,
    tokens,
    furniture,
    layouts,
  };
  const calculated = digest(base);
  if (calculated !== root.semanticDigest) {
    throw new Error("Deck master semantic metadata digest does not match");
  }
  return { ...base, semanticDigest: calculated };
}

function relationshipInventory(xml: string): readonly Record<string, string>[] {
  return [...xml.matchAll(/<Relationship\b([^>]*)\/>/g)].map((match) => attributes(match[1]));
}

function placeholderType(type: DeckMasterPlaceholder["type"]): string {
  return type === "content" ? "obj" : type;
}

function assertShapeMapping(
  xml: string,
  mapping: SemanticFurnitureMapping | SemanticPlaceholderMapping
): void {
  const shapeMatches = [...xml.matchAll(/<p:(sp|pic)\b[^>]*>([\s\S]*?)<\/p:\1>/g)].filter((match) => {
    const cNvPr = /<p:cNvPr\b([^>]*)\/>/.exec(match[2]);
    return cNvPr && attributes(cNvPr[1]).name === mapping.id;
  });
  if (shapeMatches.length !== 1) {
    throw new Error(`Semantic shape ${mapping.id} must be materialized exactly once`);
  }
  const shapeMatch = shapeMatches[0];
  const shapeBody = shapeMatch[2];
  const cNvPr = attributes(/<p:cNvPr\b([^>]*)\/>/.exec(shapeBody)![1]);
  if (Number(cNvPr.id) !== mapping.shapeId || !cNvPr.descr?.includes(`vnext:role=${mapping.role}`)) {
    throw new Error(`Semantic shape ${mapping.id} has unstable id or role metadata`);
  }
  if ("kind" in mapping) {
    if (mapping.kind === "pageNumber" && !/<a:fld\b[^>]*\btype="slidenum"/.test(shapeBody)) {
      throw new Error(`Page-number furniture ${mapping.id} is not an editable PowerPoint slide-number field`);
    }
    if (mapping.kind === "logo" && mapping.assetId !== null) {
      const blip = /<a:blip\b([^>]*)\/>/.exec(shapeBody);
      if (
        shapeMatch[1] !== "pic" ||
        !blip ||
        attributes(blip[1])["r:embed"] !== mapping.assetRelationshipId
      ) {
        throw new Error(`Logo furniture ${mapping.id} does not embed its declared asset`);
      }
    }
  }
  if ("index" in mapping) {
    const phMatch = /<p:ph\b([^>]*)\/>/.exec(shapeBody);
    if (!phMatch) throw new Error(`Semantic placeholder ${mapping.id} is missing p:ph`);
    const ph = attributes(phMatch[1]);
    if (Number(ph.idx) !== mapping.index || ph.type !== placeholderType(mapping.type)) {
      throw new Error(`Semantic placeholder ${mapping.id} does not match type/index mapping`);
    }
    const markerMatch = /<(?:[A-Za-z_][\w.-]*:)?placeholder\b([^>]*)\/>/.exec(shapeBody);
    const marker = markerMatch ? attributes(markerMatch[1]) : {};
    if (marker.role !== mapping.role || Number(marker.idx) !== mapping.index) {
      throw new Error(`Semantic placeholder ${mapping.id} extension does not match role/index mapping`);
    }
  }
}

function resolveRelationshipTarget(sourcePart: string, target: string): string {
  return target.startsWith("/")
    ? target.slice(1)
    : path.posix.normalize(path.posix.join(path.posix.dirname(sourcePart), target));
}

function assertTheme(themeXmlValue: string, metadata: DeckMasterSemanticMetadata): void {
  const theme = attributes(/<a:theme\b([^>]*)>/.exec(themeXmlValue)?.[1] ?? "");
  if (theme.name !== metadata.themeName) throw new Error("Theme name does not match semantic metadata");
  for (const name of DECK_MASTER_REQUIRED_THEME_COLORS) {
    const match = new RegExp(`<a:${name}>\\s*<a:srgbClr\\b([^>]*)\\/>\\s*</a:${name}>`).exec(themeXmlValue);
    const value = match ? attributes(match[1]).val : undefined;
    if (`#${value}`.toUpperCase() !== metadata.themeColors[name]?.toUpperCase()) {
      throw new Error(`Theme color ${name} does not match semantic metadata`);
    }
  }
  const majorFont = /<a:majorFont>([\s\S]*?)<\/a:majorFont>/.exec(themeXmlValue)?.[1] ?? "";
  for (const [tag, expected] of [
    ["latin", metadata.fonts.latin],
    ["ea", metadata.fonts.eastAsian],
    ["cs", metadata.fonts.complexScript],
  ] as const) {
    const match = new RegExp(`<a:${tag}\\b([^>]*)\\/>`).exec(majorFont);
    if (!match || attributes(match[1]).typeface !== expected) {
      throw new Error(`Theme ${tag} font does not match semantic metadata`);
    }
  }
}

function assertContentTypes(
  parts: ReadonlyMap<string, Uint8Array>,
  metadata: DeckMasterSemanticMetadata
): boolean {
  const value = parts.get("[Content_Types].xml");
  if (!value) return false;
  const xml = Buffer.from(value).toString("utf8");
  const overrides = [...xml.matchAll(/<Override\b([^>]*)\/>/g)].map((match) => attributes(match[1]));
  const expected = [
    { partName: `/${metadata.masterPartName}`, contentType: MASTER_CONTENT_TYPE },
    ...metadata.layouts.map((layout) => ({
      partName: `/${layout.partName}`,
      contentType: LAYOUT_CONTENT_TYPE,
    })),
    { partName: `/${metadata.themePartName}`, contentType: THEME_CONTENT_TYPE },
    ...metadata.furniture
      .filter((item) => item.assetPartName !== null)
      .map((item) => ({
        partName: `/${item.assetPartName!}`,
        contentType: PNG_CONTENT_TYPE,
      })),
  ];
  for (const item of expected) {
    if (!overrides.some(
      (override) => override.PartName === item.partName && override.ContentType === item.contentType
    )) {
      throw new Error(`Content type override is missing for ${item.partName}`);
    }
  }
  return true;
}

export function readDeckMasterParts(
  parts: ReadonlyMap<string, Uint8Array>
): DeckMasterReadBackReceipt {
  const masterXml = requirePart(parts, MASTER_PART);
  const metadata = parseMetadata(masterXml);
  if (metadata.masterPartName !== MASTER_PART || metadata.themePartName !== THEME_PART) {
    throw new Error("Deck master semantic metadata refers to unsupported part names");
  }
  const masterRels = relationshipInventory(requirePart(parts, MASTER_RELS_PART));
  const verified = new Set<string>([MASTER_PART, MASTER_RELS_PART]);
  for (const mapping of metadata.furniture) {
    assertShapeMapping(masterXml, mapping);
    if (mapping.assetPartName) {
      const relation = masterRels.find(
        (candidate) => candidate.Id === mapping.assetRelationshipId,
      );
      if (
        !relation ||
        !relation.Type?.endsWith("/image") ||
        resolveRelationshipTarget(MASTER_PART, relation.Target) !== mapping.assetPartName
      ) {
        throw new Error(`Logo relationship for ${mapping.id} is missing or inconsistent`);
      }
      const bytes = Buffer.from(parts.get(mapping.assetPartName) ?? []);
      if (
        bytes.length === 0 ||
        !bytes.subarray(0, PNG_SIGNATURE.length).equals(PNG_SIGNATURE) ||
        createHash("sha256").update(bytes).digest("hex") !== mapping.assetSha256
      ) {
        throw new Error(`Logo asset bytes for ${mapping.id} do not match semantic metadata`);
      }
      verified.add(mapping.assetPartName);
    }
  }
  for (const layout of metadata.layouts) {
    const relation = masterRels.find((candidate) => candidate.Id === layout.relationshipId);
    if (
      !relation ||
      !relation.Type?.endsWith("/slideLayout") ||
      resolveRelationshipTarget(MASTER_PART, relation.Target) !== layout.partName
    ) {
      throw new Error(`Master relationship for ${layout.id} is missing or inconsistent`);
    }
    const layoutXml = requirePart(parts, layout.partName);
    const cSld = attributes(/<p:cSld\b([^>]*)>/.exec(layoutXml)?.[1] ?? "");
    if (cSld.name !== layout.name) {
      throw new Error(`Layout name does not match metadata for ${layout.id}`);
    }
    for (const placeholder of layout.placeholders) assertShapeMapping(layoutXml, placeholder);
    const layoutExtension = /<(?:[A-Za-z_][\w.-]*:)?layout\b([^>]*)>/.exec(layoutXml);
    const extensionAttrs = layoutExtension ? attributes(layoutExtension[1]) : {};
    if (extensionAttrs.id !== layout.id || extensionAttrs.contractDigest !== metadata.contractDigest) {
      throw new Error(`Layout semantic extension does not match master metadata for ${layout.id}`);
    }
    const relsPart = `${path.posix.dirname(layout.partName)}/_rels/${path.posix.basename(layout.partName)}.rels`;
    const layoutRels = relationshipInventory(requirePart(parts, relsPart));
    const masterRelation = layoutRels.find((candidate) => candidate.Type?.endsWith("/slideMaster"));
    if (!masterRelation || resolveRelationshipTarget(layout.partName, masterRelation.Target) !== MASTER_PART) {
      throw new Error(`Layout ${layout.id} does not relate back to its slide master`);
    }
    verified.add(layout.partName);
    verified.add(relsPart);
  }
  const themeRelation = masterRels.find((candidate) => candidate.Type?.endsWith("/theme"));
  if (!themeRelation || resolveRelationshipTarget(MASTER_PART, themeRelation.Target) !== THEME_PART) {
    throw new Error("Slide master theme relationship is missing or inconsistent");
  }
  assertTheme(requirePart(parts, THEME_PART), metadata);
  verified.add(THEME_PART);
  return {
    semanticMetadata: metadata,
    verifiedPartNames: [...verified].sort((left, right) => left.localeCompare(right, "en")),
    contentTypesVerified: assertContentTypes(parts, metadata),
  };
}

export function assertDeckMasterReadBack(
  contract: unknown,
  parts: ReadonlyMap<string, Uint8Array>
): DeckMasterReadBackReceipt {
  const normalized = normalizeDeckMasterContract(contract);
  const receipt = readDeckMasterParts(parts);
  const expected = buildMetadata(normalized);
  if (stableJson(receipt.semanticMetadata) !== stableJson(expected)) {
    throw new Error("Deck master read-back does not match the requested contract");
  }
  return receipt;
}

export function createDeckMasterPartCompiler(): DeckMasterPartCompiler {
  return {
    compile: compileDeckMasterParts,
    readBack: readDeckMasterParts,
    assertReadBack: assertDeckMasterReadBack,
  };
}
