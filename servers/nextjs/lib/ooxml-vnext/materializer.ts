import { createHash } from "node:crypto";

import { xmlEscape } from "./package-ops.ts";

const XML_HEADER = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>';
const P_NS = "http://schemas.openxmlformats.org/presentationml/2006/main";
const A_NS = "http://schemas.openxmlformats.org/drawingml/2006/main";
const R_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships";
const REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships";
const VNEXT_NS = "urn:presenton:ooxml-vnext:semantic-metadata:v1";
const VNEXT_EXT_URI = "{7D786E45-7874-4D65-7461-646174613A31}";

export interface MaterializedPlaceholderDefinition {
  role: string;
  type: "title" | "body" | "ctrTitle" | "subTitle" | "obj";
  idx: number;
  fontFamily: string;
  fontSizePt: number;
  bold?: boolean;
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
  masterDigest: string;
  contractVersion: string;
}

export interface MaterializedSlideParts {
  slideXml: Buffer;
  relationshipsXml: Buffer;
  semanticMetadata: {
    slideId: string;
    layoutId: string;
    masterDigest: string;
    placeholderRoleDigest: string;
    roles: readonly { role: string; type: string; idx: number }[];
  };
}

function stableDigest(value: unknown): string {
  return createHash("sha256").update(JSON.stringify(value)).digest("hex");
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
  return `<p:sp><p:nvSpPr><p:cNvPr id="${shapeId}" name="placeholder:${role}" descr="vnext:role=${role}"/><p:cNvSpPr txBox="1"/><p:nvPr><p:ph type="${type}" idx="${definition.idx}"/><p:extLst><p:ext uri="${VNEXT_EXT_URI}"><v:placeholder xmlns:v="${VNEXT_NS}" role="${role}" idx="${definition.idx}"/></p:ext></p:extLst></p:nvPr></p:nvSpPr><p:spPr/><p:txBody><a:bodyPr wrap="square"/><a:lstStyle/>${paragraphs}</p:txBody></p:sp>`;
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
  const shapes = request.placeholders
    .slice()
    .sort((left, right) => left.idx - right.idx || left.role.localeCompare(right.role))
    .map((placeholder, index) =>
      textShapeXml(placeholder, request.values[placeholder.role], index + 2),
    )
    .join("");
  const metadata = `<p:extLst><p:ext uri="${VNEXT_EXT_URI}"><v:slide xmlns:v="${VNEXT_NS}" slideId="${xmlEscape(request.slideId)}" layoutId="${xmlEscape(request.layoutId)}" contractVersion="${xmlEscape(request.contractVersion)}" masterDigest="${xmlEscape(request.masterDigest)}" placeholderRoleDigest="${placeholderRoleDigest}"/></p:ext></p:extLst>`;
  const slideXml = `${XML_HEADER}<p:sld xmlns:a="${A_NS}" xmlns:r="${R_NS}" xmlns:p="${P_NS}" xmlns:v="${VNEXT_NS}" showMasterSp="1"><p:cSld name="${xmlEscape(request.slideId)}"><p:spTree><p:nvGrpSpPr><p:cNvPr id="1" name=""/><p:cNvGrpSpPr/><p:nvPr/></p:nvGrpSpPr><p:grpSpPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="0" cy="0"/><a:chOff x="0" y="0"/><a:chExt cx="0" cy="0"/></a:xfrm></p:grpSpPr>${shapes}</p:spTree></p:cSld><p:clrMapOvr><a:masterClrMapping/></p:clrMapOvr>${metadata}</p:sld>`;
  const relationshipsXml = `${XML_HEADER}<Relationships xmlns="${REL_NS}"><Relationship Id="rId1" Type="${R_NS}/slideLayout" Target="${relationshipTarget(request.layoutPartName)}"/></Relationships>`;
  return {
    slideXml: Buffer.from(slideXml, "utf8"),
    relationshipsXml: Buffer.from(relationshipsXml, "utf8"),
    semanticMetadata: {
      slideId: request.slideId,
      layoutId: request.layoutId,
      masterDigest: request.masterDigest,
      placeholderRoleDigest,
      roles,
    },
  };
}

export function readSlideSemanticMetadata(slideXmlBytes: Buffer): MaterializedSlideParts["semanticMetadata"] {
  const xml = slideXmlBytes.toString("utf8");
  const slide = xml.match(/<v:slide\b([^>]*)\/>/);
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
      const type = shape.match(/<p:ph\b[^>]*\btype="([^"]+)"/)?.[1];
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
  return {
    slideId: attributes.slideId,
    layoutId: attributes.layoutId,
    masterDigest: attributes.masterDigest,
    placeholderRoleDigest: attributes.placeholderRoleDigest,
    roles,
  };
}
