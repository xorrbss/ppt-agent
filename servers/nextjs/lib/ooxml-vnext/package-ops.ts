const XML_HEADER = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>';

const REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships";
const PRESENTATION_NS =
  "http://schemas.openxmlformats.org/presentationml/2006/main";
const DRAWING_NS = "http://schemas.openxmlformats.org/drawingml/2006/main";
const OFFICE_REL_NS =
  "http://schemas.openxmlformats.org/officeDocument/2006/relationships";

export const OOXML_RELATIONSHIP_TYPES = Object.freeze({
  officeDocument: `${OFFICE_REL_NS}/officeDocument`,
  master: `${OFFICE_REL_NS}/slideMaster`,
  layout: `${OFFICE_REL_NS}/slideLayout`,
  theme: `${OFFICE_REL_NS}/theme`,
  slide: `${OFFICE_REL_NS}/slide`,
});

export const OOXML_CONTENT_TYPES = Object.freeze({
  presentation:
    "application/vnd.openxmlformats-officedocument.presentationml.presentation.main+xml",
  master:
    "application/vnd.openxmlformats-officedocument.presentationml.slideMaster+xml",
  layout:
    "application/vnd.openxmlformats-officedocument.presentationml.slideLayout+xml",
  slide:
    "application/vnd.openxmlformats-officedocument.presentationml.slide+xml",
  theme: "application/vnd.openxmlformats-officedocument.theme+xml",
});

export interface RelationshipAddition {
  id?: string;
  type: string;
  target: string;
  targetMode?: "External";
}

export function xmlEscape(value: string): string {
  return value
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&apos;");
}

export function xmlUnescape(value: string): string {
  return value
    .replaceAll("&lt;", "<")
    .replaceAll("&gt;", ">")
    .replaceAll("&quot;", '"')
    .replaceAll("&apos;", "'")
    .replaceAll("&amp;", "&");
}

function requireXml(entries: ReadonlyMap<string, Buffer>, partName: string): string {
  const value = entries.get(partName);
  if (!value) throw new Error(`Required OOXML part is missing: ${partName}`);
  return value.toString("utf8");
}

export function nextRelationshipId(xml: string): string {
  let maximum = 0;
  for (const match of xml.matchAll(/\bId="rId(\d+)"/g)) {
    maximum = Math.max(maximum, Number(match[1]));
  }
  return `rId${maximum + 1}`;
}

export function addRelationship(
  entries: Map<string, Buffer>,
  relsPartName: string,
  relationship: RelationshipAddition,
): string {
  const xml = requireXml(entries, relsPartName);
  const id = relationship.id ?? nextRelationshipId(xml);
  if (new RegExp(`\\bId="${id.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}"`).test(xml)) {
    throw new Error(`Relationship id already exists in ${relsPartName}: ${id}`);
  }
  const element = `<Relationship Id="${xmlEscape(id)}" Type="${xmlEscape(relationship.type)}" Target="${xmlEscape(relationship.target)}"${relationship.targetMode ? ` TargetMode="${relationship.targetMode}"` : ""}/>`;
  if (!/<\/Relationships>\s*$/i.test(xml)) {
    throw new Error(`Malformed relationships part: ${relsPartName}`);
  }
  entries.set(
    relsPartName,
    Buffer.from(xml.replace(/<\/Relationships>\s*$/i, `${element}</Relationships>`), "utf8"),
  );
  return id;
}

export function removeRelationship(
  entries: Map<string, Buffer>,
  relsPartName: string,
  relationshipId: string,
): void {
  const xml = requireXml(entries, relsPartName);
  const escaped = relationshipId.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  const pattern = new RegExp(
    `<Relationship\\b(?=[^>]*\\bId="${escaped}")[^>]*/>`,
    "g",
  );
  const updated = xml.replace(pattern, "");
  if (updated === xml) {
    throw new Error(`Relationship was not found in ${relsPartName}: ${relationshipId}`);
  }
  entries.set(relsPartName, Buffer.from(updated, "utf8"));
}

export function ensureContentTypeOverride(
  entries: Map<string, Buffer>,
  partName: string,
  contentType: string,
): void {
  const xml = requireXml(entries, "[Content_Types].xml");
  const normalized = partName.startsWith("/") ? partName : `/${partName}`;
  const existing = [...xml.matchAll(/<Override\b[^>]*\/>/g)].find((match) =>
    new RegExp(`\\bPartName="${normalized.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}"`).test(
      match[0],
    ),
  );
  if (existing) {
    if (!existing[0].includes(`ContentType="${contentType}"`)) {
      throw new Error(`Conflicting content type for ${normalized}`);
    }
    return;
  }
  if (!/<\/Types>\s*$/i.test(xml)) throw new Error("Malformed [Content_Types].xml");
  const element = `<Override PartName="${xmlEscape(normalized)}" ContentType="${xmlEscape(contentType)}"/>`;
  entries.set(
    "[Content_Types].xml",
    Buffer.from(xml.replace(/<\/Types>\s*$/i, `${element}</Types>`), "utf8"),
  );
}

export function removeContentTypeOverride(
  entries: Map<string, Buffer>,
  partName: string,
): void {
  const xml = requireXml(entries, "[Content_Types].xml");
  const normalized = partName.startsWith("/") ? partName : `/${partName}`;
  const escaped = normalized.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  const pattern = new RegExp(
    `<Override\\b(?=[^>]*\\bPartName="${escaped}")[^>]*/>`,
    "g",
  );
  entries.set("[Content_Types].xml", Buffer.from(xml.replace(pattern, ""), "utf8"));
}

function addPresentationListReference(
  entries: Map<string, Buffer>,
  listName: "sldMasterIdLst" | "sldIdLst",
  elementName: "sldMasterId" | "sldId",
  numericId: number,
  relationshipId: string,
): void {
  const partName = "ppt/presentation.xml";
  const xml = requireXml(entries, partName);
  const element = `<p:${elementName} id="${numericId}" r:id="${xmlEscape(relationshipId)}"/>`;
  const closing = new RegExp(`</p:${listName}>`, "i");
  const empty = new RegExp(`<p:${listName}\\b[^>]*/>`, "i");
  let updated: string;
  if (closing.test(xml)) {
    updated = xml.replace(closing, `${element}</p:${listName}>`);
  } else if (empty.test(xml)) {
    updated = xml.replace(empty, `<p:${listName}>${element}</p:${listName}>`);
  } else {
    const anchor = listName === "sldMasterIdLst" ? /<p:sldIdLst\b/i : /<p:sldSz\b/i;
    const insertion = `<p:${listName}>${element}</p:${listName}>`;
    if (!anchor.test(xml)) throw new Error("Malformed ppt/presentation.xml");
    updated = xml.replace(anchor, `${insertion}$&`);
  }
  entries.set(partName, Buffer.from(updated, "utf8"));
}

export function addPresentationMasterReference(
  entries: Map<string, Buffer>,
  numericId: number,
  relationshipId: string,
): void {
  addPresentationListReference(
    entries,
    "sldMasterIdLst",
    "sldMasterId",
    numericId,
    relationshipId,
  );
}

export function addPresentationSlideReference(
  entries: Map<string, Buffer>,
  numericId: number,
  relationshipId: string,
): void {
  addPresentationListReference(
    entries,
    "sldIdLst",
    "sldId",
    numericId,
    relationshipId,
  );
}

export function removePresentationReference(
  entries: Map<string, Buffer>,
  elementName: "sldMasterId" | "sldId",
  relationshipId: string,
): void {
  const xml = requireXml(entries, "ppt/presentation.xml");
  const escaped = relationshipId.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  const pattern = new RegExp(
    `<p:${elementName}\\b(?=[^>]*\\br:id="${escaped}")[^>]*/>`,
    "g",
  );
  const updated = xml.replace(pattern, "");
  if (updated === xml) throw new Error(`Presentation reference was not found: ${relationshipId}`);
  entries.set("ppt/presentation.xml", Buffer.from(updated, "utf8"));
}

export function nextNumericId(xml: string, elementName: "sldMasterId" | "sldId"): number {
  let maximum = elementName === "sldId" ? 255 : 2_147_483_647;
  const pattern = new RegExp(`<p:${elementName}\\b[^>]*\\bid="(\\d+)"`, "g");
  for (const match of xml.matchAll(pattern)) maximum = Math.max(maximum, Number(match[1]));
  if (!Number.isSafeInteger(maximum + 1)) throw new Error("OOXML numeric id space is exhausted");
  return maximum + 1;
}

export function nextPartNumber(entries: ReadonlyMap<string, Buffer>, pattern: RegExp): number {
  let maximum = 0;
  for (const name of entries.keys()) {
    const match = pattern.exec(name);
    pattern.lastIndex = 0;
    if (match) maximum = Math.max(maximum, Number(match[1]));
  }
  return maximum + 1;
}

export function createEmptyPresentationPackage(): Map<string, Buffer> {
  return new Map<string, Buffer>([
    [
      "[Content_Types].xml",
      Buffer.from(
        `${XML_HEADER}<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/ppt/presentation.xml" ContentType="${OOXML_CONTENT_TYPES.presentation}"/><Override PartName="/docProps/core.xml" ContentType="application/vnd.openxmlformats-package.core-properties+xml"/><Override PartName="/docProps/app.xml" ContentType="application/vnd.openxmlformats-officedocument.extended-properties+xml"/></Types>`,
        "utf8",
      ),
    ],
    [
      "_rels/.rels",
      Buffer.from(
        `${XML_HEADER}<Relationships xmlns="${REL_NS}"><Relationship Id="rId1" Type="${OOXML_RELATIONSHIP_TYPES.officeDocument}" Target="ppt/presentation.xml"/><Relationship Id="rId2" Type="http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties" Target="docProps/core.xml"/><Relationship Id="rId3" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/extended-properties" Target="docProps/app.xml"/></Relationships>`,
        "utf8",
      ),
    ],
    [
      "ppt/presentation.xml",
      Buffer.from(
        `${XML_HEADER}<p:presentation xmlns:a="${DRAWING_NS}" xmlns:r="${OFFICE_REL_NS}" xmlns:p="${PRESENTATION_NS}"><p:sldIdLst/><p:sldSz cx="12192000" cy="6858000" type="screen16x9"/><p:notesSz cx="6858000" cy="9144000"/><p:defaultTextStyle/></p:presentation>`,
        "utf8",
      ),
    ],
    [
      "ppt/_rels/presentation.xml.rels",
      Buffer.from(`${XML_HEADER}<Relationships xmlns="${REL_NS}"></Relationships>`, "utf8"),
    ],
    [
      "docProps/core.xml",
      Buffer.from(
        `${XML_HEADER}<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties" xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:dcterms="http://purl.org/dc/terms/" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"><dc:title>Authored vNext presentation</dc:title><dc:creator>Presenton</dc:creator><dcterms:created xsi:type="dcterms:W3CDTF">2000-01-01T00:00:00Z</dcterms:created><dcterms:modified xsi:type="dcterms:W3CDTF">2000-01-01T00:00:00Z</dcterms:modified></cp:coreProperties>`,
        "utf8",
      ),
    ],
    [
      "docProps/app.xml",
      Buffer.from(
        `${XML_HEADER}<Properties xmlns="http://schemas.openxmlformats.org/officeDocument/2006/extended-properties" xmlns:vt="http://schemas.openxmlformats.org/officeDocument/2006/docPropsVTypes"><Application>Presenton OOXML vNext</Application><PresentationFormat>Widescreen</PresentationFormat><Slides>0</Slides></Properties>`,
        "utf8",
      ),
    ],
  ]);
}

export function setApplicationSlideCount(entries: Map<string, Buffer>, count: number): void {
  const partName = "docProps/app.xml";
  const xml = requireXml(entries, partName);
  const updated = /<Slides>\d+<\/Slides>/.test(xml)
    ? xml.replace(/<Slides>\d+<\/Slides>/, `<Slides>${count}</Slides>`)
    : xml.replace(/<\/Properties>/, `<Slides>${count}</Slides></Properties>`);
  entries.set(partName, Buffer.from(updated, "utf8"));
}

export function cloneEntries(entries: ReadonlyMap<string, Buffer>): Map<string, Buffer> {
  return new Map([...entries].map(([name, bytes]) => [name, Buffer.from(bytes)]));
}
