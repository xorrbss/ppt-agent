import { createHash } from "node:crypto";

import {
  readPptxArchive,
  relationshipTargetPath,
  writePptxArchive,
  xmlAttributeValue,
} from "./pptx-archive.ts";

const RELATIONSHIPS_NS =
  "http://schemas.openxmlformats.org/officeDocument/2006/relationships";
const FONT_RELATIONSHIP = `${RELATIONSHIPS_NS}/font`;
const FONT_CONTENT_TYPE = "application/x-fontdata";

import {
  createPowerPointFontData,
  normalizePowerPointCharset,
} from "./pptx-font-data.ts";

export type PowerPointEmbeddedFontFace =
  | "regular"
  | "bold"
  | "italic"
  | "boldItalic";

export interface PowerPointFontFaceInput {
  readonly data: Buffer;
  /** True only when the supplied SFNT has already been subsetted upstream. */
  readonly subset?: boolean;
  readonly source?: string;
}

export interface PowerPointTypefaceInput {
  readonly typeface: string;
  readonly pitchFamily?: number;
  /**
   * Windows LOGFONT charset byte. Values 128-255 are accepted for callers
   * that use the unsigned Win32 constants and are serialized as the
   * equivalent signed OOXML byte (for example, HANGEUL_CHARSET 129 -> -127).
   */
  readonly charset?: number;
  readonly faces: Readonly<
    Partial<Record<PowerPointEmbeddedFontFace, PowerPointFontFaceInput>>
  >;
}

export interface EmbeddedPowerPointFontFace {
  readonly face: PowerPointEmbeddedFontFace;
  readonly relationshipId: string;
  readonly partName: string;
  readonly source?: string;
  readonly sourceSha256: string;
  readonly sourceBytes: number;
  readonly embeddedBytes: number;
  readonly subset: boolean;
  readonly fsType: number;
  readonly weight: number;
  readonly italic: boolean;
  readonly format: "eot-uncompressed-xor";
}

export interface EmbeddedPowerPointTypeface {
  readonly typeface: string;
  readonly pitchFamily: number;
  readonly charset: number;
  readonly faces: readonly EmbeddedPowerPointFontFace[];
}

export interface PowerPointFontEmbeddingResult {
  readonly requested: true;
  readonly applied: boolean;
  readonly embeddedFontFiles: number;
  readonly embeddedTypefaces: number;
  readonly fonts: readonly EmbeddedPowerPointTypeface[];
  readonly reason?: string;
}

export interface EmbeddedPowerPointPptx {
  readonly pptx: Buffer;
  readonly result: PowerPointFontEmbeddingResult;
}


const FACE_ORDER = Object.freeze([
  "regular",
  "bold",
  "italic",
  "boldItalic",
] as const);

function xmlEscapeAttribute(value: string): string {
  return value
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll('"', "&quot;")
    .replaceAll("\r", "&#xD;")
    .replaceAll("\n", "&#xA;");
}


function nextRelationshipNumber(xml: string): number {
  let result = 1;
  const seen = new Set<string>();
  for (const match of xml.matchAll(/<(?:[A-Za-z_][\w.-]*:)?Relationship\b[^>]*>/gi)) {
    const id = xmlAttributeValue(match[0], "Id");
    if (!id || seen.has(id)) throw new Error("PPTX presentation relationship Id is missing or duplicated.");
    seen.add(id);
    const numeric = id.match(/^rId(\d+)$/i)?.[1];
    if (numeric === undefined) continue;
    const value = Number(numeric);
    if (!Number.isSafeInteger(value)) throw new Error("PPTX presentation relationship Id is out of range.");
    result = Math.max(result, value + 1);
  }
  return result;
}

function appendRelationship(
  xml: string,
  id: string,
  target: string
): string {
  const relationship =
    `<Relationship Id="${id}" Type="${FONT_RELATIONSHIP}" ` +
    `Target="${xmlEscapeAttribute(target)}"/>`;
  if (/<\/(?:\w+:)?Relationships\s*>/i.test(xml)) {
    return xml.replace(
      /<\/(?:\w+:)?Relationships\s*>/i,
      (closing) => `${relationship}${closing}`
    );
  }
  if (/<(?:\w+:)?Relationships\b[^>]*\/\s*>/i.test(xml)) {
    return xml.replace(
      /<((?:\w+:)?Relationships)\b([^>]*)\/\s*>/i,
      `<$1$2>${relationship}</$1>`
    );
  }
  throw new Error("PPTX presentation relationships are malformed.");
}

function ensureFontContentType(xml: string): string {
  if (
    /<Default\b[^>]*\bExtension\s*=\s*["']fntdata["'][^>]*>/i.test(xml)
  ) {
    if (
      !/<Default\b[^>]*\bExtension\s*=\s*["']fntdata["'][^>]*\bContentType\s*=\s*["']application\/x-fontdata["'][^>]*>/i.test(
        xml
      ) &&
      !/<Default\b[^>]*\bContentType\s*=\s*["']application\/x-fontdata["'][^>]*\bExtension\s*=\s*["']fntdata["'][^>]*>/i.test(
        xml
      )
    ) {
      throw new Error("PPTX declares an incompatible fntdata content type.");
    }
    return xml;
  }
  const declaration = `<Default Extension="fntdata" ContentType="${FONT_CONTENT_TYPE}"/>`;
  if (/<\/(?:\w+:)?Types\s*>/i.test(xml)) {
    return xml.replace(/<\/(?:\w+:)?Types\s*>/i, (closing) => `${declaration}${closing}`);
  }
  if (/<(?:\w+:)?Types\b[^>]*\/\s*>/i.test(xml)) {
    return xml.replace(
      /<((?:\w+:)?Types)\b([^>]*)\/\s*>/i,
      `<$1$2>${declaration}</$1>`
    );
  }
  throw new Error("PPTX content types are malformed.");
}

function ensureRelationshipNamespace(xml: string): string {
  const opening = xml.match(/<p:presentation\b[^>]*>/i)?.[0];
  if (!opening) throw new Error("PPTX presentation.xml is malformed.");
  const rBinding = opening.match(/\bxmlns:r\s*=\s*(["'])(.*?)\1/i)?.[2];
  if (rBinding && rBinding !== RELATIONSHIPS_NS) {
    throw new Error("PPTX presentation.xml binds the r prefix incompatibly.");
  }
  if (rBinding) return xml;
  return xml.replace(
    opening,
    opening.replace(/>$/, ` xmlns:r="${RELATIONSHIPS_NS}">`)
  );
}

function ensureEmbedTrueTypeFonts(xml: string): string {
  const opening = xml.match(/<p:presentation\b[^>]*>/i)?.[0];
  if (!opening) throw new Error("PPTX presentation.xml is malformed.");
  if (/\bembedTrueTypeFonts\s*=/i.test(opening)) {
    return xml.replace(
      opening,
      opening.replace(
        /\bembedTrueTypeFonts\s*=\s*(["'])(?:true|false|0|1)\1/i,
        'embedTrueTypeFonts="1"'
      )
    );
  }
  return xml.replace(
    opening,
    opening.replace(/>$/, ' embedTrueTypeFonts="1">')
  );
}

function insertEmbeddedFontList(xml: string, list: string): string {
  if (/<p:embeddedFontLst\b/i.test(xml)) {
    throw new Error("Replacing an existing PowerPoint embedded-font list is unsupported.");
  }
  const orderedSuccessors = [
    "custShowLst",
    "photoAlbum",
    "custDataLst",
    "kinsoku",
    "defaultTextStyle",
    "modifyVerifier",
    "extLst",
  ];
  for (const successor of orderedSuccessors) {
    const expression = new RegExp(`<p:${successor}\\b`, "i");
    const match = expression.exec(xml);
    if (match) return `${xml.slice(0, match.index)}${list}${xml.slice(match.index)}`;
  }
  const closing = /<\/p:presentation\s*>/i.exec(xml);
  if (!closing) throw new Error("PPTX presentation.xml has no closing element.");
  return `${xml.slice(0, closing.index)}${list}${xml.slice(closing.index)}`;
}

function allocateFontPartName(
  entries: ReadonlyMap<string, Buffer>,
  start: number
): { readonly name: string; readonly next: number } {
  let number = start;
  while (entries.has(`ppt/fonts/font${number}.fntdata`)) number += 1;
  return { name: `ppt/fonts/font${number}.fntdata`, next: number + 1 };
}

function fontNameKey(value: string): string {
  return value.normalize("NFKC").replace(/[\s_-]+/g, " ").trim().toLowerCase();
}

function assertEmbeddedFontPackage(
  entries: ReadonlyMap<string, Buffer>,
  fonts: readonly EmbeddedPowerPointTypeface[]
): void {
  const presentation = entries.get("ppt/presentation.xml")?.toString("utf8") ?? "";
  const relationships = entries.get("ppt/_rels/presentation.xml.rels")?.toString("utf8") ?? "";
  const contentTypes = entries.get("[Content_Types].xml")?.toString("utf8") ?? "";
  const list = presentation.match(/<p:embeddedFontLst\b[^>]*>[\s\S]*?<\/p:embeddedFontLst>/i)?.[0];
  const declarations = list
    ? [...list.matchAll(/<p:embeddedFont\b[^>]*>([\s\S]*?)<\/p:embeddedFont>/gi)]
    : [];
  if (declarations.length !== fonts.length) {
    throw new Error("PPTX embedded-font declarations do not match the packaged typefaces.");
  }
  const expectedFaces = new Map<string, EmbeddedPowerPointFontFace>();
  declarations.forEach((declaration, index) => {
    const fontTag = declaration[1].match(/<p:font\b[^>]*\/>/i)?.[0];
    if (!fontTag ||
        xmlAttributeValue(fontTag, "typeface") !== fonts[index].typeface ||
        Number(xmlAttributeValue(fontTag, "pitchFamily")) !== fonts[index].pitchFamily ||
        Number(xmlAttributeValue(fontTag, "charset")) !== fonts[index].charset) {
      throw new Error("PPTX embedded-font declaration typeface is inconsistent.");
    }
    const declaredFaces = [...declaration[1].matchAll(
      /<p:(regular|bold|italic|boldItalic)\b[^>]*\/>/gi
    )];
    if (declaredFaces.length !== fonts[index].faces.length) {
      throw new Error(`PPTX embedded ${fonts[index].typeface} face declarations are incomplete.`);
    }
    declaredFaces.forEach((match, faceIndex) => {
      const expected = fonts[index].faces[faceIndex];
      const relationshipId = xmlAttributeValue(match[0], "r:id");
      if (match[1] !== expected.face || relationshipId !== expected.relationshipId || expectedFaces.has(expected.relationshipId)) {
        throw new Error(`PPTX embedded ${fonts[index].typeface} face declaration is inconsistent.`);
      }
      expectedFaces.set(expected.relationshipId, expected);
    });
  });

  const relationshipFaces = new Map<string, string>();
  for (const match of relationships.matchAll(/<(?:[A-Za-z_][\w.-]*:)?Relationship\b[^>]*>/gi)) {
    if (xmlAttributeValue(match[0], "Type") !== FONT_RELATIONSHIP) continue;
    const id = xmlAttributeValue(match[0], "Id");
    const target = xmlAttributeValue(match[0], "Target");
    const targetPath = target ? relationshipTargetPath("ppt/_rels/presentation.xml.rels", target) : null;
    if (!id || !targetPath || relationshipFaces.has(id)) {
      throw new Error("PPTX embedded-font relationship is malformed or duplicated.");
    }
    relationshipFaces.set(id, targetPath);
  }
  if (relationshipFaces.size !== expectedFaces.size) {
    throw new Error("PPTX embedded-font relationships do not match the face declarations.");
  }
  for (const [id, face] of expectedFaces) {
    if (relationshipFaces.get(id) !== face.partName || !entries.get(face.partName)?.length) {
      throw new Error(`PPTX embedded-font part is missing or misrelated: ${face.partName}.`);
    }
  }
  const fontDefaults = [...contentTypes.matchAll(/<Default\b[^>]*>/gi)].filter(
    (match) => xmlAttributeValue(match[0], "Extension")?.toLowerCase() === "fntdata"
  );
  if (fontDefaults.length !== 1 || xmlAttributeValue(fontDefaults[0][0], "ContentType") !== FONT_CONTENT_TYPE) {
    throw new Error("PPTX embedded-font content type is missing or inconsistent.");
  }
}

/**
 * Add real PresentationML embedded-font declarations and EOT font parts.
 *
 * Font discovery, allowlisting, and provenance checks belong at the server-only
 * caller boundary. This function independently validates the binary SFNT and
 * embedding-license bits before mutating the package.
 */
export function embedPowerPointFonts(
  pptxBytes: Buffer,
  requestedFonts: readonly PowerPointTypefaceInput[]
): EmbeddedPowerPointPptx {
  if (requestedFonts.length === 0) {
    throw new Error("PowerPoint font embedding was requested without any embeddable faces.");
  }
  const entries = readPptxArchive(pptxBytes);
  const presentationPath = "ppt/presentation.xml";
  const relationshipsPath = "ppt/_rels/presentation.xml.rels";
  const contentTypesPath = "[Content_Types].xml";
  let presentation = entries.get(presentationPath)?.toString("utf8");
  let relationships = entries.get(relationshipsPath)?.toString("utf8");
  let contentTypes = entries.get(contentTypesPath)?.toString("utf8");
  if (!presentation || !relationships || !contentTypes) {
    throw new Error("PPTX is missing presentation, relationship, or content-type parts.");
  }
  if (
    new RegExp(`\\bType\\s*=\\s*["']${FONT_RELATIONSHIP.replaceAll("/", "\\/")}["']`, "i").test(
      relationships
    )
  ) {
    throw new Error("PPTX already contains embedded font relationships.");
  }
  presentation = ensureRelationshipNamespace(presentation);
  contentTypes = ensureFontContentType(contentTypes);

  const seenTypefaces = new Set<string>();
  let nextRelationship = nextRelationshipNumber(relationships);
  let nextPart = 1;
  const embeddedFonts: EmbeddedPowerPointTypeface[] = [];
  const declarations: string[] = [];
  for (const requested of requestedFonts) {
    const typeface = requested.typeface.trim();
    if (!typeface || typeface.length > 127 || /[\0\r\n]/.test(typeface)) {
      throw new Error("Embedded PowerPoint typeface is invalid.");
    }
    const typefaceKey = typeface.toLocaleLowerCase("en-US");
    if (seenTypefaces.has(typefaceKey)) {
      throw new Error(`Embedded PowerPoint typeface is duplicated: ${typeface}.`);
    }
    seenTypefaces.add(typefaceKey);
    const pitchFamily = requested.pitchFamily ?? 0;
    const charset = normalizePowerPointCharset(requested.charset ?? 1);
    if (
      !Number.isInteger(pitchFamily) ||
      pitchFamily < 0 ||
      pitchFamily > 255
    ) {
      throw new Error("Embedded PowerPoint font pitchFamily is invalid.");
    }
    const faceResults: EmbeddedPowerPointFontFace[] = [];
    const faceDeclarations: string[] = [];
    for (const face of FACE_ORDER) {
      const input = requested.faces[face];
      if (!input) continue;
      const wrapped = createPowerPointFontData(input.data, {
        subset: input.subset,
        charset: charset.eot,
      });
      const expectedBold = face === "bold" || face === "boldItalic";
      const expectedItalic = face === "italic" || face === "boldItalic";
      if (fontNameKey(wrapped.metadata.family) !== fontNameKey(typeface)) {
        throw new Error(
          `Embedded ${typeface} ${face} face declares the SFNT family ${wrapped.metadata.family}.`
        );
      }
      if (expectedBold !== (wrapped.metadata.weight >= 700)) {
        throw new Error(`Embedded ${typeface} ${face} face has incompatible weight metadata.`);
      }
      if (expectedItalic !== wrapped.metadata.italic) {
        throw new Error(`Embedded ${typeface} ${face} face has incompatible italic metadata.`);
      }
      const allocated = allocateFontPartName(entries, nextPart);
      nextPart = allocated.next;
      const relationshipId = `rId${nextRelationship}`;
      nextRelationship += 1;
      entries.set(allocated.name, wrapped.data);
      relationships = appendRelationship(
        relationships,
        relationshipId,
        allocated.name.slice("ppt/".length)
      );
      faceDeclarations.push(`<p:${face} r:id="${relationshipId}"/>`);
      faceResults.push({
        face,
        relationshipId,
        partName: allocated.name,
        source: input.source,
        sourceSha256: createHash("sha256").update(input.data).digest("hex"),
        sourceBytes: input.data.length,
        embeddedBytes: wrapped.data.length,
        subset: Boolean(input.subset),
        fsType: wrapped.metadata.fsType,
        weight: wrapped.metadata.weight,
        italic: wrapped.metadata.italic,
        format: "eot-uncompressed-xor",
      });
    }
    if (!faceResults.length) {
      throw new Error(`Embedded PowerPoint typeface has no faces: ${typeface}.`);
    }
    declarations.push(
      `<p:embeddedFont><p:font typeface="${xmlEscapeAttribute(typeface)}" ` +
        `pitchFamily="${pitchFamily}" charset="${charset.presentation}"/>${faceDeclarations.join("")}` +
        `</p:embeddedFont>`
    );
    embeddedFonts.push({
      typeface,
      pitchFamily,
      charset: charset.presentation,
      faces: faceResults,
    });
  }

  presentation = ensureEmbedTrueTypeFonts(presentation);
  presentation = insertEmbeddedFontList(
    presentation,
    `<p:embeddedFontLst>${declarations.join("")}</p:embeddedFontLst>`
  );
  entries.set(presentationPath, Buffer.from(presentation, "utf8"));
  entries.set(relationshipsPath, Buffer.from(relationships, "utf8"));
  entries.set(contentTypesPath, Buffer.from(contentTypes, "utf8"));
  const output = writePptxArchive(entries);
  // Re-read our output so truncated or internally inconsistent packages fail
  // before the caller can label embedding as applied.
  const verifiedEntries = readPptxArchive(output);
  const embeddedFontFiles = embeddedFonts.reduce(
    (sum, item) => sum + item.faces.length,
    0
  );
  assertEmbeddedFontPackage(verifiedEntries, embeddedFonts);
  return {
    pptx: output,
    result: {
      requested: true,
      applied: embeddedFontFiles > 0,
      embeddedFontFiles,
      embeddedTypefaces: embeddedFonts.length,
      fonts: embeddedFonts,
    },
  };
}


