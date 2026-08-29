import type {
  PresentationExportFallbackElement,
  PresentationExportQidCoverage,
  PresentationExportQualityReport,
  PresentationExportSlideQuality,
} from "../presentation-export-quality.ts";

export const AUTHORED_NATIVE_DISPOSITION_RECEIPT_SCHEMA_VERSION =
  "authored-native-disposition-receipt/v1" as const;

type NativeDispositionKind = PresentationExportQidCoverage["kind"];

interface NativeDispositionCounts {
  readonly text: number;
  readonly image: number;
  readonly shape: number;
  readonly table: number;
  readonly chart: number;
}

export interface AuthoredNativeDispositionElementReceipt {
  readonly elementId: string;
  readonly kind: NativeDispositionKind;
  readonly disposition: "native" | "rasterFallback";
  readonly fallbackReasons: readonly string[];
}

export interface AuthoredNativeDispositionSlideReceipt {
  readonly schemaVersion: typeof AUTHORED_NATIVE_DISPOSITION_RECEIPT_SCHEMA_VERSION;
  readonly status: "passed" | "failed";
  readonly slideNumber: number;
  readonly nativeElements: number;
  readonly rasterFallbackElements: number;
  readonly totalSemanticElements: number;
  /** Exact integer percentage scale: 10,000 means 100% native. */
  readonly nativeRatioBasisPoints: number;
  /** Convenience projection of nativeRatioBasisPoints onto the 0..1 range. */
  readonly nativeRatio: number;
  readonly nativeByKind: NativeDispositionCounts;
  readonly rasterFallbackByKind: NativeDispositionCounts;
  readonly fallbackReasonCounts: Readonly<Record<string, number>>;
  readonly conflictingElementIds: readonly string[];
  readonly elements: readonly AuthoredNativeDispositionElementReceipt[];
}

export interface AuthoredNativeDispositionReceipt
  extends Omit<AuthoredNativeDispositionSlideReceipt, "slideNumber" | "elements"> {
  readonly slides: readonly AuthoredNativeDispositionSlideReceipt[];
}

export type WithAuthoredNativeDisposition<
  T extends PresentationExportQualityReport,
> = T & {
  readonly nativeDisposition: AuthoredNativeDispositionReceipt;
  readonly slides: Array<PresentationExportSlideQuality & {
    readonly nativeDisposition: AuthoredNativeDispositionSlideReceipt;
  }>;
};

const KINDS: readonly NativeDispositionKind[] = [
  "text",
  "image",
  "shape",
  "table",
  "chart",
];

function emptyCounts(): Record<NativeDispositionKind, number> {
  return { text: 0, image: 0, shape: 0, table: 0, chart: 0 };
}

function cleanReasons(reasons: readonly string[]): string[] {
  return [...new Set(reasons.map((reason) => reason.trim()).filter(Boolean))]
    .sort((left, right) => left.localeCompare(right));
}

function reasonCounts(
  fallbackElements: readonly PresentationExportFallbackElement[] | undefined,
  fallbackReasons: readonly string[],
): Record<string, number> {
  const counts = new Map<string, number>();
  const detailedReasons = (fallbackElements ?? []).flatMap((element) =>
    cleanReasons(element.reasons),
  );
  const reasons = detailedReasons.length
    ? detailedReasons
    : cleanReasons(fallbackReasons);
  for (const reason of reasons) {
    counts.set(reason, (counts.get(reason) ?? 0) + 1);
  }
  return Object.fromEntries(
    [...counts.entries()].sort(([left], [right]) => left.localeCompare(right)),
  );
}

function addReason(
  counts: Record<string, number>,
  reason: string,
): Record<string, number> {
  return Object.fromEntries(
    [...Object.entries({
      ...counts,
      [reason]: (counts[reason] ?? 0) + 1,
    })].sort(([left], [right]) => left.localeCompare(right)),
  );
}

function aggregateNativeCounts(
  slide: PresentationExportSlideQuality,
): Record<NativeDispositionKind, number> {
  return {
    text: Math.max(0, Math.trunc(slide.nativeTextElements)),
    image: Math.max(0, Math.trunc(slide.nativeImageElements)),
    shape: Math.max(0, Math.trunc(slide.nativeShapeElements)),
    table: Math.max(0, Math.trunc(slide.nativeTableElements ?? 0)),
    chart: Math.max(0, Math.trunc(slide.nativeChartElements ?? 0)),
  };
}

function total(counts: NativeDispositionCounts): number {
  return KINDS.reduce((sum, kind) => sum + counts[kind], 0);
}

function ratioBasisPoints(nativeElements: number, totalElements: number): number {
  return totalElements > 0
    ? Math.max(0, Math.min(10_000, Math.round(nativeElements * 10_000 / totalElements)))
    : 0;
}

export function createAuthoredNativeDispositionSlideReceipt(
  slide: PresentationExportSlideQuality,
): AuthoredNativeDispositionSlideReceipt {
  const fallbackById = new Map(
    (slide.fallbackElements ?? []).map((element) => [
      element.elementId,
      cleanReasons(element.reasons),
    ] as const),
  );
  const qids = new Map<string, PresentationExportQidCoverage>();
  const conflicts = new Set<string>();
  for (const coverage of slide.qidCoverage ?? []) {
    const existing = qids.get(coverage.elementId);
    if (existing &&
        (existing.logical !== coverage.logical || existing.kind !== coverage.kind)) {
      conflicts.add(coverage.elementId);
      qids.set(coverage.elementId, { ...coverage, logical: "raster" });
      continue;
    }
    qids.set(coverage.elementId, coverage);
  }

  const elements = [...qids.values()]
    .map((coverage): AuthoredNativeDispositionElementReceipt => ({
      elementId: coverage.elementId,
      kind: coverage.kind,
      disposition:
        coverage.logical === "native" && !conflicts.has(coverage.elementId)
          ? "native"
          : "rasterFallback",
      fallbackReasons: cleanReasons([
        ...(fallbackById.get(coverage.elementId) ?? []),
        ...(conflicts.has(coverage.elementId)
          ? ["native-raster-owner-conflict"]
          : []),
      ]),
    }))
    .sort((left, right) => left.elementId.localeCompare(right.elementId));

  const nativeByKind = emptyCounts();
  const rasterFallbackByKind = emptyCounts();
  if (elements.length) {
    for (const element of elements) {
      const target = element.disposition === "native"
        ? nativeByKind
        : rasterFallbackByKind;
      target[element.kind] += 1;
    }
  } else {
    Object.assign(nativeByKind, aggregateNativeCounts(slide));
  }

  const nativeElements = total(nativeByKind);
  const rasterFallbackElements = elements.length
    ? total(rasterFallbackByKind)
    : Math.max(
        slide.imageFallback ? 1 : 0,
        Math.max(0, Math.trunc(slide.rasterFallbackElements)),
      );
  const totalSemanticElements = nativeElements + rasterFallbackElements;
  const nativeRatioBasisPoints = ratioBasisPoints(
    nativeElements,
    totalSemanticElements,
  );
  let fallbackReasonCounts = reasonCounts(
    slide.fallbackElements,
    slide.fallbackReasons,
  );
  for (const _elementId of conflicts) {
    fallbackReasonCounts = addReason(
      fallbackReasonCounts,
      "native-raster-owner-conflict",
    );
  }

  return {
    schemaVersion: AUTHORED_NATIVE_DISPOSITION_RECEIPT_SCHEMA_VERSION,
    status: conflicts.size ? "failed" : "passed",
    slideNumber: slide.slideNumber,
    nativeElements,
    rasterFallbackElements,
    totalSemanticElements,
    nativeRatioBasisPoints,
    nativeRatio: nativeRatioBasisPoints / 10_000,
    nativeByKind,
    rasterFallbackByKind,
    fallbackReasonCounts,
    conflictingElementIds: [...conflicts].sort(),
    elements,
  };
}

export function createAuthoredNativeDispositionReceipt(
  slides: readonly PresentationExportSlideQuality[],
): AuthoredNativeDispositionReceipt {
  const slideReceipts = slides
    .map(createAuthoredNativeDispositionSlideReceipt)
    .sort((left, right) => left.slideNumber - right.slideNumber);
  const nativeByKind = emptyCounts();
  const rasterFallbackByKind = emptyCounts();
  const fallbackReasonCounts = new Map<string, number>();
  for (const slide of slideReceipts) {
    for (const kind of KINDS) {
      nativeByKind[kind] += slide.nativeByKind[kind];
      rasterFallbackByKind[kind] += slide.rasterFallbackByKind[kind];
    }
    for (const [reason, count] of Object.entries(slide.fallbackReasonCounts)) {
      fallbackReasonCounts.set(
        reason,
        (fallbackReasonCounts.get(reason) ?? 0) + count,
      );
    }
  }
  const nativeElements = slideReceipts.reduce(
    (sum, slide) => sum + slide.nativeElements,
    0,
  );
  const rasterFallbackElements = slideReceipts.reduce(
    (sum, slide) => sum + slide.rasterFallbackElements,
    0,
  );
  const totalSemanticElements = nativeElements + rasterFallbackElements;
  const nativeRatioBasisPoints = ratioBasisPoints(
    nativeElements,
    totalSemanticElements,
  );
  const conflictingElementIds = slideReceipts.flatMap((slide) =>
    slide.conflictingElementIds.map((elementId) =>
      `slide:${slide.slideNumber}:${elementId}`,
    ),
  );

  return {
    schemaVersion: AUTHORED_NATIVE_DISPOSITION_RECEIPT_SCHEMA_VERSION,
    status: slideReceipts.every((slide) => slide.status === "passed")
      ? "passed"
      : "failed",
    nativeElements,
    rasterFallbackElements,
    totalSemanticElements,
    nativeRatioBasisPoints,
    nativeRatio: nativeRatioBasisPoints / 10_000,
    nativeByKind,
    rasterFallbackByKind,
    fallbackReasonCounts: Object.fromEntries(
      [...fallbackReasonCounts.entries()].sort(([left], [right]) =>
        left.localeCompare(right),
      ),
    ),
    conflictingElementIds,
    slides: slideReceipts,
  };
}

export function attachAuthoredNativeDispositionReceipt<
  T extends PresentationExportQualityReport,
>(quality: T): WithAuthoredNativeDisposition<T> {
  const nativeDisposition = createAuthoredNativeDispositionReceipt(
    quality.slides,
  );
  const bySlide = new Map(
    nativeDisposition.slides.map((slide) => [slide.slideNumber, slide] as const),
  );
  return {
    ...quality,
    nativeDisposition,
    slides: quality.slides.map((slide) => ({
      ...slide,
      nativeDisposition: bySlide.get(slide.slideNumber)!,
    })),
  } as WithAuthoredNativeDisposition<T>;
}
