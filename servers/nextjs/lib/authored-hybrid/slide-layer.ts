import { createHash } from "node:crypto";

import {
  extractDeclaredAuthoredSlideDom,
  extractAuthoredSlideDom,
  renderAuthoredBackplate,
  renderAuthoredFidelityBackplate,
  renderAuthoredRasterPaintBand,
} from "./index.ts";
import {
  preflightNativeTypography,
  flattenPreparedNativeElements,
  nestPreparedNativeGroups,
  planLayerSafeNativeElements,
  connectorTextClearanceViolations,
  preparedNativeElementNonVisualIdCount,
  prepareNativeElements,
  prepareStructuralNativeGroups,
  serializePreparedNativeElement,
  type LayerSafetyPlan,
  type PowerPointTypefaceSerializationOptions,
  type PreparedNativeElement,
} from "./native-plan.ts";
import type { AuthoredEmbeddedTypefaceFaceEvidence } from "./font-policy.ts";
import {
  isCanvasBackgroundShape,
  isOpaqueRgbPng,
  preparedNativeElementExpectedReadbackBounds,
  type AuthoredHybridSlideLayer,
} from "./pptx-assembler.ts";
import { preflightAuthoredHtmlForHybrid } from "./security.ts";
import { getGoogleFontsCollector } from "./google-fonts-runtime.ts";
import { mergePowerPointTextLayout } from "./font-layout.ts";
import type {
  PresentationExportFallbackElement,
  PresentationExportSlideQuality,
} from "../presentation-export-quality.ts";
import type { AuthoredHybridSlideV1 } from "./schema.ts";
import {
  fallbackElementForContractElement,
  imageFallbackSlide,
} from "./export-slide-quality.ts";
import {
  buildBrowserObservationReceiptV1,
  digestObservedDeclaredAssets,
  type DeclaredSemanticElementForObservation,
} from "./semantic-reconciliation.ts";
import { prepareDeclaredNativeCharts } from "./native-chart.ts";
import { prepareDeclaredNativeTables } from "./native-table.ts";

// The collector adds a bounded network step ahead of each browser pass, so
// the per-pass ceiling is higher than the pre-collection 20s.
const HYBRID_BROWSER_PASS_TIMEOUT_MS = 45_000;

export interface PreparedSlideOutcome {
  layer: AuthoredHybridSlideLayer | null;
  quality: PresentationExportSlideQuality;
  fontRendering: {
    browserFontFilesCollected: number;
    browserCollectionFailures: number;
  };
  paintPlan?: {
    readonly schemaVersion: "hybrid-paint-plan/browser-v1";
    readonly strategy: "single-backplate-fixed-point" | "audited-multi-z-band";
    readonly fixedPointReached: true;
    readonly closureIterations: number;
    readonly closureDigest: string;
    readonly slideDemotedToRaster: boolean;
    readonly downgradeReceipt: {
      readonly schemaVersion: "hybrid-layer-downgrade-receipt/browser-v1";
      readonly state: "closed" | "slideRaster";
      readonly reason:
        | "no-closed-native-z-band"
        | "backplate-identity-mismatch"
        | "opaque-backplate-collision"
        | null;
      readonly demotedElementIds: readonly string[];
      readonly receiptDigest: string;
    };
    readonly demotions: ReadonlyArray<{
      readonly elementId: string;
      readonly fallbackReason: "z-band-unsafe";
      readonly blockedByElementIds: readonly string[];
    }>;
    readonly bands?: ReadonlyArray<{
      readonly zBand: number;
      readonly paintOwner: "native" | "rasterIsland";
      readonly elementIds: readonly string[];
      readonly artifactDigest?: string;
    }>;
  };
}

export interface AuthoredSemanticSlideContext {
  readonly exportAttemptId: string;
  readonly inputBundleDigest: string;
  readonly rendererSourceDigest: string;
  readonly fontSetDigest: string;
  readonly declaredElementIds: readonly string[];
  readonly declaredElements: readonly DeclaredSemanticElementForObservation[];
}

function canonical(value: unknown): string {
  if (value === null || typeof value !== "object") return JSON.stringify(value);
  if (Array.isArray(value)) return `[${value.map(canonical).join(",")}]`;
  const record = value as Record<string, unknown>;
  return `{${Object.keys(record).sort().map((key) =>
    `${JSON.stringify(key)}:${canonical(record[key])}`
  ).join(",")}}`;
}

function semanticDigest(value: unknown): string {
  return `sha256:${createHash("sha256").update(canonical(value), "utf8").digest("hex")}`;
}

function normalizeDigest(value: string): string {
  return value.startsWith("sha256:") ? value : `sha256:${value}`;
}

export function semanticRasterDegradationDigest(
  context: Pick<AuthoredSemanticSlideContext, "inputBundleDigest" | "rendererSourceDigest">,
  slideNumber: number,
  reason: string,
): string {
  return semanticDigest({
    schemaVersion: "hybrid-raster-degradation-receipt/browser-v1",
    inputBundleDigest: context.inputBundleDigest,
    rendererSourceDigest: normalizeDigest(context.rendererSourceDigest),
    slideNumber,
    state: "degradedFidelityOnly",
    reason,
  });
}

export function semanticRasterOwnerQids(
  fallbackElements: readonly Pick<PresentationExportFallbackElement, "elementId">[],
  nativeElementIds: ReadonlySet<string>,
): string[] {
  return fallbackElements
    .map((element) => element.elementId)
    .filter((elementId) => !nativeElementIds.has(elementId))
    .sort();
}

export interface PreparedFidelitySlideOutcome {
  layer: AuthoredHybridSlideLayer | null;
  fontRendering: {
    browserFontFilesCollected: number;
    browserCollectionFailures: number;
  };
}

function preSerializePreparedElements(
  elements: readonly PreparedNativeElement[],
  options: PowerPointTypefaceSerializationOptions = {}
): PreparedNativeElement[] {
  const serializable: PreparedNativeElement[] = [];
  for (const element of elements) {
    try {
      // A paint-less shape produces no OOXML object. Keeping it in the
      // promoted set would overstate native coverage and make the persisted
      // manifest disagree with PowerPoint readback.
      if (preparedNativeElementNonVisualIdCount(element) === 0) continue;
      serializePreparedNativeElement(
        element,
        serializable.length + 3,
        element.kind === "image" || element.kind === "chart"
          ? "rId999"
          : undefined,
        options
      );
      serializable.push(element);
    } catch (error) {
      // Never hide an element whose final OOXML cannot be constructed.
      console.warn(
        "[authored-hybrid] native pre-serialization rejected",
        { elementId: element.source.id, kind: element.kind, error },
      );
    }
  }
  return serializable;
}

function sameIds(
  elements: readonly PreparedNativeElement[],
  ids: readonly string[]
): boolean {
  return (
    elements.length === ids.length &&
    elements.every((element, index) => element.source.id === ids[index])
  );
}

function backplatePromotedElementIds(
  elements: readonly PreparedNativeElement[],
): string[] {
  // Structural groups are paintless semantic owners. Their native children are
  // suppressed from the residual raster, while the group itself has no pixels
  // to remove and is intentionally absent from the extractor's promotable set.
  return elements
    .filter((element) => element.kind !== "group")
    .map((element) => element.source.id);
}

function withAppliedStructuralGroups(
  elements: readonly PreparedNativeElement[],
  appliedElementIds: readonly string[],
): Set<string> {
  return new Set([
    ...appliedElementIds,
    ...elements
      .filter((element) => element.kind === "group")
      .map((element) => element.source.id),
  ]);
}

function withAdmittedSemanticChartEligibility(
  contract: AuthoredHybridSlideV1,
  selected: readonly PreparedNativeElement[],
  semanticContext?: AuthoredSemanticSlideContext,
): AuthoredHybridSlideV1 {
  if (!semanticContext || !selected.some((element) => element.kind === "chart" || element.kind === "table")) {
    return contract;
  }
  const declaredCharts = new Set(
    semanticContext.declaredElements
      .filter((element) => element.kind === "chart")
      .map((element) => element.elementId),
  );
  const admittedChartIds = selected
    .filter(
      (element) => element.kind === "chart" && declaredCharts.has(element.source.id),
    )
    .map((element) => element.source.id);
  const declaredTables = new Set(semanticContext.declaredElements.filter((element) => element.kind === "table").map((element) => element.elementId));
  const admittedTableIds = selected.filter((element) => element.kind === "table" && declaredTables.has(element.source.id)).map((element) => element.source.id);
  if (!admittedChartIds.length && !admittedTableIds.length) return contract;
  return {
    ...contract,
    backplate: {
      ...contract.backplate,
      nativeChartElementIds: admittedChartIds,
      nativeTableElementIds: admittedTableIds,
      eligibleElementIds: [
        ...new Set([
          ...contract.backplate.eligibleElementIds,
          ...admittedChartIds,
          ...admittedTableIds,
        ]),
      ],
    },
  };
}

function slideRasterPaintPlan(
  plan: LayerSafetyPlan,
  reason: "backplate-identity-mismatch" | "opaque-backplate-collision",
): NonNullable<PreparedSlideOutcome["paintPlan"]> {
  const downgradeUnsigned = {
    schemaVersion: "hybrid-layer-downgrade-receipt/browser-v1" as const,
    state: "slideRaster" as const,
    reason,
    demotedElementIds: plan.selected
      .map((element) => element.source.id)
      .concat(plan.demotions.map((item) => item.elementId))
      .sort(),
  };
  return {
    schemaVersion: "hybrid-paint-plan/browser-v1",
    strategy: plan.strategy,
    fixedPointReached: true,
    closureIterations: plan.closureIterations,
    closureDigest: plan.closureDigest,
    slideDemotedToRaster: true,
    downgradeReceipt: {
      ...downgradeUnsigned,
      receiptDigest: semanticDigest(downgradeUnsigned),
    },
    demotions: plan.demotions.map((demotion) => ({
      elementId: demotion.elementId,
      fallbackReason: demotion.reason,
      blockedByElementIds: demotion.blockedByElementIds,
    })),
  };
}

export async function prepareSlideLayer(
  html: string,
  slideNumber: number,
  chromeExecutable: string,
  embeddedTypefaceFamilies: readonly string[] = [],
  embeddedTypefaceFaces: readonly AuthoredEmbeddedTypefaceFaceEvidence[] = [],
  semanticContext?: AuthoredSemanticSlideContext,
  embeddedGlyphAvailable?: (
    typeface: string,
    face: AuthoredEmbeddedTypefaceFaceEvidence["face"],
    codePoint: number,
  ) => boolean,
): Promise<PreparedSlideOutcome> {
  // Network access is permitted only in this validated server-side collector.
  // The resulting document is self-contained before isolated Chrome receives it.
  // The collector returns two documents on purpose. Only the sanitized form is
  // preflighted, so untrusted authored HTML can never self-authorize a data
  // font face; the verified form carrying collector-built @font-face rules is
  // used only after that preflight succeeds.
  const fontCollection = await getGoogleFontsCollector().prepareHtml(html);
  const fontRendering = {
    browserFontFilesCollected: fontCollection.fontFiles,
    browserCollectionFailures: fontCollection.failures.length,
  };
  const preflight = preflightAuthoredHtmlForHybrid(
    fontCollection.htmlForPreflight
  );
  if (!preflight.ok) {
    return {
      layer: null,
      quality: imageFallbackSlide(
        slideNumber,
        preflight.reason ?? "html-preflight-failed"
      ),
      fontRendering,
    };
  }
  const collectedHtml = fontCollection.htmlForChrome;
  const extractionOptions = {
    chromeExecutable,
    timeoutMs: HYBRID_BROWSER_PASS_TIMEOUT_MS,
    ...(semanticContext
      ? { declaredElementIds: semanticContext.declaredElementIds }
      : {}),
  };
  const declaredCapture = semanticContext
    ? await extractDeclaredAuthoredSlideDom(
        collectedHtml,
        semanticContext.declaredElementIds,
        extractionOptions,
      )
    : undefined;
  const contract = declaredCapture?.slide ??
    await extractAuthoredSlideDom(collectedHtml, extractionOptions);
  const observationReceipt =
    semanticContext && declaredCapture
      ? buildBrowserObservationReceiptV1(
          semanticContext.declaredElements,
          declaredCapture.observation,
          {
            inputBundleDigest: semanticContext.inputBundleDigest,
            rendererSourceDigest: normalizeDigest(semanticContext.rendererSourceDigest),
            rendererVersion: "presenton-authored-chromium/v1",
            policyVersion: "authored-declared-observation/v1",
            fontSetDigest: semanticContext.fontSetDigest,
            capturedPngDigest: declaredCapture.capturedPngSha256,
            observedAssetContentDigests: digestObservedDeclaredAssets(
              semanticContext.declaredElements,
              declaredCapture.observation,
            ),
          },
        )
      : undefined;
  let nativeLayoutElements = contract.elements;
  try {
    const layoutContract = await extractAuthoredSlideDom(collectedHtml, {
      ...extractionOptions,
      fontLayoutMode: "powerpoint",
    });
    const merged = mergePowerPointTextLayout(
      contract.elements,
      layoutContract.elements,
      { embeddedTypefaceFamilies, embeddedTypefaceFaces }
    );
    nativeLayoutElements = merged.elements;
  } catch {
    // The mapped-font pass is an accuracy enhancement. Source extraction and
    // fidelity backplate remain a safe compatible fallback if it cannot run.
  }
  const typographyPreflight = preflightNativeTypography(nativeLayoutElements, {
    embeddedTypefaceFaces,
    embeddedGlyphAvailable,
    backplateElements: contract.elements,
  });
  nativeLayoutElements = typographyPreflight.elements;
  const typographyRejected = new Set(typographyPreflight.rejectedElementIds);
  const declaredChartPreparation = semanticContext && declaredCapture
    ? prepareDeclaredNativeCharts(
        semanticContext.declaredElements,
        declaredCapture.observation.elements,
      )
    : {
        charts: [],
        rejections: new Map<
          string,
          "unsupported-native-chart" | "unsupported-chart-css"
        >(),
      };
  const declaredTablePreparation = semanticContext && declaredCapture
    ? prepareDeclaredNativeTables(semanticContext.declaredElements, declaredCapture.observation.elements)
    : { tables: [], rejections: new Map<string, "unsupported-native-table" | "unsupported-table-css" | "table-observation-mismatch">() };
  const declaredPreparedElements: PreparedNativeElement[] = [
    ...declaredChartPreparation.charts.map((source) => ({
      kind: "chart" as const,
      source,
    })),
    ...declaredTablePreparation.tables.map((source) => ({
      kind: "table" as const,
      source,
    })),
  ];
  const preparedLeaves = preSerializePreparedElements(
    [
      ...(
        await prepareNativeElements(nativeLayoutElements, {
          includeRasterText: true,
          includeRasterShapes: true,
        })
      ).filter((element) => !typographyRejected.has(element.source.id)),
      ...declaredPreparedElements,
    ],
    { embeddedTypefaceFamilies, embeddedTypefaceFaces }
  );
  const preparedBeforeConnectorGate = preparedLeaves.concat(
    prepareStructuralNativeGroups(
      nativeLayoutElements,
      preparedLeaves,
      new Set(
        semanticContext?.declaredElements
          .filter(
            (element) =>
              element.kind === "group" ||
              (element.kind === "shape" && (element.childIds?.length ?? 0) > 0),
          )
          .map((element) => element.elementId) ?? [],
      ),
    )
  );
  const connectorClearanceViolations = connectorTextClearanceViolations(
    preparedBeforeConnectorGate,
    nativeLayoutElements,
    new Set(
      semanticContext?.declaredElements
        .filter((element) => element.kind === "connector")
        .map((element) => element.elementId) ?? [],
    ),
  );
  const connectorClearanceRejected = new Set(
    connectorClearanceViolations.map((violation) => violation.elementId),
  );
  const prepared = preparedBeforeConnectorGate.filter(
    (element) => !connectorClearanceRejected.has(element.source.id),
  );
  const closeStructuralGroups = (
    candidates: readonly PreparedNativeElement[]
  ): PreparedNativeElement[] =>
    flattenPreparedNativeElements(nestPreparedNativeGroups(candidates));
  let layerSafetyPlan = planLayerSafeNativeElements(
    nativeLayoutElements,
    prepared,
    undefined,
    // The current serializer has one residual backplate. Promote text or shape
    // only when every overlapping higher paint can also be native; otherwise
    // fixed-point demotion preserves the authored stack without inventing a
    // z-band that this serializer cannot safely encode.
    {
      promoteTextAboveRaster: false,
      promoteShapesAboveRaster: false,
      retainedChildPaint: "slide-root",
    }
  );
  let selected = closeStructuralGroups(layerSafetyPlan.selected);
  if (!selected.length) {
    return {
      layer: null,
      quality: imageFallbackSlide(
        slideNumber,
        layerSafetyPlan.demotions.length ? "z-band-unsafe" : "no-safe-native-elements"
      ),
      fontRendering,
      paintPlan: {
        schemaVersion: "hybrid-paint-plan/browser-v1",
        strategy: layerSafetyPlan.strategy,
        fixedPointReached: true,
        closureIterations: layerSafetyPlan.closureIterations,
        closureDigest: layerSafetyPlan.closureDigest,
        slideDemotedToRaster: true,
        downgradeReceipt: layerSafetyPlan.downgradeReceipt,
        demotions: layerSafetyPlan.demotions.map((demotion) => ({
          elementId: demotion.elementId,
          fallbackReason: demotion.reason,
          blockedByElementIds: demotion.blockedByElementIds,
        })),
      },
    };
  }

  let backplate = await renderAuthoredBackplate(
    collectedHtml,
    withAdmittedSemanticChartEligibility(contract, selected, semanticContext),
    backplatePromotedElementIds(selected),
    extractionOptions
  );
  const applied = withAppliedStructuralGroups(
    selected,
    backplate.appliedPromotedElementIds,
  );
  const finalLayerSafetyPlan = planLayerSafeNativeElements(
    nativeLayoutElements,
    prepared,
    applied,
    {
      promoteTextAboveRaster: false,
      promoteShapesAboveRaster: false,
      retainedChildPaint: "slide-root",
    }
  );
  const finalSelected = closeStructuralGroups(finalLayerSafetyPlan.selected);
  layerSafetyPlan = finalLayerSafetyPlan;
  if (!finalSelected.length) {
    return {
      layer: null,
      quality: imageFallbackSlide(
        slideNumber,
        finalLayerSafetyPlan.demotions.length
          ? "z-band-unsafe"
          : "backplate-identity-mismatch"
      ),
      fontRendering,
      paintPlan: {
        schemaVersion: "hybrid-paint-plan/browser-v1",
        strategy: finalLayerSafetyPlan.strategy,
        fixedPointReached: true,
        closureIterations: finalLayerSafetyPlan.closureIterations,
        closureDigest: finalLayerSafetyPlan.closureDigest,
        slideDemotedToRaster: true,
        downgradeReceipt: finalLayerSafetyPlan.downgradeReceipt,
        demotions: finalLayerSafetyPlan.demotions.map((demotion) => ({
          elementId: demotion.elementId,
          fallbackReason: demotion.reason,
          blockedByElementIds: demotion.blockedByElementIds,
        })),
      },
    };
  }

  if (!sameIds(
    finalSelected.filter((element) => element.kind !== "group"),
    backplate.appliedPromotedElementIds,
  )) {
    selected = finalSelected;
    backplate = await renderAuthoredBackplate(
      collectedHtml,
      withAdmittedSemanticChartEligibility(contract, selected, semanticContext),
      backplatePromotedElementIds(selected),
      extractionOptions
    );
    if (!sameIds(
      selected.filter((element) => element.kind !== "group"),
      backplate.appliedPromotedElementIds,
    )) {
      return {
        layer: null,
        quality: imageFallbackSlide(slideNumber, "backplate-identity-mismatch"),
        fontRendering,
        paintPlan: slideRasterPaintPlan(
          layerSafetyPlan,
          "backplate-identity-mismatch",
        ),
      };
    }
  } else {
    selected = finalSelected;
  }

  // An opaque residual is serialized as the PowerPoint slide background. A
  // full-canvas page-colour shape cannot sit beneath that background, so the
  // assembler intentionally omits it. Keep the coverage contract honest by
  // leaving that shape in the raster residual instead of claiming a native
  // object that PowerPoint cannot read back.
  if (
    layerSafetyPlan.strategy === "single-backplate-fixed-point" &&
    isOpaqueRgbPng(backplate.backplatePng)
  ) {
    const withoutCanvasBackground = selected.filter(
      (element) => !isCanvasBackgroundShape(element)
    );
    if (withoutCanvasBackground.length !== selected.length) {
      selected = closeStructuralGroups(withoutCanvasBackground);
      if (!selected.length) {
        return {
          layer: null,
          quality: imageFallbackSlide(slideNumber, "opaque-backplate-collision"),
          fontRendering,
          paintPlan: slideRasterPaintPlan(
            layerSafetyPlan,
            "opaque-backplate-collision",
          ),
        };
      }
      backplate = await renderAuthoredBackplate(
        collectedHtml,
        withAdmittedSemanticChartEligibility(contract, selected, semanticContext),
        backplatePromotedElementIds(selected),
        extractionOptions
      );
      if (!sameIds(
        selected.filter((element) => element.kind !== "group"),
        backplate.appliedPromotedElementIds,
      )) {
        return {
          layer: null,
          quality: imageFallbackSlide(slideNumber, "backplate-identity-mismatch"),
          fontRendering,
          paintPlan: slideRasterPaintPlan(
            layerSafetyPlan,
            "backplate-identity-mismatch",
          ),
        };
      }
    }
  }
  const selectedIds = new Set(
    selected.map((element) => element.source.id)
  );
  const rasterPaintBands = layerSafetyPlan.strategy === "audited-multi-z-band"
    ? await Promise.all(
        layerSafetyPlan.paintBands
          .filter((band) => band.paintOwner === "rasterIsland")
          .map(async (band) => {
            const capture = await renderAuthoredRasterPaintBand(
              collectedHtml,
              contract,
              band.elementIds,
              extractionOptions,
            );
            return {
              zBand: band.zBand,
              rasterPng: capture.rasterPng,
              elementIds: band.elementIds,
              artifactDigest: capture.capturePngSha256,
            };
          }),
      )
    : [];
  const zBandByElementId = new Map(
    layerSafetyPlan.paintBands.flatMap((band) =>
      band.elementIds.map((elementId) => [elementId, band.zBand] as const)
    ),
  );
  const selectedKindById = new Map(
    selected.map((element) => [
      element.source.id,
      element.kind === "group" ? "shape" as const : element.kind,
    ] as const),
  );
  const zBandDemotions = new Set(
    layerSafetyPlan.demotions.map((demotion) => demotion.elementId)
  );
  const fallbackElements = nativeLayoutElements
    // Fallback evidence is semantic-owner scoped. An admitted element may
    // retain audited paint fragments on the residual backplate, but it cannot
    // also be counted as a raster owner without inflating the denominator and
    // contradicting its native QID disposition.
    .filter((element) => !selectedIds.has(element.id))
    .map((element) =>
      fallbackElementForContractElement(
        element,
        [
          ...(zBandDemotions.has(element.id) ? ["z-band-unsafe"] : []),
          ...(connectorClearanceRejected.has(element.id)
            ? ["connector-text-clearance" as const]
            : []),
          ...(declaredChartPreparation.rejections.has(element.id)
            ? [declaredChartPreparation.rejections.get(element.id)!]
            : []),
          ...(declaredTablePreparation.rejections.has(element.id)
            ? [declaredTablePreparation.rejections.get(element.id)!]
            : []),
        ]
      )
    );
  if (semanticContext) {
    const declaredIds = semanticContext.declaredElements
      .map((element) => element.elementId)
      .sort();
    const ownerIds = [
      ...selectedIds,
      ...fallbackElements.map((element) => element.elementId),
    ].sort();
    if (
      new Set(ownerIds).size !== ownerIds.length ||
      JSON.stringify(ownerIds) !== JSON.stringify(declaredIds)
    ) {
      throw new Error("authored_hybrid_qid_owner_exact_once_mismatch");
    }
  }
  const fallbackKindById = new Map(
    fallbackElements.map((element) => [element.elementId, element.candidateKind] as const),
  );
  const semanticReceipts = semanticContext && observationReceipt
    ? (() => {
        const fallbackById = new Map(
          fallbackElements.map((element) => [element.elementId, element.reasons] as const),
        );
        const admissionUnsigned = {
          schemaVersion: "native-admission-receipt/v1" as const,
          inputBundleDigest: semanticContext.inputBundleDigest,
          observationReceiptDigest: observationReceipt.receiptDigest,
          reconciliationPolicyVersion: "authored-native-admission/v1",
          serializerId: "presenton-authored-hybrid",
          serializerVersion: "v1",
          elements: semanticContext.declaredElements.map((element) => {
            const admitted = selectedIds.has(element.elementId);
            return {
              elementId: element.elementId,
              kind: element.kind,
              state: admitted ? "admitted" as const : "rasterFallback" as const,
              admittedFeatures: admitted
                ? [...(element.requiredFeatures ?? [])].sort()
                : [],
              fallbackReason: admitted
                ? null
                : fallbackById.get(element.elementId)?.[0] ?? "native-admission-rejected",
            };
          }),
        };
        const nativeAdmissionReceiptDigest = semanticDigest(admissionUnsigned);
        const paintEntries = [
          ...(rasterPaintBands.length
            ? rasterPaintBands.map((band) => ({
                elementId: null,
                paintOwner: "rasterIsland" as const,
                zBand: band.zBand,
                artifactDigest: band.artifactDigest,
                fallbackReason: band.elementIds.length ? "raster-residual" : null,
              }))
            : [{
                elementId: null,
                paintOwner: "rasterIsland" as const,
                zBand: 0,
                artifactDigest: `sha256:${createHash("sha256")
                  .update(backplate.backplatePng)
                  .digest("hex")}`,
                fallbackReason: fallbackElements.length ? "raster-residual" : null,
              }]),
          ...selected
            .slice()
            .sort((left, right) => left.source.zOrder - right.source.zOrder)
            .map((element, index) => ({
              elementId: element.source.id,
              paintOwner: "native" as const,
              zBand: zBandByElementId.get(element.source.id) ?? index + 1,
              artifactDigest: null,
              fallbackReason: null,
            })),
        ];
        const planUnsigned = {
          schemaVersion: "hybrid-export-plan/v1" as const,
          exportAttemptId: semanticContext.exportAttemptId,
          inputBundleDigest: semanticContext.inputBundleDigest,
          admissionReceiptDigest: nativeAdmissionReceiptDigest,
          rendererSourceDigest: normalizeDigest(semanticContext.rendererSourceDigest),
          paintEntries,
          fixedPointReached: true,
          slideDemotedToRaster: false,
        };
        return {
          observationReceiptDigest: observationReceipt.receiptDigest,
          nativeAdmissionReceiptDigest,
          hybridPlanDigest: semanticDigest(planUnsigned),
        };
      })()
    : undefined;

  return {
    layer: {
      slideNumber,
      backplatePng: backplate.backplatePng,
      elements: nestPreparedNativeGroups(selected),
      ...(rasterPaintBands.length
        ? {
            rasterBands: rasterPaintBands.map((band) => ({
              zBand: band.zBand,
              rasterPng: band.rasterPng,
              semanticOwnerQids: band.elementIds.filter(
                (elementId) => !selectedIds.has(elementId),
              ),
            })),
            nativeZBandByElementId: Object.fromEntries(
              layerSafetyPlan.paintBands
                .filter((band) => band.paintOwner === "native")
                .flatMap((band) =>
                  band.elementIds.map((elementId) => [elementId, band.zBand] as const)
                ),
            ),
          }
        : {}),
      ...(semanticContext
        ? {
            // The residual raster owns only demoted semantic owners. Admitted
            // native QIDs are carried by their native object and must never be
            // repeated on the raster plane as a second logical owner. A native
            // element can still retain backplate-only paint telemetry (for
            // example a shadow), but that visual fragment does not become a
            // second logical QID owner.
            semanticOwnerQids: semanticRasterOwnerQids(
              fallbackElements,
              selectedIds,
            ),
          }
        : {}),
    },
    quality: {
      slideNumber,
      editable: selected.length > 0,
      imageFallback: selected.length === 0,
      nativeTextElements: selected.filter((element) => element.kind === "text")
        .length,
      nativeShapeElements: selected.filter(
        (element) => element.kind === "shape" || element.kind === "group",
      ).length,
      nativeChartElements: selected.filter((element) => element.kind === "chart")
        .length,
      nativeTableElements: selected.filter((element) => element.kind === "table").length,
      nativeGroupElements: selected.filter((element) => element.kind === "group")
        .length,
      nativeImageElements: selected.filter((element) => element.kind === "image")
        .length,
      rasterFallbackElements: fallbackElements.length,
      logicalCoverage: {
        nativeText: selected.filter((element) => element.kind === "text").length,
        nativeImage: selected.filter((element) => element.kind === "image").length,
        nativeShape: selected.filter(
          (element) => element.kind === "shape" || element.kind === "group",
        ).length,
        nativeChart: selected.filter((element) => element.kind === "chart").length,
        nativeGroup: selected.filter((element) => element.kind === "group").length,
        rasterRemaining: fallbackElements.length,
      },
      ooxmlReadback: {
        status: "not-run",
        nativeText: 0,
        nativeImage: 0,
        nativeShape: 0,
        nativeChart: 0,
        nativeGroup: 0,
        rasterRemaining: 0,
      },
      backplateCount: rasterPaintBands.length || 1,
      typographyIssues: typographyPreflight.typographyIssues.map((issue) => ({
        code: issue.code,
        elementId: issue.elementId,
      })),
      fallbackReasons: fallbackElements.flatMap((element) => element.reasons),
      fallbackElements,
      qidCoverage: semanticContext?.declaredElements.map((element) => {
        const native = selectedIds.has(element.elementId);
        // The sealed semantic kind describes source intent. A bounded native
        // serializer can admit a raster-declared complex island as an actual
        // text/image/shape object; OOXML ownership must record that emitted
        // kind rather than coercing every complex island to shape. Raster
        // carriers remain non-visual shapes by contract.
        const fallbackKind = fallbackKindById.get(element.elementId);
        const kind = native
          ? selectedKindById.get(element.elementId)!
          : fallbackKind === "text" ||
              fallbackKind === "image" ||
              fallbackKind === "shape" ||
              fallbackKind === "table" ||
              fallbackKind === "chart"
            ? fallbackKind
            : element.kind === "connector" ||
                element.kind === "group" ||
                element.kind === "complexIsland"
              ? "shape" as const
              : element.kind;
        return { elementId: element.elementId, kind, logical: native ? "native" as const : "raster" as const, ooxml: "not-run" as const, com: "not-run" as const };
      }),
      geometryContract: (() => {
        const elements = selected.map((element) => {
          // Seal the exact assembler bounds, including the bounded standalone
          // text slack, so OOXML readback checks the emitted line-height and
          // overflow contract rather than a pre-serialization DOM box.
          const bounds = preparedNativeElementExpectedReadbackBounds(element);
          return {
            elementId: element.source.id,
            kind: element.kind === "group" ? "shape" as const : element.kind,
            x: bounds.x,
            y: bounds.y,
            width: bounds.width,
            height: bounds.height,
          };
        }).sort((left, right) => left.elementId.localeCompare(right.elementId));
        return {
          schemaVersion: "presenton.hybrid-geometry/v1" as const,
          chromiumDigest: semanticDigest({
            schemaVersion: "presenton.hybrid-geometry/v1",
            slideNumber,
            elements,
          }),
          elements,
        };
      })(),
      ...(semanticReceipts ? { semanticReceipts } : {}),
    },
    fontRendering,
    paintPlan: {
      schemaVersion: "hybrid-paint-plan/browser-v1",
      strategy: layerSafetyPlan.strategy,
      fixedPointReached: layerSafetyPlan.fixedPointReached,
      closureIterations: layerSafetyPlan.closureIterations,
      closureDigest: layerSafetyPlan.closureDigest,
      slideDemotedToRaster: layerSafetyPlan.slideDemotedToRaster,
      downgradeReceipt: layerSafetyPlan.downgradeReceipt,
      demotions: layerSafetyPlan.demotions.map((demotion) => ({
        elementId: demotion.elementId,
        fallbackReason: demotion.reason,
        blockedByElementIds: demotion.blockedByElementIds,
      })),
      bands: layerSafetyPlan.paintBands.map((band) => {
        const raster = rasterPaintBands.find((item) => item.zBand === band.zBand);
        return {
          ...band,
          ...(raster ? { artifactDigest: raster.artifactDigest } : {}),
        };
      }),
    },
  };
}

/**
 * Capture the exact authored browser result as a full-slide backplate without
 * promoting any DOM node. Fidelity export uses this path so it shares the
 * revision-sealed renderer with Hybrid while remaining intentionally raster.
 */
export async function prepareFidelitySlideLayer(
  html: string,
  slideNumber: number,
  chromeExecutable: string
): Promise<PreparedFidelitySlideOutcome> {
  const fontCollection = await getGoogleFontsCollector().prepareHtml(html);
  const fontRendering = {
    browserFontFilesCollected: fontCollection.fontFiles,
    browserCollectionFailures: fontCollection.failures.length,
  };
  const preflight = preflightAuthoredHtmlForHybrid(
    fontCollection.htmlForPreflight
  );
  if (!preflight.ok) return { layer: null, fontRendering };

  const extractionOptions = {
    chromeExecutable,
    timeoutMs: HYBRID_BROWSER_PASS_TIMEOUT_MS,
  };
  const backplate = await renderAuthoredFidelityBackplate(
    fontCollection.htmlForChrome,
    extractionOptions
  );
  if (backplate.appliedPromotedElementIds.length !== 0) {
    throw new Error("authored_fidelity_backplate_identity_mismatch");
  }
  return {
    layer: { slideNumber, backplatePng: backplate.backplatePng, elements: [] },
    fontRendering,
  };
}
