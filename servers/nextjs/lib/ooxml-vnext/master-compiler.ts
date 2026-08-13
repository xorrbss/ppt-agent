import { createHash } from "node:crypto";
import path from "node:path";

import {
  DECK_MASTER_CONTRACT_VERSION,
  DECK_MASTER_REQUIRED_THEME_COLORS,
  deckMasterRole,
  normalizeDeckMasterContract,
  type DeckMasterFurniture,
  type DeckMasterLayout,
  type DeckMasterPlaceholder,
  type DeckMasterRect,
  type DeckMasterTextStyle,
  type NormalizedDeckMasterContract,
} from "./contracts.ts";
import type {
  CompiledDeckMasterParts,
  ContentTypeOverride,
  DeckMasterBinaryAsset,
  DeckMasterSemanticMetadata,
  SemanticFontMapping,
  SemanticFurnitureMapping,
  SemanticLayoutMapping,
} from "./master-compiler-contracts.ts";
export * from "./master-compiler-contracts.ts";

const XML_HEADER = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>';
const NS = {
  a: "http://schemas.openxmlformats.org/drawingml/2006/main",
  p: "http://schemas.openxmlformats.org/presentationml/2006/main",
  r: "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
  rels: "http://schemas.openxmlformats.org/package/2006/relationships",
  avm: "urn:presenton:authored-vnext:deck-master:v1",
  v: "urn:presenton:ooxml-vnext:semantic-metadata:v1",
} as const;
const MASTER_EXTENSION_URI = "{7A13C7D4-75D2-4AA0-94ED-FAFF2DB5A9CE}";
const PLACEHOLDER_EXTENSION_URI = "{7D786E45-7874-4D65-7461-646174613A31}";
const EMU_PER_PIXEL = 9_525;
const MASTER_PART = "ppt/slideMasters/slideMaster1.xml";
const MASTER_RELS_PART = "ppt/slideMasters/_rels/slideMaster1.xml.rels";
const THEME_PART = "ppt/theme/theme1.xml";
const MASTER_CONTENT_TYPE = "application/vnd.openxmlformats-officedocument.presentationml.slideMaster+xml";
const LAYOUT_CONTENT_TYPE = "application/vnd.openxmlformats-officedocument.presentationml.slideLayout+xml";
const THEME_CONTENT_TYPE = "application/vnd.openxmlformats-officedocument.theme+xml";
const PNG_CONTENT_TYPE = "image/png";
const PNG_SIGNATURE = Buffer.from("89504e470d0a1a0a", "hex");

function xmlEscape(value: string): string {
  return value
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&apos;");
}

function stableValue(value: unknown): unknown {
  if (Array.isArray(value)) return value.map(stableValue);
  if (value && typeof value === "object") {
    return Object.fromEntries(
      Object.entries(value as Record<string, unknown>)
        .sort(([left], [right]) => left.localeCompare(right, "en"))
        .map(([key, child]) => [key, stableValue(child)]),
    );
  }
  return value;
}

function stableJson(value: unknown): string {
  return JSON.stringify(stableValue(value));
}

function digest(value: unknown): string {
  return `sha256:${createHash("sha256").update(typeof value === "string" ? value : stableJson(value)).digest("hex")}`;
}

function contractDigest(contract: NormalizedDeckMasterContract): string {
  const semanticContract = { ...contract } as Record<string, unknown>;
  delete semanticContract.digest;
  return digest(semanticContract);
}

function semanticDigestPayload(metadata: Omit<DeckMasterSemanticMetadata, "semanticDigest">): unknown {
  return metadata;
}

function asEmu(value: number): number {
  return Math.round(value * EMU_PER_PIXEL);
}

function xfrmXml(rect: DeckMasterRect): string {
  return `<a:xfrm><a:off x="${asEmu(rect.x)}" y="${asEmu(rect.y)}"/><a:ext cx="${asEmu(rect.width)}" cy="${asEmu(rect.height)}"/></a:xfrm>`;
}

function solidFill(color: string): string {
  return `<a:solidFill><a:srgbClr val="${color.slice(1).toUpperCase()}"/></a:solidFill>`;
}

function shapeLocks(locked: boolean): string {
  return locked
    ? '<a:spLocks noGrp="1" noMove="1" noResize="1" noEditPoints="1" noChangeShapeType="1"/>'
    : "";
}

function ownershipDescription(owner: "master" | "layout", role: string, locked: boolean, modelWritable: boolean): string {
  return `vnext:owner=${owner};vnext:role=${role};locked=${locked ? 1 : 0};modelWritable=${modelWritable ? 1 : 0}`;
}

function furnitureXml(
  item: DeckMasterFurniture,
  mapping: SemanticFurnitureMapping,
  font: string,
): string {
  const shapeId = mapping.shapeId;
  const role = deckMasterRole(item.id);
  const description = ownershipDescription("master", role, item.ownership.locked, item.ownership.modelWritable);
  const textKind = item.kind === "text" || item.kind === "pageNumber";
  const placeholder = item.kind === "pageNumber" ? `<p:ph type="sldNum" idx="${shapeId}"/>` : "";
  const nonVisual = `<p:nvSpPr><p:cNvPr id="${shapeId}" name="${xmlEscape(item.id)}" descr="${xmlEscape(description)}"/><p:cNvSpPr${textKind ? ' txBox="1"' : ""}>${shapeLocks(item.ownership.locked)}</p:cNvSpPr><p:nvPr>${placeholder}</p:nvPr></p:nvSpPr>`;
  if (item.kind === "text") {
    const run = `<a:p><a:r><a:rPr lang="ko-KR" sz="1000"${item.id.endsWith(":brand") ? ' b="1"' : ""}><a:solidFill><a:srgbClr val="${item.fill!.slice(1).toUpperCase()}"/></a:solidFill><a:latin typeface="${xmlEscape(font)}"/><a:ea typeface="${xmlEscape(font)}"/><a:cs typeface="${xmlEscape(font)}"/></a:rPr><a:t>${xmlEscape(item.text!)}</a:t></a:r><a:endParaRPr lang="ko-KR"/></a:p>`;
    return `<p:sp>${nonVisual}<p:spPr>${xfrmXml(item.bounds)}<a:prstGeom prst="rect"><a:avLst/></a:prstGeom><a:noFill/><a:ln><a:noFill/></a:ln></p:spPr><p:txBody><a:bodyPr wrap="none" anchor="ctr"><a:noAutofit/></a:bodyPr><a:lstStyle/>${run}</p:txBody></p:sp>`;
  }
  if (item.kind === "pageNumber") {
    const fieldId = `{00000000-0000-0000-0000-${String(shapeId).padStart(12, "0")}}`;
    const runProperties = `<a:rPr lang="ko-KR" sz="1000"><a:solidFill><a:srgbClr val="${item.fill!.slice(1).toUpperCase()}"/></a:solidFill><a:latin typeface="${xmlEscape(font)}"/><a:ea typeface="${xmlEscape(font)}"/><a:cs typeface="${xmlEscape(font)}"/></a:rPr>`;
    const field = `<a:p><a:fld id="${fieldId}" type="slidenum">${runProperties}<a:t>${xmlEscape(item.text ?? "1")}</a:t></a:fld><a:endParaRPr lang="ko-KR"/></a:p>`;
    return `<p:sp>${nonVisual}<p:spPr>${xfrmXml(item.bounds)}<a:prstGeom prst="rect"><a:avLst/></a:prstGeom><a:noFill/><a:ln><a:noFill/></a:ln></p:spPr><p:txBody><a:bodyPr wrap="none" anchor="ctr"><a:noAutofit/></a:bodyPr><a:lstStyle/>${field}</p:txBody></p:sp>`;
  }
  if (item.kind === "logo") {
    if (mapping.assetId) {
      const locks = item.ownership.locked
        ? '<a:picLocks noGrp="1" noMove="1" noResize="1" noChangeAspect="1"/>'
        : '<a:picLocks noChangeAspect="1"/>';
      return `<p:pic><p:nvPicPr><p:cNvPr id="${shapeId}" name="${xmlEscape(item.id)}" descr="${xmlEscape(description)}"/><p:cNvPicPr>${locks}</p:cNvPicPr><p:nvPr/></p:nvPicPr><p:blipFill><a:blip r:embed="${mapping.assetRelationshipId}"/><a:stretch><a:fillRect/></a:stretch></p:blipFill><p:spPr>${xfrmXml(item.bounds)}<a:prstGeom prst="rect"><a:avLst/></a:prstGeom></p:spPr></p:pic>`;
    }
    return `<p:sp>${nonVisual}<p:spPr>${xfrmXml(item.bounds)}<a:prstGeom prst="rect"><a:avLst/></a:prstGeom><a:noFill/><a:ln><a:noFill/></a:ln></p:spPr></p:sp>`;
  }
  const geometry = item.kind === "line" ? "line" : "rect";
  const fill = item.kind === "rect" ? solidFill(item.fill!) : "<a:noFill/>";
  const line = item.kind === "line"
    ? `<a:ln w="${Math.max(1, asEmu(item.bounds.height))}">${solidFill(item.stroke!)}<a:prstDash val="solid"/></a:ln>`
    : "<a:ln><a:noFill/></a:ln>";
  return `<p:sp>${nonVisual}<p:spPr>${xfrmXml(item.bounds)}<a:prstGeom prst="${geometry}"><a:avLst/></a:prstGeom>${fill}${line}</p:spPr></p:sp>`;
}

function placeholderType(type: DeckMasterPlaceholder["type"]): string {
  return type === "content" ? "obj" : type;
}

function placeholderXml(item: DeckMasterPlaceholder, shapeId: number): string {
  const role = deckMasterRole(item.id);
  const style = item.textStyle;
  const description = ownershipDescription("layout", role, item.ownership.locked, item.ownership.modelWritable);
  const title = item.type === "title";
  const color = title ? "tx1" : "tx2";
  const fontRef = title ? "mj" : "mn";
  const semanticMarker = `<p:extLst><p:ext uri="${PLACEHOLDER_EXTENSION_URI}"><v:placeholder xmlns:v="${NS.v}" role="${xmlEscape(role)}" idx="${item.index}"/></p:ext></p:extLst>`;
  return `<p:sp><p:nvSpPr><p:cNvPr id="${shapeId}" name="${xmlEscape(item.id)}" descr="${xmlEscape(description)}"/><p:cNvSpPr txBox="1">${shapeLocks(item.ownership.locked)}</p:cNvSpPr><p:nvPr><p:ph type="${placeholderType(item.type)}" idx="${item.index}"/>${semanticMarker}</p:nvPr></p:nvSpPr><p:spPr>${xfrmXml(item.bounds)}<a:prstGeom prst="rect"><a:avLst/></a:prstGeom><a:noFill/><a:ln><a:noFill/></a:ln></p:spPr><p:txBody>${bodyProperties(style, title ? "ctr" : "t")}<a:lstStyle><a:lvl1pPr algn="l" eaLnBrk="1" latinLnBrk="0" hangingPunct="0"><a:defRPr sz="${Math.round(style.fontPt * 100)}"${title ? ' b="1"' : ""}><a:solidFill><a:schemeClr val="${color}"/></a:solidFill><a:latin typeface="+${fontRef}-lt"/><a:ea typeface="+${fontRef}-ea"/><a:cs typeface="+${fontRef}-cs"/></a:defRPr></a:lvl1pPr></a:lstStyle><a:p><a:endParaRPr lang="ko-KR"/></a:p></p:txBody></p:sp>`;
}

function bodyProperties(style: DeckMasterTextStyle, anchor: "ctr" | "t"): string {
  const margins = style.marginsPx;
  return `<a:bodyPr wrap="square" anchor="${anchor}" lIns="${asEmu(margins.left)}" rIns="${asEmu(margins.right)}" tIns="${asEmu(margins.top)}" bIns="${asEmu(margins.bottom)}"><a:noAutofit/></a:bodyPr>`;
}

function relationshipsXml(items: readonly { id: string; type: string; target: string }[]): string {
  return `${XML_HEADER}<Relationships xmlns="${NS.rels}">${items.map((item) => `<Relationship Id="${item.id}" Type="${NS.r}/${item.type}" Target="${item.target}"/>`).join("")}</Relationships>`;
}

function themeXml(contract: NormalizedDeckMasterContract, fonts: SemanticFontMapping): string {
  const colors = contract.theme.colors;
  const scheme = DECK_MASTER_REQUIRED_THEME_COLORS.map(
    (name) => `<a:${name}><a:srgbClr val="${colors[name].slice(1).toUpperCase()}"/></a:${name}>`,
  ).join("");
  const scripts = contract.fontManifest.themeScripts;
  const scriptXml = (["Hang", "Jpan", "Hans", "Hant"] as const)
    .map((script) => `<a:font script="${script}" typeface="${xmlEscape(scripts[script] ?? fonts.eastAsian)}"/>`)
    .join("");
  const fontGroup = `<a:latin typeface="${xmlEscape(fonts.latin)}"/><a:ea typeface="${xmlEscape(fonts.eastAsian)}"/><a:cs typeface="${xmlEscape(fonts.complexScript)}"/>${scriptXml}`;
  return `${XML_HEADER}<a:theme xmlns:a="${NS.a}" name="${xmlEscape(contract.theme.name)}"><a:themeElements><a:clrScheme name="${xmlEscape(contract.theme.name)}">${scheme}</a:clrScheme><a:fontScheme name="${xmlEscape(contract.name)} Fonts"><a:majorFont>${fontGroup}</a:majorFont><a:minorFont>${fontGroup}</a:minorFont></a:fontScheme><a:fmtScheme name="${xmlEscape(contract.name)} Format"><a:fillStyleLst><a:solidFill><a:schemeClr val="phClr"/></a:solidFill><a:solidFill><a:schemeClr val="accent1"/></a:solidFill><a:solidFill><a:schemeClr val="accent2"/></a:solidFill></a:fillStyleLst><a:lnStyleLst><a:ln w="12700"><a:solidFill><a:schemeClr val="phClr"/></a:solidFill><a:prstDash val="solid"/></a:ln><a:ln w="25400"><a:solidFill><a:schemeClr val="phClr"/></a:solidFill><a:prstDash val="solid"/></a:ln><a:ln w="38100"><a:solidFill><a:schemeClr val="phClr"/></a:solidFill><a:prstDash val="solid"/></a:ln></a:lnStyleLst><a:effectStyleLst><a:effectStyle><a:effectLst/></a:effectStyle><a:effectStyle><a:effectLst/></a:effectStyle><a:effectStyle><a:effectLst/></a:effectStyle></a:effectStyleLst><a:bgFillStyleLst><a:solidFill><a:schemeClr val="phClr"/></a:solidFill><a:solidFill><a:schemeClr val="bg1"/></a:solidFill><a:solidFill><a:schemeClr val="bg2"/></a:solidFill></a:bgFillStyleLst></a:fmtScheme></a:themeElements><a:objectDefaults/><a:extraClrSchemeLst/></a:theme>`;
}

function metadataXml(metadata: DeckMasterSemanticMetadata): string {
  const colors = Object.entries(metadata.themeColors)
    .sort(([left], [right]) => left.localeCompare(right, "en"))
    .map(([name, value]) => `<avm:color name="${xmlEscape(name)}" value="${xmlEscape(value.toUpperCase())}"/>`)
    .join("");
  const tokens = Object.entries(metadata.tokens)
    .sort(([left], [right]) => left.localeCompare(right, "en"))
    .map(([name, value]) => `<avm:token name="${xmlEscape(name)}" kind="${typeof value}" value="${xmlEscape(String(value))}"/>`)
    .join("");
  const furniture = metadata.furniture
    .map((item) => `<avm:item id="${xmlEscape(item.id)}" role="${xmlEscape(item.role)}" kind="${item.kind}" shapeId="${item.shapeId}" visibility="${item.visibility}" assetId="${xmlEscape(item.assetId ?? "")}" assetSha256="${item.assetSha256 ?? ""}" assetPartName="${xmlEscape(item.assetPartName ?? "")}" assetRelationshipId="${xmlEscape(item.assetRelationshipId ?? "")}"/>`)
    .join("");
  const fontFiles = metadata.fontFiles
    .map((file) => `<avm:file style="${xmlEscape(file.style)}" fileName="${xmlEscape(file.fileName)}" version="${xmlEscape(file.version)}" sizeBytes="${file.sizeBytes}" sha256="${file.sha256}" sourcePathDigest="${file.sourcePathDigest}"/>`)
    .join("");
  const layouts = metadata.layouts
    .map((layout) => `<avm:layout id="${xmlEscape(layout.id)}" role="${xmlEscape(layout.role)}" name="${xmlEscape(layout.name)}" kind="${layout.kind}" partName="${layout.partName}" relationshipId="${layout.relationshipId}">${layout.placeholders.map((placeholder) => `<avm:placeholder id="${xmlEscape(placeholder.id)}" role="${xmlEscape(placeholder.role)}" type="${placeholder.type}" idx="${placeholder.index}" shapeId="${placeholder.shapeId}"/>`).join("")}</avm:layout>`)
    .join("");
  return `<p:extLst><p:ext uri="${MASTER_EXTENSION_URI}"><avm:deckMaster xmlns:avm="${NS.avm}" version="${metadata.version}" contractId="${xmlEscape(metadata.contractId)}" contractName="${xmlEscape(metadata.contractName)}" contractDigest="${metadata.contractDigest}" semanticDigest="${metadata.semanticDigest}" masterPartName="${metadata.masterPartName}" themePartName="${metadata.themePartName}" themeName="${xmlEscape(metadata.themeName)}"><avm:fonts primary="${xmlEscape(metadata.fonts.primary)}" latin="${xmlEscape(metadata.fonts.latin)}" eastAsian="${xmlEscape(metadata.fonts.eastAsian)}" complexScript="${xmlEscape(metadata.fonts.complexScript)}"/><avm:fontFiles>${fontFiles}</avm:fontFiles><avm:themeColors>${colors}</avm:themeColors><avm:tokens>${tokens}</avm:tokens><avm:furniture>${furniture}</avm:furniture><avm:layouts>${layouts}</avm:layouts></avm:deckMaster></p:ext></p:extLst>`;
}

function orderedLayouts(contract: NormalizedDeckMasterContract): readonly DeckMasterLayout[] {
  const ranks: Readonly<Record<DeckMasterLayout["kind"], number>> = {
    cover: 0,
    section: 1,
    standard: 2,
    "long-title": 3,
  };
  return [...contract.layouts].sort((left, right) => {
    return ranks[left.kind] - ranks[right.kind] || left.id.localeCompare(right.id, "en");
  });
}

export function buildMetadata(contract: NormalizedDeckMasterContract): DeckMasterSemanticMetadata {
  const scripts = contract.fontManifest.themeScripts;
  const fonts: SemanticFontMapping = {
    primary: contract.fontManifest.primary,
    latin: scripts.latin,
    eastAsian: scripts.eastAsian ?? scripts.Hang ?? contract.fontManifest.windowsFallback ?? contract.fontManifest.primary,
    complexScript: scripts.complexScript ?? contract.fontManifest.primary,
  };
  const layouts = orderedLayouts(contract).map((layout, layoutIndex): SemanticLayoutMapping => ({
    id: layout.id,
    role: deckMasterRole(layout.id),
    name: layout.name,
    kind: layout.kind,
    partName: `ppt/slideLayouts/slideLayout${layoutIndex + 1}.xml`,
    relationshipId: `rId${layoutIndex + 1}`,
    placeholders: layout.placeholders.map((placeholder, placeholderIndex) => ({
      id: placeholder.id,
      role: deckMasterRole(placeholder.id),
      type: placeholder.type,
      index: placeholder.index,
      shapeId: placeholderIndex + 2,
    })),
  }));
  const base: Omit<DeckMasterSemanticMetadata, "semanticDigest"> = {
    version: DECK_MASTER_CONTRACT_VERSION,
    contractId: contract.id,
    contractName: contract.name,
    contractDigest: contractDigest(contract),
    masterPartName: MASTER_PART,
    themePartName: THEME_PART,
    themeName: contract.theme.name,
    themeColors: Object.fromEntries(
      Object.entries(contract.theme.colors)
        .sort(([left], [right]) => left.localeCompare(right, "en"))
        .map(([name, value]) => [name, value.toUpperCase()]),
    ),
    fonts,
    fontFiles: (contract.fontManifest.files ?? []).map((file) => ({
      style: file.style,
      fileName: path.win32.basename(file.path.replaceAll("/", "\\")),
      version: file.version,
      sizeBytes: file.sizeBytes,
      sha256: file.sha256,
      sourcePathDigest: digest(file.path),
    })),
    tokens: contract.tokens,
    furniture: contract.furniture.map((item, index) => {
      const shapeId = index + 2;
      const role = deckMasterRole(item.id);
      const assetSha256 = item.assetSha256 ?? null;
      return {
        id: item.id,
        role,
        kind: item.kind,
        shapeId,
        visibility: item.visibility ?? "always",
        assetId: item.assetId ?? null,
        assetSha256,
        assetPartName: assetSha256
          ? `ppt/media/${role}-${assetSha256.slice(0, 16)}.png`
          : null,
        assetRelationshipId: assetSha256 ? `rIdLogo${shapeId}` : null,
      };
    }),
    layouts,
  };
  return { ...base, semanticDigest: digest(semanticDigestPayload(base)) };
}

function commonShapeTreeStart(name: string): string {
  return `<p:cSld name="${xmlEscape(name)}"><p:spTree><p:nvGrpSpPr><p:cNvPr id="1" name=""/><p:cNvGrpSpPr/><p:nvPr/></p:nvGrpSpPr><p:grpSpPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="0" cy="0"/><a:chOff x="0" y="0"/><a:chExt cx="0" cy="0"/></a:xfrm></p:grpSpPr>`;
}

function verifiedAssetBytes(
  metadata: DeckMasterSemanticMetadata,
  assets: readonly DeckMasterBinaryAsset[],
): ReadonlyMap<string, Buffer> {
  const expected = new Map(
    metadata.furniture
      .filter((item) => item.assetId !== null)
      .map((item) => [item.assetId!, item]),
  );
  const provided = new Map<string, Buffer>();
  for (const asset of assets) {
    if (provided.has(asset.assetId) || !expected.has(asset.assetId)) {
      throw new Error("Deck master binary assets contain duplicate or unused asset IDs");
    }
    if (asset.mediaType !== PNG_CONTENT_TYPE || !/^[0-9a-f]{64}$/.test(asset.sha256)) {
      throw new Error("Deck master logo assets must be digest-pinned PNG files");
    }
    const bytes = Buffer.from(asset.bytesBase64, "base64");
    if (
      bytes.length === 0 ||
      bytes.length > 5_000_000 ||
      bytes.toString("base64") !== asset.bytesBase64 ||
      !bytes.subarray(0, PNG_SIGNATURE.length).equals(PNG_SIGNATURE) ||
      createHash("sha256").update(bytes).digest("hex") !== asset.sha256 ||
      expected.get(asset.assetId)!.assetSha256 !== asset.sha256
    ) {
      throw new Error("Deck master logo asset bytes do not match the contract digest");
    }
    provided.set(asset.assetId, bytes);
  }
  if (provided.size !== expected.size) {
    throw new Error("Configured logo assets require verified WorkerAsset bytes");
  }
  return provided;
}

function compileNormalized(
  contract: NormalizedDeckMasterContract,
  assets: readonly DeckMasterBinaryAsset[],
): CompiledDeckMasterParts {
  const metadata = buildMetadata(contract);
  const assetBytes = verifiedAssetBytes(metadata, assets);
  const parts = new Map<string, Buffer>();
  const furniture = contract.furniture
    .map((item, index) => furnitureXml(item, metadata.furniture[index], metadata.fonts.primary))
    .join("");
  const masterRelationships = [
    ...metadata.layouts.map((layout) => ({
      id: layout.relationshipId,
      type: "slideLayout",
      target: `../slideLayouts/${path.posix.basename(layout.partName)}`,
    })),
    { id: `rId${metadata.layouts.length + 1}`, type: "theme", target: "../theme/theme1.xml" },
    ...metadata.furniture
      .filter((item) => item.assetPartName !== null)
      .map((item) => ({
        id: item.assetRelationshipId!,
        type: "image",
        target: `../media/${path.posix.basename(item.assetPartName!)}`,
      })),
  ];
  const master = `${XML_HEADER}<p:sldMaster xmlns:a="${NS.a}" xmlns:r="${NS.r}" xmlns:p="${NS.p}">${commonShapeTreeStart(contract.name)}${furniture}</p:spTree></p:cSld><p:clrMap accent1="accent1" accent2="accent2" accent3="accent3" accent4="accent4" accent5="accent5" accent6="accent6" bg1="lt1" bg2="lt2" folHlink="folHlink" hlink="hlink" tx1="dk1" tx2="dk2"/><p:sldLayoutIdLst>${metadata.layouts.map((layout, index) => `<p:sldLayoutId id="${2147483649 + index}" r:id="${layout.relationshipId}"/>`).join("")}</p:sldLayoutIdLst><p:txStyles><p:titleStyle><a:lvl1pPr algn="l" eaLnBrk="1" latinLnBrk="0" hangingPunct="0"><a:defRPr sz="3600" b="1"><a:solidFill><a:schemeClr val="tx1"/></a:solidFill><a:latin typeface="+mj-lt"/><a:ea typeface="+mj-ea"/><a:cs typeface="+mj-cs"/></a:defRPr></a:lvl1pPr></p:titleStyle><p:bodyStyle><a:lvl1pPr algn="l" eaLnBrk="1" latinLnBrk="0" hangingPunct="0"><a:defRPr sz="1800"><a:solidFill><a:schemeClr val="tx2"/></a:solidFill><a:latin typeface="+mn-lt"/><a:ea typeface="+mn-ea"/><a:cs typeface="+mn-cs"/></a:defRPr></a:lvl1pPr></p:bodyStyle><p:otherStyle><a:defPPr><a:defRPr lang="ko-KR"/></a:defPPr></p:otherStyle></p:txStyles>${metadataXml(metadata)}</p:sldMaster>`;
  parts.set(MASTER_PART, Buffer.from(master, "utf8"));
  parts.set(MASTER_RELS_PART, Buffer.from(relationshipsXml(masterRelationships), "utf8"));
  parts.set(THEME_PART, Buffer.from(themeXml(contract, metadata.fonts), "utf8"));
  for (const item of metadata.furniture) {
    if (item.assetId && item.assetPartName) {
      parts.set(item.assetPartName, Buffer.from(assetBytes.get(item.assetId)!));
    }
  }

  for (const mapping of metadata.layouts) {
    const layout = contract.layouts.find((candidate) => candidate.id === mapping.id)!;
    const shapes = layout.placeholders.map((placeholder, index) => placeholderXml(placeholder, index + 2)).join("");
    const layoutType = {
      cover: "title",
      section: "secHead",
      standard: "obj",
      "long-title": "cust",
    }[layout.kind];
    const layoutMetadata = `<p:extLst><p:ext uri="${MASTER_EXTENSION_URI}"><avm:layout xmlns:avm="${NS.avm}" id="${xmlEscape(mapping.id)}" role="${xmlEscape(mapping.role)}" kind="${mapping.kind}" contractDigest="${metadata.contractDigest}">${mapping.placeholders.map((placeholder) => `<avm:placeholder id="${xmlEscape(placeholder.id)}" role="${xmlEscape(placeholder.role)}" type="${placeholder.type}" idx="${placeholder.index}" shapeId="${placeholder.shapeId}"/>`).join("")}</avm:layout></p:ext></p:extLst>`;
    const xml = `${XML_HEADER}<p:sldLayout xmlns:a="${NS.a}" xmlns:r="${NS.r}" xmlns:p="${NS.p}" type="${layoutType}" preserve="1" showMasterSp="1">${commonShapeTreeStart(layout.name)}${shapes}</p:spTree></p:cSld><p:clrMapOvr><a:masterClrMapping/></p:clrMapOvr>${layoutMetadata}</p:sldLayout>`;
    parts.set(mapping.partName, Buffer.from(xml, "utf8"));
    const relsPart = `${path.posix.dirname(mapping.partName)}/_rels/${path.posix.basename(mapping.partName)}.rels`;
    parts.set(relsPart, Buffer.from(relationshipsXml([{ id: "rId1", type: "slideMaster", target: "../slideMasters/slideMaster1.xml" }]), "utf8"));
  }

  const contentTypeOverrides: ContentTypeOverride[] = [
    { partName: `/${MASTER_PART}`, contentType: MASTER_CONTENT_TYPE },
    ...metadata.layouts.map((layout) => ({ partName: `/${layout.partName}`, contentType: LAYOUT_CONTENT_TYPE })),
    { partName: `/${THEME_PART}`, contentType: THEME_CONTENT_TYPE },
    ...metadata.furniture
      .filter((item) => item.assetPartName !== null)
      .map((item) => ({ partName: `/${item.assetPartName!}`, contentType: PNG_CONTENT_TYPE })),
  ];
  const contentTypeOverridesXml = Buffer.from(
    contentTypeOverrides.map((item) => `<Override PartName="${item.partName}" ContentType="${item.contentType}"/>`).join(""),
    "utf8",
  );
  return { parts, contentTypeOverrides, contentTypeOverridesXml, semanticMetadata: metadata };
}

export function compileDeckMasterParts(
  contract: unknown,
  assets: readonly DeckMasterBinaryAsset[] = [],
): CompiledDeckMasterParts {
  return compileNormalized(normalizeDeckMasterContract(contract), assets);
}

export {
  assertDeckMasterReadBack,
  createDeckMasterPartCompiler,
  readDeckMasterParts,
} from "./master-readback.ts";
