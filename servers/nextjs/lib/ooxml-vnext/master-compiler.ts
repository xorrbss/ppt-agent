import { createHash } from "node:crypto";
import path from "node:path";

import type {
  DeckMasterContractV1 as AuthoredDeckMasterContract,
  SemanticMasterElement,
} from "../authored-vnext/master/contracts.ts";

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
  SemanticElementExportMapping,
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
const JPEG_CONTENT_TYPE = "image/jpeg";
const SVG_CONTENT_TYPE = "image/svg+xml";
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
  return `<p:sp><p:nvSpPr><p:cNvPr id="${shapeId}" name="${xmlEscape(item.id)}" descr="${xmlEscape(description)}"/><p:cNvSpPr txBox="1">${shapeLocks(item.ownership.locked)}</p:cNvSpPr><p:nvPr><p:ph type="${placeholderType(item.type)}" idx="${item.index}"/>${semanticMarker}</p:nvPr></p:nvSpPr><p:spPr>${xfrmXml(item.bounds)}<a:prstGeom prst="rect"><a:avLst/></a:prstGeom><a:noFill/><a:ln><a:noFill/></a:ln></p:spPr><p:txBody>${bodyProperties(style, title ? "ctr" : "t", !title)}<a:lstStyle><a:lvl1pPr algn="l" eaLnBrk="1" latinLnBrk="0" hangingPunct="0"><a:defRPr sz="${Math.round(style.fontPt * 100)}"${title ? ' b="1"' : ""}><a:solidFill><a:schemeClr val="${color}"/></a:solidFill><a:latin typeface="+${fontRef}-lt"/><a:ea typeface="+${fontRef}-ea"/><a:cs typeface="+${fontRef}-cs"/></a:defRPr></a:lvl1pPr></a:lstStyle><a:p><a:endParaRPr lang="ko-KR"/></a:p></p:txBody></p:sp>`;
}

function bodyProperties(
  style: DeckMasterTextStyle,
  anchor: "ctr" | "t",
  shrinkToFit: boolean,
): string {
  const margins = style.marginsPx;
  const fit = shrinkToFit ? "<a:normAutofit/>" : "<a:noAutofit/>";
  return `<a:bodyPr wrap="square" anchor="${anchor}" lIns="${asEmu(margins.left)}" rIns="${asEmu(margins.right)}" tIns="${asEmu(margins.top)}" bIns="${asEmu(margins.bottom)}">${fit}</a:bodyPr>`;
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
    .flatMap((item, index) => item.visibility === "hidden"
      ? []
      : [furnitureXml(item, metadata.furniture[index], metadata.fonts.primary)])
    .join("");
  const masterRelationships = [
    ...metadata.layouts.map((layout) => ({
      id: layout.relationshipId,
      type: "slideLayout",
      target: `../slideLayouts/${path.posix.basename(layout.partName)}`,
    })),
    { id: `rId${metadata.layouts.length + 1}`, type: "theme", target: "../theme/theme1.xml" },
    ...metadata.furniture
      .filter((item) => item.visibility !== "hidden" && item.assetPartName !== null)
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

function authoredToCompilerContract(contract: AuthoredDeckMasterContract): NormalizedDeckMasterContract {
  const colors = contract.tokens.colors;
  const themeColors = {
    dk1: colors.textPrimary,
    lt1: colors.background,
    dk2: colors.textSecondary,
    lt2: colors.surface,
    accent1: colors.accent,
    accent2: colors.warning,
    accent3: colors.divider,
    accent4: colors.accent,
    accent5: colors.textSecondary,
    accent6: colors.surface,
    hlink: colors.accent,
    folHlink: colors.warning,
  };
  const furniture = Object.values(contract.furniture).map((item): DeckMasterFurniture => ({
    id: item.id,
    kind: item.kind === "logo" ? "logo" : item.kind,
    bounds: item.bounds,
    ownership: item.ownership,
    visibility: item.visibility,
    ...(item.text === undefined ? {} : { text: item.text }),
    ...(item.assetId === undefined ? {} : { assetId: item.assetId }),
    ...(item.assetId === undefined
      ? {}
      : {
          assetSha256: [contract.brandAssets.logo, contract.brandAssets.wordmark]
            .find((asset) => asset?.assetId === item.assetId)?.digest.slice("sha256:".length),
        }),
    ...(item.fill === undefined ? {} : { fill: item.fill }),
    ...(item.stroke === undefined ? {} : { stroke: item.stroke }),
  }));
  const layouts = [
    ...Object.values(contract.layouts),
    ...(contract.customLayouts ?? []),
  ].map((layout): DeckMasterLayout => ({
    id: layout.id,
    name: layout.name,
    kind: layout.kind === "custom" ? "standard" : layout.kind === "longTitle" ? "long-title" : layout.kind,
    placeholders: layout.slots.map((slot): DeckMasterPlaceholder => {
      const style = contract.semanticStyles[slot.style];
      return {
        id: slot.id.replace(/^slot:/, "placeholder:"),
        type: slot.role,
        index: slot.placeholderIndex,
        bounds: slot.bounds,
        ownership: slot.ownership,
        textStyle: {
          fontPt: style.fontPt,
          lineHeight: style.lineHeight,
          maxLines: style.maxLines,
          marginsPx: {
            left: style.margins.leftPt * 4 / 3,
            right: style.margins.rightPt * 4 / 3,
            top: style.margins.topPt * 4 / 3,
            bottom: style.margins.bottomPt * 4 / 3,
          },
        },
      };
    }),
  }));
  return {
    contractVersion: DECK_MASTER_CONTRACT_VERSION,
    id: contract.masterId,
    name: contract.name,
    slideSizePx: { x: 0, y: 0, width: contract.canvas.widthPx, height: contract.canvas.heightPx },
    theme: { name: `${contract.name} Theme`, colors: themeColors },
    tokens: {
      background: colors.background,
      surface: colors.surface,
      textPrimary: colors.textPrimary,
      textSecondary: colors.textSecondary,
      accent: colors.accent,
      divider: colors.divider,
      warning: colors.warning,
    },
    fontManifest: {
      primary: contract.fontManifest.primary,
      fallbacks: contract.fontManifest.fallbacks,
      windowsFallback: contract.fontManifest.windowsFallback,
      themeScripts: {
        latin: contract.fontManifest.themeScripts.latin,
        eastAsian: contract.fontManifest.themeScripts.hang,
        complexScript: contract.fontManifest.themeScripts.latin,
        Hang: contract.fontManifest.themeScripts.hang,
        Jpan: contract.fontManifest.themeScripts.jpan,
        Hans: contract.fontManifest.themeScripts.hans,
        Hant: contract.fontManifest.themeScripts.hant,
      },
      files: contract.fontManifest.files,
      ...(contract.fontManifest.digest ? { digest: contract.fontManifest.digest } : {}),
    },
    furniture,
    layouts,
    ...(contract.digest ? { digest: contract.digest } : {}),
  };
}

function mediaExtension(mediaType: DeckMasterBinaryAsset["mediaType"]): string {
  if (mediaType === JPEG_CONTENT_TYPE) return "jpg";
  if (mediaType === SVG_CONTENT_TYPE) return "svg";
  return "png";
}

function semanticAssetRole(identifier: string): string {
  if (!/^asset:[a-z0-9][a-z0-9._-]*$/.test(identifier)) {
    throw new Error(`Invalid semantic asset identifier: ${identifier}`);
  }
  return identifier.slice("asset:".length);
}

function semanticXfrmXml(element: SemanticMasterElement): string {
  const rotation = Math.round(element.rotation * 60_000);
  return `<a:xfrm${rotation === 0 ? "" : ` rot="${rotation}"`}><a:off x="${asEmu(element.bounds.x)}" y="${asEmu(element.bounds.y)}"/><a:ext cx="${asEmu(element.bounds.width)}" cy="${asEmu(element.bounds.height)}"/></a:xfrm>`;
}

function semanticSolidFill(color: string | undefined, opacity = 1): string {
  if (!color) return "<a:noFill/>";
  const alpha = Math.max(0, Math.min(100_000, Math.round(opacity * 100_000)));
  return `<a:solidFill><a:srgbClr val="${color.slice(1).toUpperCase()}">${alpha === 100_000 ? "" : `<a:alpha val="${alpha}"/>`}</a:srgbClr></a:solidFill>`;
}

function semanticElementXml(
  element: SemanticMasterElement,
  mapping: SemanticElementExportMapping,
  primaryFont: string,
): string {
  const description = `vnext:semantic=1;editableByUser=${element.editableByUser ? 1 : 0};editableByAI=${element.editableByAI ? 1 : 0};locked=${element.locked ? 1 : 0}`;
  const locks = shapeLocks(element.locked);
  const opacity = element.style.opacity ?? 1;
  if (element.type === "image") {
    const crop = element.content.crop;
    const srcRect = crop
      ? `<a:srcRect l="${Math.round(crop.x * 100_000)}" t="${Math.round(crop.y * 100_000)}" r="${Math.round((1 - crop.x - crop.width) * 100_000)}" b="${Math.round((1 - crop.y - crop.height) * 100_000)}"/>`
      : "";
    const alpha = Math.max(0, Math.min(100_000, Math.round(opacity * 100_000)));
    const blip = alpha === 100_000
      ? `<a:blip r:embed="${mapping.assetRelationshipId}"/>`
      : `<a:blip r:embed="${mapping.assetRelationshipId}"><a:alphaModFix amt="${alpha}"/></a:blip>`;
    return `<p:pic><p:nvPicPr><p:cNvPr id="${mapping.shapeId}" name="${xmlEscape(element.id)}" descr="${xmlEscape(description)}"/><p:cNvPicPr><a:picLocks noChangeAspect="1"${element.locked ? ' noMove="1" noResize="1"' : ""}/></p:cNvPicPr><p:nvPr/></p:nvPicPr><p:blipFill>${blip}${srcRect}<a:stretch><a:fillRect/></a:stretch></p:blipFill><p:spPr>${semanticXfrmXml(element)}<a:prstGeom prst="rect"><a:avLst/></a:prstGeom></p:spPr></p:pic>`;
  }
  const placeholderTypeValue = element.type === "pageNumber"
    ? "sldNum"
    : element.type === "placeholder"
      ? element.content.placeholderRole === "content" || element.content.placeholderRole === "image" ? "obj" : element.content.placeholderRole
      : null;
  const placeholder = placeholderTypeValue
    ? `<p:ph type="${placeholderTypeValue}" idx="${element.content.placeholderIndex ?? mapping.shapeId}"/>`
    : "";
  const textBox = element.type === "text" || element.type === "pageNumber" || element.type === "placeholder";
  const nonVisual = `<p:nvSpPr><p:cNvPr id="${mapping.shapeId}" name="${xmlEscape(element.id)}" descr="${xmlEscape(description)}"/><p:cNvSpPr${textBox ? ' txBox="1"' : ""}>${locks}</p:cNvSpPr><p:nvPr>${placeholder}</p:nvPr></p:nvSpPr>`;
  const geometry = element.type === "line" ? "line" : element.style.shape === "ellipse" ? "ellipse" : "rect";
  const fill = element.type === "line" || textBox ? "<a:noFill/>" : semanticSolidFill(element.style.fill, opacity);
  const stroke = element.style.stroke;
  const line = stroke
    ? `<a:ln w="${Math.max(1, asEmu(element.style.strokeWidth ?? 1))}">${semanticSolidFill(stroke, opacity)}</a:ln>`
    : "<a:ln><a:noFill/></a:ln>";
  let textBody = "";
  if (textBox) {
    const font = element.style.fontFamily ?? primaryFont;
    const size = Math.round((element.style.fontSize ?? 18) * 100);
    const weight = (element.style.fontWeight ?? 400) >= 600 ? ' b="1"' : "";
    const color = element.style.color ?? "#000000";
    const rPr = `<a:rPr lang="ko-KR" sz="${size}"${weight}>${semanticSolidFill(color, opacity)}<a:latin typeface="${xmlEscape(font)}"/><a:ea typeface="${xmlEscape(font)}"/><a:cs typeface="${xmlEscape(font)}"/></a:rPr>`;
    const run = element.type === "pageNumber"
      ? `<a:fld id="{00000000-0000-0000-0000-${String(mapping.shapeId).padStart(12, "0")}}" type="slidenum">${rPr}<a:t>1</a:t></a:fld>`
      : element.type === "placeholder" ? "" : `<a:r>${rPr}<a:t>${xmlEscape(element.content.text ?? "")}</a:t></a:r>`;
    textBody = `<p:txBody><a:bodyPr wrap="square"/><a:lstStyle/><a:p>${run}<a:endParaRPr lang="ko-KR"/></a:p></p:txBody>`;
  }
  return `<p:sp>${nonVisual}<p:spPr>${semanticXfrmXml(element)}<a:prstGeom prst="${geometry}"><a:avLst/></a:prstGeom>${fill}${line}</p:spPr>${textBody}</p:sp>`;
}

function injectBefore(xml: string, marker: string, addition: string): string {
  const index = xml.indexOf(marker);
  if (index < 0) throw new Error(`OOXML insertion marker is missing: ${marker}`);
  return `${xml.slice(0, index)}${addition}${xml.slice(index)}`;
}

function semanticElementMetadataXml(item: SemanticElementExportMapping): string {
  const optional = [
    item.opacity === undefined ? "" : ` opacity="${item.opacity}"`,
    item.sharedGroupId === undefined ? "" : ` sharedGroupId="${xmlEscape(item.sharedGroupId)}"`,
    item.sharedOverride === undefined ? "" : ` sharedOverride="${item.sharedOverride}"`,
  ].join("");
  return `<avm:element id="${xmlEscape(item.id)}" type="${item.type}" scope="${item.scope}" layoutId="${xmlEscape(item.layoutId ?? "")}" shapeId="${item.shapeId}" zIndex="${item.zIndex}" rotation="${item.rotation}" visibility="${item.visibility}" assetId="${xmlEscape(item.assetId ?? "")}" assetSha256="${item.assetSha256 ?? ""}" mediaType="${item.mediaType ?? ""}" assetPartName="${xmlEscape(item.assetPartName ?? "")}" assetRelationshipId="${xmlEscape(item.assetRelationshipId ?? "")}" fit="${item.fit ?? ""}" crop="${item.crop ? `${item.crop.x},${item.crop.y},${item.crop.width},${item.crop.height}` : ""}"${optional}/>`;
}

/**
 * Compiles the canonical authored manifest without persisting any Konva state.
 * Legacy fixed furniture/layouts are compiled first, then optional WYSIWYG
 * semantic elements are emitted as native master/layout shapes.
 */
export function compileAuthoredDeckMasterParts(
  contract: AuthoredDeckMasterContract,
  assets: readonly DeckMasterBinaryAsset[] = [],
): CompiledDeckMasterParts {
  const normalized = authoredToCompilerContract(contract);
  const legacyAssetIds = new Set(normalized.furniture.flatMap((item) => item.assetId ? [item.assetId] : []));
  const semanticImageIds = new Set(
    (contract.semanticElements ?? []).flatMap((element) =>
      element.type === "image" && element.content.assetId ? [element.content.assetId] : [],
    ),
  );
  const allowedAssetIds = new Set([...legacyAssetIds, ...semanticImageIds]);
  if (assets.some((asset) => !allowedAssetIds.has(asset.assetId))) {
    throw new Error("Deck master binary assets contain unused asset IDs");
  }
  const assetsById = new Map<string, DeckMasterBinaryAsset>();
  for (const asset of assets) {
    if (assetsById.has(asset.assetId)) {
      throw new Error("Deck master binary assets contain duplicate asset IDs");
    }
    assetsById.set(asset.assetId, asset);
  }
  const base = compileNormalized(normalized, assets.filter((asset) => legacyAssetIds.has(asset.assetId)));
  const parts = new Map(base.parts);
  const overrides = [...base.contentTypeOverrides];
  const mediaPartByAsset = new Map<string, string>(
    base.semanticMetadata.furniture.flatMap((item) =>
      item.assetId && item.assetSha256 && item.assetPartName
        ? [[`${item.assetId}\u0000${item.assetSha256}`, item.assetPartName] as const]
        : [],
    ),
  );
  const layoutPartById = new Map(base.semanticMetadata.layouts.map((layout) => [layout.id, layout.partName]));
  const mappings: SemanticElementExportMapping[] = [];
  const masterShapeXml: string[] = [];
  const layoutShapeXml = new Map<string, string[]>();
  const masterRelationships: string[] = [];
  const layoutRelationships = new Map<string, string[]>();

  const ordered = [...(contract.semanticElements ?? [])].sort((left, right) => left.zIndex - right.zIndex || left.id.localeCompare(right.id, "en"));
  ordered.forEach((element, index) => {
    const shapeId = 10_000 + index;
    const asset = element.type === "image" && element.content.assetId
      ? assetsById.get(element.content.assetId)
      : undefined;
    if (element.type === "image") {
      const expectedDigest = element.content.digest?.slice("sha256:".length);
      if (!asset || !expectedDigest || asset.sha256 !== expectedDigest || asset.mediaType !== element.content.mediaType) {
        throw new Error(`Semantic image ${element.id} requires matching digest-pinned asset bytes`);
      }
      const bytes = Buffer.from(asset.bytesBase64, "base64");
      if (bytes.length === 0 || bytes.length > 10_000_000 || createHash("sha256").update(bytes).digest("hex") !== asset.sha256) {
        throw new Error(`Semantic image ${element.id} bytes do not match its digest`);
      }
    }
    const extension = asset ? mediaExtension(asset.mediaType) : null;
    const assetKey = asset ? `${asset.assetId}\u0000${asset.sha256}` : null;
    const partName = asset && assetKey
      ? mediaPartByAsset.get(assetKey) ?? `ppt/media/asset-${semanticAssetRole(asset.assetId)}-${asset.sha256.slice(0, 16)}.${extension}`
      : null;
    if (assetKey && partName) mediaPartByAsset.set(assetKey, partName);
    const relationshipId = asset ? `rIdSemantic${index + 1}` : null;
    const mapping: SemanticElementExportMapping = {
      id: element.id,
      type: element.type,
      scope: element.scope.kind,
      layoutId: element.scope.kind === "layout" ? element.scope.layoutId : null,
      shapeId,
      zIndex: element.zIndex,
      rotation: element.rotation,
      visibility: element.visibility,
      assetId: element.content.assetId ?? null,
      assetSha256: asset?.sha256 ?? null,
      mediaType: asset?.mediaType ?? null,
      assetPartName: partName,
      assetRelationshipId: relationshipId,
      fit: element.content.fit ?? null,
      crop: element.content.crop ? { ...element.content.crop } : null,
      opacity: element.style.opacity ?? 1,
      ...(element.content.sharedGroupId === undefined ? {} : { sharedGroupId: element.content.sharedGroupId }),
      ...(element.content.sharedOverride === undefined ? {} : { sharedOverride: element.content.sharedOverride }),
    };
    mappings.push(mapping);
    if (element.visibility === "hidden") return;
    const targetLayoutIds = element.scope.kind === "layout"
      ? [element.scope.layoutId]
      : element.visibility === "coverOnly"
        ? [contract.layouts.cover.id]
        : element.visibility === "exceptCover" || element.visibility === "layoutOnly"
          ? [...Object.values(contract.layouts), ...(contract.customLayouts ?? [])].filter((layout) => layout.id !== contract.layouts.cover.id).map((layout) => layout.id)
          : [];
    const xml = semanticElementXml(element, mapping, contract.fontManifest.primary);
    if (element.scope.kind === "common" && targetLayoutIds.length === 0) {
      masterShapeXml.push(xml);
      if (asset && partName && relationshipId) {
        masterRelationships.push(`<Relationship Id="${relationshipId}" Type="${NS.r}/image" Target="../media/${path.posix.basename(partName)}"/>`);
      }
    } else {
      for (const layoutId of targetLayoutIds) {
        const part = layoutPartById.get(layoutId);
        if (!part) throw new Error(`Semantic element ${element.id} references unknown layout ${layoutId}`);
        const relationship = asset ? `${relationshipId}-${deckMasterRole(layoutId)}` : null;
        const perLayoutMapping = relationship ? { ...mapping, assetRelationshipId: relationship } : mapping;
        const perLayoutXml = semanticElementXml(element, perLayoutMapping, contract.fontManifest.primary);
        layoutShapeXml.set(part, [...(layoutShapeXml.get(part) ?? []), perLayoutXml]);
        if (asset && partName && relationship) {
          const relsPart = `${path.posix.dirname(part)}/_rels/${path.posix.basename(part)}.rels`;
          layoutRelationships.set(relsPart, [
            ...(layoutRelationships.get(relsPart) ?? []),
            `<Relationship Id="${relationship}" Type="${NS.r}/image" Target="../media/${path.posix.basename(partName)}"/>`,
          ]);
        }
      }
    }
    if (asset && partName && !parts.has(partName)) {
      parts.set(partName, Buffer.from(asset.bytesBase64, "base64"));
      if (!overrides.some((item) => item.partName === `/${partName}`)) {
        overrides.push({ partName: `/${partName}`, contentType: asset.mediaType });
      }
    }
  });

  const semanticMetadata: DeckMasterSemanticMetadata = {
    ...base.semanticMetadata,
    semanticElements: mappings,
    semanticDigest: digest({ ...base.semanticMetadata, semanticElements: mappings, semanticDigest: undefined }),
  };
  let masterXml = parts.get(MASTER_PART)!.toString("utf8");
  masterXml = masterXml.replace(
    `semanticDigest="${base.semanticMetadata.semanticDigest}"`,
    `semanticDigest="${semanticMetadata.semanticDigest}"`,
  );
  masterXml = injectBefore(masterXml, "</p:spTree>", masterShapeXml.join(""));
  const semanticMetadataXml = `<avm:semanticElements>${mappings.map(semanticElementMetadataXml).join("")}</avm:semanticElements>`;
  masterXml = injectBefore(masterXml, "</avm:deckMaster>", semanticMetadataXml);
  parts.set(MASTER_PART, Buffer.from(masterXml, "utf8"));
  if (masterRelationships.length) {
    const xml = parts.get(MASTER_RELS_PART)!.toString("utf8");
    parts.set(MASTER_RELS_PART, Buffer.from(injectBefore(xml, "</Relationships>", masterRelationships.join("")), "utf8"));
  }
  for (const [partName, shapes] of layoutShapeXml) {
    parts.set(partName, Buffer.from(injectBefore(parts.get(partName)!.toString("utf8"), "</p:spTree>", shapes.join("")), "utf8"));
  }
  for (const [partName, relationships] of layoutRelationships) {
    parts.set(partName, Buffer.from(injectBefore(parts.get(partName)!.toString("utf8"), "</Relationships>", relationships.join("")), "utf8"));
  }
  const contentTypeOverridesXml = Buffer.from(
    overrides.map((item) => `<Override PartName="${item.partName}" ContentType="${item.contentType}"/>`).join(""),
    "utf8",
  );
  return { parts, contentTypeOverrides: overrides, contentTypeOverridesXml, semanticMetadata };
}

export {
  assertDeckMasterReadBack,
  createDeckMasterPartCompiler,
  readDeckMasterParts,
} from "./master-readback.ts";
