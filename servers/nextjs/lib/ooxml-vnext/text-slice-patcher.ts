import { createHash } from "node:crypto";
import type {
  StableTextSelector,
  TextSlicePatchReceipt,
  TextSlicePatchRequest,
  TextSlicePatchResult,
} from "./text-slice-contracts.ts";
export type {
  ExpectedOldText,
  StableTextSelector,
  TextSlicePatchReceipt,
  TextSlicePatchRequest,
  TextSlicePatchResult,
} from "./text-slice-contracts.ts";
const ALLOWED_TEXT_PART =
  /^ppt\/(?:slides\/slide[1-9]\d*|slideLayouts\/slideLayout[1-9]\d*|slideMasters\/slideMaster[1-9]\d*)\.xml$/;
const SHA256 = /^[a-f0-9]{64}$/i;
interface ShapeSlice {
  start: number;
  end: number;
  xml: string;
  masked: string;
}
interface TextNodeSlice {
  openingStart: number;
  contentStart: number;
  contentEnd: number;
  openingTag: string;
  encodedText: string;
  decodedText: string;
}
interface XmlTagToken {
  start: number;
  end: number;
  name: string;
  kind: "open" | "close" | "self";
}
function reject(reason: string): never {
  throw new Error(`ooxml-text-patch-rejected:${reason}`);
}

function sha256(value: Uint8Array | string): string {
  return createHash("sha256").update(value).digest("hex");
}

function assertNonNegativeInteger(value: number | undefined, name: string): void {
  if (value !== undefined && (!Number.isSafeInteger(value) || value < 0)) {
    reject(`${name}-must-be-a-non-negative-integer`);
  }
}

function normalizedElementId(value: number | string): string {
  const text = String(value);
  if (!/^[1-9]\d*$/.test(text)) reject("element-id-must-be-a-positive-integer");
  const parsed = Number(text);
  if (!Number.isSafeInteger(parsed)) reject("element-id-is-out-of-range");
  return text;
}

function assertWellFormedText(value: string, name: string): void {
  for (let index = 0; index < value.length; index += 1) {
    const unit = value.charCodeAt(index);
    if (unit >= 0xd800 && unit <= 0xdbff) {
      const trail = value.charCodeAt(index + 1);
      if (!(trail >= 0xdc00 && trail <= 0xdfff)) {
        reject(`${name}-contains-an-unpaired-surrogate`);
      }
      index += 1;
      continue;
    }
    if (unit >= 0xdc00 && unit <= 0xdfff) {
      reject(`${name}-contains-an-unpaired-surrogate`);
    }
    if (
      unit === 0 ||
      (unit < 0x20 && unit !== 0x09 && unit !== 0x0a && unit !== 0x0d) ||
      unit === 0xfffe ||
      unit === 0xffff
    ) {
      reject(`${name}-contains-an-invalid-xml-character`);
    }
  }
}

function decodeXmlText(value: string, context: string): string {
  if (value.includes("<")) reject(`${context}-contains-markup`);
  let cursor = 0;
  let decoded = "";
  const entityPattern = /&(?:#(?:x[0-9a-fA-F]+|\d+)|[A-Za-z][A-Za-z0-9._-]*);/g;
  for (const match of value.matchAll(entityPattern)) {
    const index = match.index ?? 0;
    const plain = value.slice(cursor, index);
    if (plain.includes("&")) reject(`${context}-contains-a-bare-ampersand`);
    decoded += plain;
    const entity = match[0];
    decoded += (() => {
      if (entity === "&amp;") return "&";
      if (entity === "&lt;") return "<";
      if (entity === "&gt;") return ">";
      if (entity === "&quot;") return '"';
      if (entity === "&apos;") return "'";
      if (entity.startsWith("&#")) {
        const hex = entity[2]?.toLowerCase() === "x";
        const digits = entity.slice(hex ? 3 : 2, -1);
        const point = Number.parseInt(digits, hex ? 16 : 10);
        if (
          !Number.isSafeInteger(point) ||
          point <= 0 ||
          point > 0x10ffff ||
          (point >= 0xd800 && point <= 0xdfff) ||
          (point < 0x20 && point !== 0x09 && point !== 0x0a && point !== 0x0d) ||
          point === 0xfffe ||
          point === 0xffff
        ) {
          reject(`${context}-contains-an-invalid-character-reference`);
        }
        return String.fromCodePoint(point);
      }
      reject(`${context}-contains-an-unsupported-entity`);
    })();
    cursor = index + entity.length;
  }
  const tail = value.slice(cursor);
  if (tail.includes("&")) reject(`${context}-contains-a-bare-ampersand`);
  return decoded + tail;
}

function encodeXmlText(value: string): string {
  return value.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}

/**
 * Mask markup whose contents must not participate in selector scans while
 * preserving JavaScript string offsets exactly.
 */
function maskIgnoredMarkup(xml: string): string {
  const patterns = [/<!--[\s\S]*?-->/g, /<\?[\s\S]*?\?>/g];
  let masked = xml;
  for (const pattern of patterns) {
    masked = masked.replace(pattern, (match) => " ".repeat(match.length));
  }
  return masked;
}

/**
 * Tokenize XML tags without decoding or normalizing any source bytes. Unlike a
 * `[^>]*` regular expression this keeps `>` inside a quoted attribute value in
 * the same tag, which is required before offsets can be trusted.
 */
function xmlTagTokens(xml: string): XmlTagToken[] {
  const tokens: XmlTagToken[] = [];
  for (let cursor = 0; cursor < xml.length; ) {
    const start = xml.indexOf("<", cursor);
    if (start < 0) break;
    let quote = "";
    let end = -1;
    for (let index = start + 1; index < xml.length; index += 1) {
      const character = xml[index];
      if (quote) {
        if (character === quote) quote = "";
      } else if (character === '"' || character === "'") {
        quote = character;
      } else if (character === ">") {
        end = index + 1;
        break;
      }
    }
    if (end < 0 || quote) reject("malformed-xml-tag");

    const body = xml.slice(start + 1, end - 1).trim();
    const closing = body.startsWith("/");
    const nameMatch = /^\/?([A-Za-z_][\w:.-]*)(?=\s|\/?$)/.exec(body);
    if (!nameMatch) reject("unsupported-or-malformed-xml-tag");
    const selfClosing = !closing && /\/\s*$/.test(body);
    tokens.push({
      start,
      end,
      name: nameMatch[1],
      kind: closing ? "close" : selfClosing ? "self" : "open",
    });
    cursor = end;
  }
  return tokens;
}

function validatedElementPairs(tokens: XmlTagToken[]): Map<number, XmlTagToken> {
  const stack: XmlTagToken[] = [];
  const pairs = new Map<number, XmlTagToken>();
  for (const token of tokens) {
    if (token.kind === "open") {
      stack.push(token);
      continue;
    }
    if (token.kind === "self") continue;
    const opening = stack.pop();
    if (!opening || opening.name !== token.name) reject("xml-elements-are-not-balanced");
    pairs.set(opening.start, token);
  }
  if (stack.length !== 0) reject("xml-elements-are-not-balanced");
  return pairs;
}

function attributes(tag: string, context: string): Map<string, string> {
  const result = new Map<string, string>();
  const body = tag.slice(tag.indexOf(" ") + 1, tag.lastIndexOf(">"));
  const attribute = /(?:^|\s)([A-Za-z_][\w:.-]*)\s*=\s*(?:"([^"]*)"|'([^']*)')/g;
  for (const match of body.matchAll(attribute)) {
    const name = match[1];
    if (result.has(name)) reject(`${context}-has-a-duplicate-${name}-attribute`);
    result.set(name, decodeXmlText(match[2] ?? match[3] ?? "", `${context}-${name}`));
  }
  return result;
}

function uniqueTag(shape: ShapeSlice, pattern: RegExp, context: string): string {
  const matches = [...shape.masked.matchAll(pattern)];
  if (matches.length !== 1 || matches[0].index === undefined) {
    reject(`${context}-tag-count-${matches.length}`);
  }
  return shape.xml.slice(matches[0].index, matches[0].index + matches[0][0].length);
}

function optionalUniqueTag(
  shape: ShapeSlice,
  pattern: RegExp,
  context: string
): string | undefined {
  const matches = [...shape.masked.matchAll(pattern)];
  if (matches.length === 0) return undefined;
  if (matches.length !== 1 || matches[0].index === undefined) {
    reject(`${context}-tag-count-${matches.length}`);
  }
  return shape.xml.slice(matches[0].index, matches[0].index + matches[0][0].length);
}

function shapeSlices(xml: string, masked: string): ShapeSlice[] {
  const shapes: ShapeSlice[] = [];
  const pattern = /<p:sp(?=[\s>])[\s\S]*?<\/p:sp\s*>/g;
  for (const match of masked.matchAll(pattern)) {
    if (match.index === undefined) continue;
    const start = match.index;
    const end = start + match[0].length;
    shapes.push({ start, end, xml: xml.slice(start, end), masked: masked.slice(start, end) });
  }
  return shapes;
}

function roleMatches(shape: ShapeSlice, role: string, idx: string): boolean {
  const semantic = uniqueTag(
    shape,
    /<v:placeholder(?=[\s>])[^>]*\/?\s*>/g,
    "semantic-placeholder"
  );
  const semanticValues = attributes(semantic, "semantic-placeholder");
  if (semanticValues.get("role") !== role || semanticValues.get("idx") !== idx) {
    return false;
  }
  const cNvPr = uniqueTag(shape, /<p:cNvPr(?=[\s>])[^>]*\/?\s*>/g, "cNvPr");
  const values = attributes(cNvPr, "cNvPr");
  if (values.get("name") !== `placeholder:${role}`) return false;
  const fields = (values.get("descr") ?? "").split(";").map((field) => field.trim());
  return fields.includes(`vnext:role=${role}`);
}

function matchingShapes(
  shapes: ShapeSlice[],
  selector: StableTextSelector
): ShapeSlice[] {
  if (selector.kind === "element") {
    const expectedId = normalizedElementId(selector.id);
    return shapes.filter((shape) => {
      const cNvPr = uniqueTag(shape, /<p:cNvPr(?=[\s>])[^>]*\/?\s*>/g, "cNvPr");
      return attributes(cNvPr, "cNvPr").get("id") === expectedId;
    });
  }

  if (!Number.isSafeInteger(selector.idx) || selector.idx < 0) {
    reject("placeholder-idx-must-be-a-non-negative-integer");
  }
  if (selector.role !== undefined) {
    if (
      selector.role.length === 0 ||
      selector.role.length > 128 ||
      /[;=\u0000-\u001f]/.test(selector.role)
    ) {
      reject("placeholder-role-is-invalid");
    }
  }
  const expectedIdx = String(selector.idx);
  return shapes.filter((shape) => {
    const ph = optionalUniqueTag(shape, /<p:ph(?=[\s>])[^>]*\/?\s*>/g, "ph");
    if (!ph) return false;
    if (attributes(ph, "ph").get("idx") !== expectedIdx) return false;
    return selector.role === undefined || roleMatches(shape, selector.role, expectedIdx);
  });
}

function textNodes(shape: ShapeSlice): TextNodeSlice[] {
  const nodes: TextNodeSlice[] = [];
  const tokens = xmlTagTokens(shape.masked);
  const pairs = validatedElementPairs(tokens);
  for (const opening of tokens) {
    if (opening.kind !== "open" || opening.name !== "a:t") continue;
    const closing = pairs.get(opening.start);
    if (!closing) reject("malformed-a-t-node");
    const nested = tokens.some(
      (token) => token.start >= opening.end && token.end <= closing.start,
    );
    if (nested) reject("a-t-text-contains-markup");
    const contentStart = opening.end;
    const contentEnd = closing.start;
    const openingTag = shape.xml.slice(opening.start, contentStart);
    const encodedText = shape.xml.slice(contentStart, contentEnd);
    nodes.push({
      openingStart: opening.start,
      contentStart,
      contentEnd,
      openingTag,
      encodedText,
      decodedText: decodeXmlText(encodedText, "a-t-text"),
    });
  }
  return nodes;
}

function assertPlainRun(shape: ShapeSlice, node: TextNodeSlice): void {
  const tokens = xmlTagTokens(shape.masked);
  const pairs = validatedElementPairs(tokens);
  const stack: XmlTagToken[] = [];
  let runOpening: XmlTagToken | undefined;

  for (const token of tokens) {
    if (token.start === node.openingStart) {
      if (stack.some((ancestor) => ancestor.name === "a:fld")) {
        reject("field-text-is-not-mutable");
      }
      runOpening = stack.at(-1);
      if (!runOpening || runOpening.name !== "a:r") {
        reject("text-is-not-in-a-plain-run");
      }
      break;
    }
    if (token.kind === "open") stack.push(token);
    else if (token.kind === "close") stack.pop();
  }
  if (!runOpening) reject("text-is-not-in-a-plain-run");
  const runClosing = pairs.get(runOpening.start);
  if (!runClosing) reject("plain-run-is-not-closed");

  const directChildren: XmlTagToken[] = [];
  const runStack: XmlTagToken[] = [runOpening];
  for (const token of tokens) {
    if (token.start <= runOpening.start) continue;
    if (token.start >= runClosing.start) break;
    if (token.kind === "open" || token.kind === "self") {
      if (runStack.length === 1) directChildren.push(token);
      if (token.kind === "open") runStack.push(token);
    } else {
      runStack.pop();
    }
  }
  const childNames = directChildren.map((token) => token.name);
  if (directChildren.filter((token) => token.name === "a:t").length !== 1) {
    reject("plain-run-has-multiple-text-nodes");
  }
  const isPlainRun =
    (childNames.length === 1 && childNames[0] === "a:t") ||
    (childNames.length === 2 && childNames[0] === "a:rPr" && childNames[1] === "a:t");
  if (!isPlainRun) reject("plain-run-has-rich-or-unsupported-children");
  let directTextCursor = runOpening.end;
  for (const child of directChildren) {
    if (/\S/u.test(shape.masked.slice(directTextCursor, child.start))) {
      reject("plain-run-has-direct-text-outside-a-t");
    }
    directTextCursor =
      child.kind === "self" ? child.end : (pairs.get(child.start)?.end ?? child.end);
  }
  if (/\S/u.test(shape.masked.slice(directTextCursor, runClosing.start))) {
    reject("plain-run-has-direct-text-outside-a-t");
  }
}

function byteOffset(xml: string, codeUnitOffset: number): number {
  return Buffer.byteLength(xml.slice(0, codeUnitOffset), "utf8");
}

function countByteDifferences(left: Uint8Array, right: Uint8Array): number {
  let differences = Math.abs(left.byteLength - right.byteLength);
  const common = Math.min(left.byteLength, right.byteLength);
  for (let index = 0; index < common; index += 1) {
    if (left[index] !== right[index]) differences += 1;
  }
  return differences;
}

/**
 * Replace exactly one approved DrawingML `<a:t>` content byte slice.
 *
 * This deliberately does not parse and serialize XML: source bytes before and
 * after the approved content range are copied verbatim. Unsupported namespaces,
 * fields, cross-run edits, selector ambiguity, and whitespace semantics that
 * would require an attribute change are rejected instead of being approximated.
 */
export function patchTextSlice(
  sourceBytes: Uint8Array,
  request: TextSlicePatchRequest
): TextSlicePatchResult {
  if (!(sourceBytes instanceof Uint8Array)) reject("source-must-be-buffered-bytes");
  if (!ALLOWED_TEXT_PART.test(request.partName)) reject("part-name-is-not-an-approved-text-part");
  assertNonNegativeInteger(request.selector.occurrence, "occurrence");
  assertWellFormedText(request.expected.text, "expected-text");
  assertWellFormedText(request.replacement, "replacement-text");
  if (/[\r\n]/.test(request.replacement)) reject("replacement-requires-a-structural-line-break");

  const expectedHash = sha256(request.expected.text);
  if (request.expected.sha256 !== undefined) {
    if (!SHA256.test(request.expected.sha256)) reject("expected-text-sha256-is-invalid");
    if (request.expected.sha256.toLowerCase() !== expectedHash) {
      reject("expected-text-sha256-mismatch");
    }
  }

  let xml: string;
  try {
    // Keep a UTF-8 BOM in the decoded string so UTF-16 code-unit offsets and
    // original byte offsets remain aligned. The default decoder strips it.
    xml = new TextDecoder("utf-8", { fatal: true, ignoreBOM: true }).decode(sourceBytes);
  } catch {
    reject("source-is-not-utf8");
  }
  if (xml.includes("\u0000")) reject("source-is-not-utf8-xml");
  if (!Buffer.from(xml, "utf8").equals(Buffer.from(sourceBytes))) {
    reject("source-utf8-round-trip-mismatch");
  }
  if (/<!DOCTYPE|<!ENTITY|<!\[CDATA\[/i.test(xml)) reject("source-has-unsupported-xml-markup");
  const encoding = /^\uFEFF?\s*<\?xml\s[^>]*encoding\s*=\s*["']([^"']+)["']/i.exec(xml)?.[1];
  if (encoding && !/^utf-?8$/i.test(encoding)) reject("source-declares-a-non-utf8-encoding");

  const masked = maskIgnoredMarkup(xml);
  const shapes = matchingShapes(shapeSlices(xml, masked), request.selector);
  if (shapes.length !== 1) reject(`selector-shape-count-${shapes.length}`);
  const shape = shapes[0];
  const candidates = textNodes(shape).filter((node) => node.decodedText === request.expected.text);
  const occurrence = request.selector.occurrence;
  if (occurrence === undefined && candidates.length !== 1) {
    reject(`expected-text-node-count-${candidates.length}`);
  }
  if (occurrence !== undefined && occurrence >= candidates.length) {
    reject(`expected-text-occurrence-out-of-range-${candidates.length}`);
  }
  const node = candidates[occurrence ?? 0];
  assertPlainRun(shape, node);

  const tAttributes = attributes(node.openingTag, "a-t");
  if (
    (/^\s|\s$/u.test(request.replacement) || /[\t]/.test(request.replacement)) &&
    tAttributes.get("xml:space") !== "preserve"
  ) {
    reject("replacement-whitespace-would-require-xml-space-preserve");
  }

  const encodedReplacement = encodeXmlText(request.replacement);
  const replacementBytes = new TextEncoder().encode(encodedReplacement);
  const contentStart = shape.start + node.contentStart;
  const contentEnd = shape.start + node.contentEnd;
  const startByte = byteOffset(xml, contentStart);
  const endByte = byteOffset(xml, contentEnd);
  const source = Uint8Array.from(sourceBytes);
  const result = new Uint8Array(startByte + replacementBytes.byteLength + source.byteLength - endByte);
  result.set(source.subarray(0, startByte), 0);
  result.set(replacementBytes, startByte);
  result.set(source.subarray(endByte), startByte + replacementBytes.byteLength);

  const resultEndByte = startByte + replacementBytes.byteLength;
  const outsideSliceDiffBytes =
    countByteDifferences(source.subarray(0, startByte), result.subarray(0, startByte)) +
    countByteDifferences(source.subarray(endByte), result.subarray(resultEndByte));
  if (outsideSliceDiffBytes !== 0) reject("internal-outside-slice-diff");

  return {
    bytes: result,
    receipt: {
      schemaVersion: 1,
      operation: "PATCH_TEXT_SLICE",
      partName: request.partName,
      selector: { ...request.selector },
      sourceSha256: sha256(source),
      resultSha256: sha256(result),
      oldTextSha256: expectedHash,
      replacementTextSha256: sha256(request.replacement),
      sourceSliceSha256: sha256(source.subarray(startByte, endByte)),
      resultSliceSha256: sha256(replacementBytes),
      sourceSlice: { startByte, endByte },
      resultSlice: { startByte, endByte: resultEndByte },
      outsideSliceDiffBytes: 0,
      outsideSliceUnchanged: true,
    },
  };
}
