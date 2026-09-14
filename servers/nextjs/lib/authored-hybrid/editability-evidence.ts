import type { PreparedSlideOutcome } from "./slide-layer.ts";
import type { AuthoredSemanticSlideContext } from "./slide-layer-semantics.ts";
import {
  inspectPptxEditability,
  inspectPptxNativeElementReadback,
} from "./pptx-quality-inspection.ts";
import { readPptxArchive } from "./pptx-archive.ts";
import { isFragmentBand } from "./raster-fragments.ts";

export const PUBLIC_HYBRID_EDITABILITY_SCHEMA =
  "public-hybrid-editability/v1" as const;

export type PublicHybridEditabilityStatus =
  | "fully-editable"
  | "partially-editable"
  | "not-editable"
  | "not-applicable"
  | "unknown";

export type EditabilityTargetRole =
  | "title"
  | "body"
  | "table-cell"
  | "supported-shape"
  | "photo"
  | "page-number"
  | "decoration"
  | "unknown";

export type EditabilityTargetOutcome =
  | "native-correspondence"
  | "rasterized"
  | "missing"
  | "occluded"
  | "unknown";

export type EditabilityReasonCode =
  | "source-role-unknown"
  | "native-correspondence-missing"
  | "native-kind-mismatch"
  | "native-text-unverified"
  | "native-text-mismatch"
  | "native-outside-canvas"
  | "native-readback-unverified"
  | "rasterized-required-target"
  | "required-target-missing"
  | "selection-obstructed"
  | "photo-only-page"
  | "no-required-targets";

export interface EditabilityTargetEvidence {
  readonly targetId: string;
  readonly role: EditabilityTargetRole;
  readonly required: boolean | null;
  readonly outcome: EditabilityTargetOutcome;
  readonly reasons?: readonly EditabilityReasonCode[];
}

interface TargetCounts {
  total: number;
  native: number;
  rasterized: number;
  missing: number;
  occluded: number;
  unknown: number;
}

type RequiredRole = "title" | "body" | "tableCell" | "supportedShape";
type RequiredByRole = Record<RequiredRole, TargetCounts>;

export interface PublicHybridEditabilitySlideEvidence {
  slideNumber: number;
  verdict: PublicHybridEditabilityStatus;
  imageOnly: boolean;
  photoOnlyExempt: boolean;
  requiredTargetCount: number;
  nativeCorrespondenceCount: number;
  rasterizedTargetCount: number;
  missingTargetCount: number;
  occludedTargetCount: number;
  unknownTargetCount: number;
  requiredByRole: RequiredByRole;
  fullSlideRaster: boolean;
  rasterFragmentCount: number;
  reasons: EditabilityReasonCode[];
}

export interface PublicHybridEditabilityEvidence {
  schemaVersion: typeof PUBLIC_HYBRID_EDITABILITY_SCHEMA;
  verdict: PublicHybridEditabilityStatus;
  requiredTargetCount: number;
  nativeCorrespondenceCount: number;
  rasterizedTargetCount: number;
  missingTargetCount: number;
  occludedTargetCount: number;
  unknownTargetCount: number;
  requiredByRole: RequiredByRole;
  imageOnlyPages: number[];
  fullSlideRasterCount: number;
  rasterFragmentCount: number;
  powerpointInteraction: {
    status: "not-run" | "passed" | "failed";
    testedTargetCount: number;
  };
  slides: PublicHybridEditabilitySlideEvidence[];
}

export interface EditabilitySlideInput {
  readonly slideNumber: number;
  readonly targets: readonly EditabilityTargetEvidence[];
  readonly fullSlideRaster?: boolean;
  readonly rasterFragments?: number;
}

const requiredRoles = new Set<EditabilityTargetRole>([
  "title", "body", "table-cell", "supported-shape",
]);

function emptyCounts(): TargetCounts {
  return { total: 0, native: 0, rasterized: 0, missing: 0, occluded: 0, unknown: 0 };
}

function emptyByRole(): RequiredByRole {
  return {
    title: emptyCounts(), body: emptyCounts(), tableCell: emptyCounts(),
    supportedShape: emptyCounts(),
  };
}

function addCount(counts: TargetCounts, outcome: EditabilityTargetOutcome): void {
  counts.total += 1;
  if (outcome === "native-correspondence") counts.native += 1;
  else counts[outcome] += 1;
}

function roleKey(role: EditabilityTargetRole): RequiredRole | null {
  if (role === "table-cell") return "tableCell";
  if (role === "supported-shape") return "supportedShape";
  return role === "title" || role === "body" ? role : null;
}

function addCounts(target: TargetCounts, source: TargetCounts): void {
  for (const key of Object.keys(target) as Array<keyof TargetCounts>) {
    target[key] += source[key];
  }
}

function slideStatus(counts: TargetCounts, hasUnknownRole: boolean): PublicHybridEditabilityStatus {
  if (hasUnknownRole) return "unknown";
  if (counts.total === 0) return "not-applicable";
  if (counts.unknown > 0) return "unknown";
  if (counts.native === counts.total) return "fully-editable";
  if (counts.native > 0) return "partially-editable";
  return "not-editable";
}

/**
 * Reduce target-level evidence to the bounded public receipt. Target IDs and
 * source text deliberately do not cross this persistence boundary.
 */
export function evaluatePublicHybridEditability(
  slides: readonly EditabilitySlideInput[],
  powerpointInteraction: PublicHybridEditabilityEvidence["powerpointInteraction"] = {
    status: "not-run", testedTargetCount: 0,
  },
): PublicHybridEditabilityEvidence {
  const seenSlides = new Set<number>();
  const resultSlides = slides.map((slide) => {
    if (!Number.isSafeInteger(slide.slideNumber) || slide.slideNumber < 1 || seenSlides.has(slide.slideNumber)) {
      throw new Error("public_hybrid_editability_slide_identity_invalid");
    }
    seenSlides.add(slide.slideNumber);
    const seenTargets = new Set<string>();
    const requiredTargets = emptyCounts();
    const requiredByRole = emptyByRole();
    let photoCount = 0;
    let excludedCount = 0;
    let hasUnknownRole = false;
    const reasons = new Set<EditabilityReasonCode>();
    for (const target of slide.targets) {
      if (!target.targetId || seenTargets.has(target.targetId)) {
        throw new Error("public_hybrid_editability_target_identity_invalid");
      }
      seenTargets.add(target.targetId);
      if (target.required === null || target.role === "unknown") hasUnknownRole = true;
      if (target.role === "photo") photoCount += 1;
      if (target.role === "page-number" || target.role === "decoration") excludedCount += 1;
      if (target.required === true) {
        if (!requiredRoles.has(target.role)) {
          throw new Error("public_hybrid_editability_required_role_invalid");
        }
        addCount(requiredTargets, target.outcome);
        addCount(requiredByRole[roleKey(target.role)!], target.outcome);
      }
      for (const reason of target.reasons ?? []) reasons.add(reason);
    }
    const status = slideStatus(requiredTargets, hasUnknownRole);
    const photoOnlyExempt = status === "not-applicable" && photoCount > 0 &&
      photoCount + excludedCount === slide.targets.length;
    if (hasUnknownRole) reasons.add("source-role-unknown");
    if (requiredTargets.total === 0) reasons.add("no-required-targets");
    if (photoOnlyExempt) reasons.add("photo-only-page");
    return {
      slideNumber: slide.slideNumber,
      verdict: status,
      imageOnly: Boolean(slide.fullSlideRaster) && requiredTargets.native === 0,
      photoOnlyExempt,
      requiredTargetCount: requiredTargets.total,
      nativeCorrespondenceCount: requiredTargets.native,
      rasterizedTargetCount: requiredTargets.rasterized,
      missingTargetCount: requiredTargets.missing,
      occludedTargetCount: requiredTargets.occluded,
      unknownTargetCount: requiredTargets.unknown,
      requiredByRole,
      fullSlideRaster: Boolean(slide.fullSlideRaster),
      rasterFragmentCount: Math.max(0, Math.trunc(slide.rasterFragments ?? 0)),
      reasons: [...reasons].sort(),
    };
  }).sort((left, right) => left.slideNumber - right.slideNumber);

  const requiredTargets = emptyCounts();
  const requiredByRole = emptyByRole();
  for (const slide of resultSlides) {
    addCounts(requiredTargets, {
      total: slide.requiredTargetCount,
      native: slide.nativeCorrespondenceCount,
      rasterized: slide.rasterizedTargetCount,
      missing: slide.missingTargetCount,
      occluded: slide.occludedTargetCount,
      unknown: slide.unknownTargetCount,
    });
    for (const key of Object.keys(requiredByRole) as RequiredRole[]) {
      addCounts(requiredByRole[key], slide.requiredByRole[key]);
    }
  }
  const status = resultSlides.some((slide) => slide.verdict === "unknown")
    ? "unknown"
    : slideStatus(requiredTargets, false);
  return {
    schemaVersion: PUBLIC_HYBRID_EDITABILITY_SCHEMA,
    verdict: status,
    requiredTargetCount: requiredTargets.total,
    nativeCorrespondenceCount: requiredTargets.native,
    rasterizedTargetCount: requiredTargets.rasterized,
    missingTargetCount: requiredTargets.missing,
    occludedTargetCount: requiredTargets.occluded,
    unknownTargetCount: requiredTargets.unknown,
    requiredByRole,
    imageOnlyPages: resultSlides.filter((slide) => slide.imageOnly).map((slide) => slide.slideNumber),
    fullSlideRasterCount: resultSlides.filter((slide) => slide.fullSlideRaster).length,
    rasterFragmentCount: resultSlides.reduce((sum, slide) => sum + slide.rasterFragmentCount, 0),
    powerpointInteraction: {
      status: powerpointInteraction.status,
      testedTargetCount: Math.max(0, Math.trunc(powerpointInteraction.testedTargetCount)),
    },
    slides: resultSlides,
  };
}

function xmlDecode(value: string): string {
  return value.replace(/&#(x[0-9a-f]+|\d+);|&(amp|lt|gt|quot|apos);/gi, (match, numeric, named) => {
    if (numeric) return String.fromCodePoint(Number.parseInt(numeric.startsWith("x") ? numeric.slice(1) : numeric, numeric.startsWith("x") ? 16 : 10));
    return ({ amp: "&", lt: "<", gt: ">", quot: '"', apos: "'" } as Record<string, string>)[String(named).toLowerCase()] ?? match;
  });
}

function normalizedText(value: string): string {
  return value.normalize("NFC").replace(/\s+/gu, " ").trim();
}

interface HtmlSourceElement { role: string; kind: string }

function htmlSourceElements(html: string): Map<string, HtmlSourceElement> {
  const elements = new Map<string, HtmlSourceElement>();
  for (const match of html.matchAll(/<[A-Za-z][^>]*>/g)) {
    const tag = match[0];
    const qid = tag.match(/\bdata-qid\s*=\s*(["'])([^"']+)\1/i)?.[2];
    if (!qid || elements.has(qid)) continue;
    const role = tag.match(/\bdata-semantic-role\s*=\s*(["'])([^"']+)\1/i)?.[2] ?? "";
    const tagName = /^<([A-Za-z][A-Za-z0-9:-]*)/.exec(tag)?.[1].toLowerCase() ?? "";
    const kind = tagName === "table" ? "table"
      : tagName === "img" ? "image"
      : /^(?:h[1-6]|p|span|li|td|th|label|blockquote)$/.test(tagName) ? "text"
      : "unknown";
    elements.set(xmlDecode(qid), { role: xmlDecode(role).trim().toLowerCase(), kind });
  }
  return elements;
}

function classifyRole(kind: string, role: string, nativeTextRole?: string): EditabilityTargetRole {
  const value = (role || nativeTextRole || "").toLowerCase();
  if (/(^|[-_])(page[-_]?number|slide[-_]?number)([-_]|$)/.test(value)) return "page-number";
  if (/(^|[-_])(decoration|decorative|furniture|running[-_]?header|footer)([-_]|$)/.test(value)) return "decoration";
  if (kind === "image" && /(^|[-_])(photo|image|illustration|figure)([-_]|$)/.test(value)) return "photo";
  if (kind === "table") return "table-cell";
  if (kind === "text") {
    if (/(^|[-_])title([-_]|$)/.test(value)) return "title";
    if (/(^|[-_])(body|subtitle|supporting|content|caption|label|data|quote|kpi)([-_]|$)/.test(value)) return "body";
  }
  if ((kind === "shape" || kind === "chart" || kind === "connector") &&
      /(^|[-_])(body|supporting|content|data|chart|diagram)([-_]|$)/.test(value)) {
    return "supported-shape";
  }
  return "unknown";
}

function requiredForRole(role: EditabilityTargetRole): boolean | null {
  if (requiredRoles.has(role)) return true;
  if (role === "unknown") return null;
  return false;
}

function nativeElements(outcome: PreparedSlideOutcome): Map<string, { kind: string; textRole?: string; text?: string }> {
  const result = new Map<string, { kind: string; textRole?: string; text?: string }>();
  const visit = (element: NonNullable<PreparedSlideOutcome["layer"]>["elements"][number]) => {
    result.set(element.source.id, {
      kind: element.kind === "group" ? "shape" : element.kind,
      ...(element.kind === "text" ? {
        textRole: element.source.text.role,
        text: element.source.text.plainText,
      } : {}),
    });
    if (element.kind === "group") element.children.forEach(visit);
  };
  outcome.layer?.elements.forEach(visit);
  return result;
}

function pptxTextByQid(pptxBytes: Buffer): Map<string, string> {
  const result = new Map<string, string>();
  for (const [name, bytes] of readPptxArchive(pptxBytes)) {
    const slideNumber = /^ppt\/slides\/slide(\d+)\.xml$/.exec(name)?.[1];
    if (!slideNumber) continue;
    const xml = bytes.toString("utf8");
    for (const match of xml.matchAll(/<p:sp\b[\s\S]*?<\/p:sp>/g)) {
      const block = match[0];
      const rawName = block.match(/<p:cNvPr\b[^>]*\bname="([^"]*)"/)?.[1];
      const identity = rawName && /^(?:DevAX hybrid (?:native |raster )?(?:text|shape) |DevAX hybrid raster )([A-Za-z][A-Za-z0-9._:-]{0,127})$/.exec(xmlDecode(rawName));
      if (!identity) continue;
      result.set(`${slideNumber}:${identity[1]}`, normalizedText([...block.matchAll(/<a:t(?:\s[^>]*)?>([\s\S]*?)<\/a:t>/g)].map((item) => xmlDecode(item[1] ?? "")).join(" ")));
    }
  }
  return result;
}

function tableCellTexts(element: AuthoredSemanticSlideContext["declaredElements"][number]): string[] {
  if (element.kind !== "table" || !element.payload || !Array.isArray(element.payload.rows)) return [];
  return element.payload.rows.flatMap((row) => Array.isArray(row)
    ? row.map((cell) => cell && typeof cell === "object" && typeof (cell as { text?: unknown }).text === "string"
      ? normalizedText((cell as { text: string }).text) : "")
    : []);
}

export interface CreatePublicHybridEditabilityEvidenceOptions {
  readonly outcomes: readonly PreparedSlideOutcome[];
  readonly semantics: readonly (AuthoredSemanticSlideContext | null)[];
  readonly htmls: readonly string[];
  readonly pptxBytes: Buffer;
  readonly powerpointInteraction?: PublicHybridEditabilityEvidence["powerpointInteraction"];
}

/** Build target correspondence from the actual layer, raster and OOXML artifacts. */
export function createPublicHybridEditabilityEvidence(
  options: CreatePublicHybridEditabilityEvidenceOptions,
): PublicHybridEditabilityEvidence {
  const structural = inspectPptxEditability(options.pptxBytes);
  const readback = inspectPptxNativeElementReadback(options.pptxBytes);
  const pptxText = pptxTextByQid(options.pptxBytes);
  const presentationXml = readPptxArchive(options.pptxBytes).get("ppt/presentation.xml")?.toString("utf8") ?? "";
  const size = presentationXml.match(/<p:sldSz\b[^>]*\bcx="(\d+)"[^>]*\bcy="(\d+)"/) ??
    presentationXml.match(/<p:sldSz\b[^>]*\bcy="(\d+)"[^>]*\bcx="(\d+)"/);
  const cxFirst = size ? size[0].indexOf("cx=") < size[0].indexOf("cy=") : true;
  const canvasWidthPx = size ? Number(size[cxFirst ? 1 : 2]) / 9_525 : null;
  const canvasHeightPx = size ? Number(size[cxFirst ? 2 : 1]) / 9_525 : null;
  const slides: EditabilitySlideInput[] = options.outcomes.map((outcome, index) => {
    const slideNumber = index + 1;
    const semantic = options.semantics[index] ?? null;
    const htmlElements = htmlSourceElements(options.htmls[index] ?? "");
    const native = nativeElements(outcome);
    const structuralSlide = structural.slides.find((slide) => slide.slideNumber === slideNumber);
    const coverage = new Map((structuralSlide?.qidCoverage ?? []).map((entry) => [entry.elementId, entry]));
    const nativeReadback = new Map(readback.filter((entry) => entry.slideNumber === slideNumber).map((entry) => [entry.elementId, entry]));
    const fallbackIds = new Set(outcome.quality.fallbackElements?.map((entry) => entry.elementId) ?? []);
    const obstructionIds = new Set(outcome.layer?.selectionObstructions?.map((entry) => entry.elementId) ?? []);
    const declared = semantic?.declaredElements ?? [
      ...new Set([
        ...native.keys(),
        ...(outcome.quality.qidCoverage ?? []).map((entry) => entry.elementId),
        ...fallbackIds,
        ...htmlElements.keys(),
      ]),
    ].map((elementId) => ({ elementId, kind: native.get(elementId)?.kind ??
      outcome.quality.fallbackElements?.find((entry) => entry.elementId === elementId)?.candidateKind ??
      htmlElements.get(elementId)?.kind ?? "unknown" }));
    const targetSources = declared.length > 0 || outcome.layer
      ? declared
      : [{ elementId: `slide-${slideNumber}-source-unavailable`, kind: "unknown" }];
    const targets: EditabilityTargetEvidence[] = [];
    for (const element of targetSources) {
      const nativeSource = native.get(element.elementId);
      const effectiveKind = nativeSource?.kind ?? element.kind;
      const role = classifyRole(effectiveKind, htmlElements.get(element.elementId)?.role ?? "", nativeSource?.textRole);
      const cells = semantic ? tableCellTexts(element as AuthoredSemanticSlideContext["declaredElements"][number]) : [];
      const targetCount = role === "table-cell" ? Math.max(1, cells.length) : 1;
      const entry = coverage.get(element.elementId);
      const actual = nativeReadback.get(element.elementId);
      const reasons: EditabilityReasonCode[] = [];
      let outcomeValue: EditabilityTargetOutcome;
      if (obstructionIds.has(element.elementId)) {
        outcomeValue = "occluded";
        reasons.push("selection-obstructed");
      } else if (entry?.logical === "raster" || fallbackIds.has(element.elementId)) {
        outcomeValue = "rasterized";
        reasons.push("rasterized-required-target");
      } else if (!entry || entry.ooxml === "missing") {
        outcomeValue = structuralSlide?.imageFallback ? "rasterized" : "missing";
        reasons.push(outcomeValue === "rasterized" ? "rasterized-required-target" : "required-target-missing");
      } else if (entry.ooxml !== "present" || !actual) {
        outcomeValue = "unknown";
        reasons.push("native-readback-unverified");
      } else {
        const expectedKind = nativeSource?.kind ?? (element.kind === "connector" || element.kind === "group" || element.kind === "complexIsland"
          ? "shape" : element.kind);
        if (expectedKind !== actual.kind) {
          outcomeValue = "unknown";
          reasons.push("native-kind-mismatch");
        } else if (!actual.renderedBounds || actual.renderedBounds.width <= 0 || actual.renderedBounds.height <= 0 || actual.renderedBounds.x < 0 || actual.renderedBounds.y < 0 ||
            canvasWidthPx === null || canvasHeightPx === null ||
            actual.renderedBounds.x + actual.renderedBounds.width > canvasWidthPx + 0.01 ||
            actual.renderedBounds.y + actual.renderedBounds.height > canvasHeightPx + 0.01) {
          outcomeValue = "unknown";
          reasons.push("native-outside-canvas");
        } else if (effectiveKind === "text" && nativeSource?.text === undefined) {
          outcomeValue = "unknown";
          reasons.push("native-text-unverified");
        } else if (effectiveKind === "text" &&
            pptxText.get(`${slideNumber}:${element.elementId}`) !== normalizedText(nativeSource!.text!)) {
          outcomeValue = "unknown";
          reasons.push(pptxText.has(`${slideNumber}:${element.elementId}`) ? "native-text-mismatch" : "native-text-unverified");
        } else if (element.kind === "table" && cells.length > 0 &&
            (!actual.table || actual.table.cellTexts.map(normalizedText).some((text, cellIndex) => text !== cells[cellIndex]) || actual.table.cellTexts.length !== cells.length)) {
          outcomeValue = "unknown";
          reasons.push(actual.table ? "native-text-mismatch" : "native-text-unverified");
        } else {
          outcomeValue = "native-correspondence";
        }
      }
      for (let targetIndex = 0; targetIndex < targetCount; targetIndex += 1) {
        targets.push({
          targetId: targetCount === 1 ? element.elementId : `${element.elementId}:cell:${targetIndex}`,
          role,
          required: requiredForRole(role),
          outcome: outcomeValue,
          reasons,
        });
      }
    }
    const fullSlideRaster = Boolean(structuralSlide?.imageFallback || outcome.quality.imageFallback);
    const rasterFragments = outcome.layer?.rasterBands?.reduce((sum, band) =>
      sum + (isFragmentBand(band) ? band.fragments.length : 1), 0) ?? 0;
    return { slideNumber, targets, fullSlideRaster, rasterFragments };
  });
  return evaluatePublicHybridEditability(slides, options.powerpointInteraction);
}
