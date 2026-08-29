import { createHash } from "node:crypto";

import type { PresentationExportQualityReport } from "../presentation-export-quality.ts";
import type { ActiveEmissionExportBinding } from "./active-emission.ts";
import { EDITABLE_HYBRID_SHADOW_PROFILE_ID } from "./presentation-mode.ts";
import type { AuthoredHybridNativeReadbackElement } from "./pptx-quality-inspection.ts";

const DIGEST = /^sha256:[0-9a-f]{64}$/;
const RASTER_OWNER_CAPABILITY = "raster.owner.v1";

type QidKind = "text" | "shape" | "image" | "table" | "chart";

export interface PowerPointQidExpectation {
  readonly schemaVersion: "authored-powerpoint-qid-expectation/v2";
  readonly presentationId: string;
  readonly presentationRevision: number;
  readonly activeManifestDigest: string;
  readonly sceneDigest: string;
  readonly editableHybridProfileId: typeof EDITABLE_HYBRID_SHADOW_PROFILE_ID;
  readonly pptxSha256: string;
  readonly slideCount: number;
  readonly expectedCount: number;
  readonly entries: readonly PowerPointQidExpectationEntry[];
}

export interface PowerPointQidExpectationEntry {
  readonly slideNumber: number;
  readonly slideId: string;
  readonly qid: string;
  readonly kind: QidKind;
  readonly capabilityId: string;
  readonly hybridDisposition: "native" | "raster";
  readonly advancedTextExpectation: AdvancedTextExpectation | null;
  readonly tableExpectation: TableExpectation | null;
  readonly chartExpectation: null;
}

interface AdvancedTextExpectation {
  readonly schemaVersion: "authored-advanced-text-com-expectation/v1";
  readonly textRunMap: readonly unknown[];
  readonly textRunStyles: readonly unknown[];
  readonly autoFit: "none" | "shrink";
  readonly advancedTextDigest: string;
}

interface TableExpectation {
  readonly schemaVersion: "authored-table-com-expectation/v1";
  readonly rowCount: number;
  readonly columnCount: number;
  readonly cells: readonly (readonly string[])[];
  readonly rowHeightsMicroPoint: readonly number[];
  readonly columnWidthsMicroPoint: readonly number[];
  readonly mergedCells: readonly unknown[];
  readonly cellStyles: readonly unknown[];
  readonly tableDataDigest: string;
  readonly tableExpectationDigest: string;
}

export interface PowerPointQidExpectationSource {
  readonly presentationId: string;
  readonly presentationRevision: number;
  readonly pptxSha256: string;
  readonly activeBinding: ActiveEmissionExportBinding;
  readonly editableHybridProfileId: string | undefined;
}

function canonical(value: unknown): string {
  if (value === null || typeof value !== "object") return JSON.stringify(value);
  if (Array.isArray(value)) return `[${value.map(canonical).join(",")}]`;
  const record = value as Record<string, unknown>;
  return `{${Object.keys(record).sort().map((key) =>
    `${JSON.stringify(key)}:${canonical(record[key])}`
  ).join(",")}}`;
}

function digest(value: unknown): string {
  return `sha256:${createHash("sha256").update(canonical(value), "utf8").digest("hex")}`;
}

function normalizedDigest(value: string): string {
  return value.startsWith("sha256:") ? value : `sha256:${value}`;
}

function expectationError(reason: string): never {
  throw new Error(`authored_powerpoint_qid_expectation_invalid:${reason}`);
}

function fixedPoint(value: number): number {
  if (!Number.isFinite(value)) expectationError("non_finite_table_measurement");
  return Math.round(value * 1_000_000);
}

function qidForSlide(slideNumber: number, elementId: string): string {
  const prefix = `s${String(slideNumber).padStart(2, "0")}-`;
  const existingPrefix = elementId.match(/^s\d{2,3}-/);
  if (!existingPrefix) return `${prefix}${elementId}`;
  if (!elementId.startsWith(prefix)) {
    expectationError(`qid_slide_prefix:${slideNumber}:${elementId}`);
  }
  return elementId;
}

function advancedExpectation(
  native: AuthoredHybridNativeReadbackElement,
): AdvancedTextExpectation | null {
  if (native.capabilityId !== "editable.text.advanced.v1") return null;
  if (
    native.autoFit === undefined ||
    !native.textRunMap?.length ||
    !native.textRunStyles?.length ||
    !native.advancedTextDigest ||
    native.textRunMap.length !== native.textRunStyles.length ||
    !DIGEST.test(native.advancedTextDigest)
  ) {
    expectationError(`advanced_text_receipt:${native.elementId}`);
  }
  return {
    schemaVersion: "authored-advanced-text-com-expectation/v1",
    textRunMap: native.textRunMap,
    textRunStyles: native.textRunStyles,
    autoFit: native.autoFit,
    advancedTextDigest: native.advancedTextDigest,
  };
}

function tableExpectation(native: AuthoredHybridNativeReadbackElement): TableExpectation | null {
  if (native.capabilityId !== "editable.table.advanced.v1") return null;
  const table = native.table;
  if (
    !table || table.rows < 1 || table.columns < 1 ||
    table.cellTexts.length !== table.rows * table.columns ||
    table.rowHeightsPt.length !== table.rows ||
    table.columnWidthsPt.length !== table.columns
  ) {
    expectationError(`table_receipt:${native.elementId}`);
  }
  const cells = Array.from({ length: table.rows }, (_, row) =>
    table.cellTexts.slice(row * table.columns, (row + 1) * table.columns),
  );
  const tableDataDigest = digest({
    cells,
    mergedCells: table.mergedCells,
    cellStyles: table.cellStyles,
  });
  const projection = {
    rowCount: table.rows,
    columnCount: table.columns,
    cells,
    rowHeightsMicroPoint: table.rowHeightsPt.map(fixedPoint),
    columnWidthsMicroPoint: table.columnWidthsPt.map(fixedPoint),
    mergedCells: table.mergedCells,
    cellStyles: table.cellStyles,
    tableDataDigest,
  };
  return {
    schemaVersion: "authored-table-com-expectation/v1",
    ...projection,
    tableExpectationDigest: digest(projection),
  };
}

/**
 * Emits a COM authority sidecar only for the bounded native profile.  Other
 * Hybrid outputs remain exportable but cannot be passed to actual-product COM
 * QA, avoiding a guessed ownership contract.
 */
export function createPowerPointQidExpectation(
  quality: PresentationExportQualityReport,
  source: PowerPointQidExpectationSource,
): PowerPointQidExpectation | null {
  const binding = source.activeBinding;
  const activeManifestDigest = binding.canonicalDigest;
  const sceneDigest = binding.sceneDigest;
  if (
    source.editableHybridProfileId !== EDITABLE_HYBRID_SHADOW_PROFILE_ID ||
    binding.canonicalMode !== "bundleGraph" ||
    !activeManifestDigest || !sceneDigest ||
    !DIGEST.test(activeManifestDigest) || !DIGEST.test(sceneDigest) ||
    !DIGEST.test(normalizedDigest(source.pptxSha256))
  ) {
    return null;
  }
  if (
    quality.mode !== "hybrid" ||
    quality.slides.length !== binding.slides.length ||
    binding.slides.length < 1 || binding.slides.length > 100
  ) {
    expectationError("binding_slide_range");
  }
  const entries: PowerPointQidExpectationEntry[] = [];
  const qids = new Set<string>();
  for (let index = 0; index < binding.slides.length; index += 1) {
    const bindingSlide = binding.slides[index];
    const qualitySlide = quality.slides[index];
    const slideNumber = index + 1;
    if (
      qualitySlide.slideNumber !== slideNumber ||
      !bindingSlide.slideId ||
      !qualitySlide.qidCoverage?.length
    ) {
      expectationError(`slide_coverage:${slideNumber}`);
    }
    const declared = new Map(bindingSlide.declaredElements.map((element) => [element.elementId, element]));
    const native = new Map(
      ((qualitySlide.nativeElementReadback ?? []) as AuthoredHybridNativeReadbackElement[])
        .map((entry) => [entry.elementId, entry]),
    );
    for (const coverage of qualitySlide.qidCoverage) {
      const element = declared.get(coverage.elementId);
      if (!element || element.kind !== coverage.kind) {
        expectationError(`declared_owner:${slideNumber}:${coverage.elementId}`);
      }
      const qid = qidForSlide(slideNumber, coverage.elementId);
      if (qids.has(qid)) expectationError(`duplicate_qid:${qid}`);
      qids.add(qid);
      if (coverage.logical === "native") {
        const readback = native.get(coverage.elementId);
        if (!readback || readback.slideNumber !== slideNumber || readback.kind !== coverage.kind || !readback.capabilityId) {
          expectationError(`native_readback:${qid}`);
        }
        entries.push({
          slideNumber,
          slideId: bindingSlide.slideId,
          qid,
          kind: coverage.kind,
          capabilityId: readback.capabilityId,
          hybridDisposition: "native",
          advancedTextExpectation: advancedExpectation(readback),
          tableExpectation: tableExpectation(readback),
          chartExpectation: null,
        });
      } else if (coverage.logical === "raster") {
        if (native.has(coverage.elementId)) expectationError(`raster_has_native_readback:${qid}`);
        entries.push({
          slideNumber,
          slideId: bindingSlide.slideId,
          qid,
          kind: coverage.kind,
          capabilityId: RASTER_OWNER_CAPABILITY,
          hybridDisposition: "raster",
          advancedTextExpectation: null,
          tableExpectation: null,
          chartExpectation: null,
        });
      } else {
        expectationError(`unknown_disposition:${qid}`);
      }
    }
  }
  if (!entries.length) expectationError("empty_entries");
  return {
    schemaVersion: "authored-powerpoint-qid-expectation/v2",
    presentationId: source.presentationId,
    presentationRevision: source.presentationRevision,
    activeManifestDigest,
    sceneDigest,
    editableHybridProfileId: EDITABLE_HYBRID_SHADOW_PROFILE_ID,
    pptxSha256: normalizedDigest(source.pptxSha256),
    slideCount: binding.slides.length,
    expectedCount: entries.length,
    entries,
  };
}
