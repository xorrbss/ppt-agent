import { createHash } from "node:crypto";

import {
  getFastApiAuthHeaders,
  getFastApiBaseUrl,
} from "../fastapi-internal.ts";

import { readBoundedResponseText } from "./security.ts";
import type { DeclaredSemanticElementForObservation } from "./semantic-reconciliation.ts";
import { parseAuthoredNativeChartPayload } from "./native-chart.ts";
import { parseAuthoredNativeTablePayload } from "./native-table.ts";

const ACTIVE_EMISSION_RESPONSE_BYTES = 24 * 1024 * 1024;
const ACTIVE_EMISSION_TIMEOUT_MS = 20_000;
const DIGEST_PATTERN = /^sha256:[a-f0-9]{64}$/;
const BARE_DIGEST_PATTERN = /^[a-f0-9]{64}$/;
const QID_PATTERN = /^[A-Za-z][A-Za-z0-9._:-]{0,127}$/;

type CanonicalMode = "legacyHtml" | "bundleGraph";
type BundleKind = "authored" | "legacyHtml";

interface OrderedSlideBundleV1 {
  readonly slideId: string;
  readonly slideRevision: number;
  readonly bundleKind: BundleKind;
  readonly bundleDigest: string;
}

interface ParsedManifestV1 {
  readonly presentationId: string;
  readonly presentationRevision: number;
  readonly canonicalDigest: string;
  readonly sceneDigest: string | null;
  readonly orderedSlides: readonly OrderedSlideBundleV1[];
}

interface ParsedBundleV1 {
  readonly bundleKind: BundleKind;
  readonly presentationId: string;
  readonly slideId: string;
  readonly slideRevision: number;
  readonly bundleDigest: string;
  readonly fontManifestDigest: string;
  readonly semanticKind:
    | "authoredSemanticSource"
    | "semanticUnavailable"
    | "compatibilityObservation";
  readonly declaredElementIds: readonly string[];
  readonly declaredElements: readonly DeclaredSemanticElementForObservation[];
  readonly fontFamilies: readonly string[];
}

export interface ActiveAuthoredEmissionResponseV1 {
  readonly schemaVersion: "active-authored-emission-response/v1";
  readonly presentationId: string;
  readonly canonicalMode: CanonicalMode;
  readonly presentationRevision: number;
  readonly activeGeneration: number;
  readonly canonicalDigest: string | null;
  readonly manifest: ParsedManifestV1 | null;
  readonly bundles: readonly ParsedBundleV1[];
}

export interface ActiveEmissionSlideBinding {
  readonly slideId: string;
  readonly slideRevision: number;
  readonly bundleKind: BundleKind;
  readonly bundleDigest: string;
  readonly fontManifestDigest: string;
  readonly semanticKind: ParsedBundleV1["semanticKind"];
  readonly declaredElementIds: readonly string[];
  readonly declaredElements: readonly DeclaredSemanticElementForObservation[];
  readonly fontFamilies: readonly string[];
}

export interface ActiveEmissionExportBinding {
  readonly schemaVersion: "authored-active-export-binding/browser-v1";
  readonly canonicalMode: CanonicalMode;
  readonly presentationRevision: number;
  readonly activeGeneration: number;
  readonly canonicalDigest: string | null;
  readonly sceneDigest: string | null;
  readonly slides: readonly ActiveEmissionSlideBinding[];
}

export interface BindActiveEmissionOptions {
  readonly mode: "fidelity" | "hybrid";
  readonly explicitMode: boolean;
  readonly presentationId: string;
  readonly presentationRevision: number;
  readonly canonicalSourceDigest?: string;
  readonly slideIds: readonly string[];
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function canonical(value: unknown): string {
  if (value === null || typeof value !== "object") return JSON.stringify(value);
  if (Array.isArray(value)) return `[${value.map(canonical).join(",")}]`;
  const record = value as Record<string, unknown>;
  return `{${Object.keys(record).sort().map((key) =>
    `${JSON.stringify(key)}:${canonical(record[key])}`
  ).join(",")}}`;
}

function contentDigest(value: unknown): string {
  return `sha256:${createHash("sha256").update(canonical(value), "utf8").digest("hex")}`;
}

function invalid(): never {
  throw new Error("authored_active_manifest_response_invalid");
}

function exactKeys(
  value: Record<string, unknown>,
  required: readonly string[],
): void {
  const actual = Object.keys(value).sort();
  const expected = [...required].sort();
  if (
    actual.length !== expected.length ||
    actual.some((key, index) => key !== expected[index])
  ) {
    invalid();
  }
}

function stringValue(value: unknown): string {
  if (typeof value !== "string" || !value) invalid();
  return value;
}

function revisionValue(value: unknown, allowZero = false): number {
  if (
    !Number.isSafeInteger(value) ||
    (value as number) < (allowZero ? 0 : 1)
  ) {
    invalid();
  }
  return value as number;
}

function digestValue(value: unknown): string {
  if (typeof value !== "string" || !DIGEST_PATTERN.test(value)) invalid();
  return value;
}

function nullableDigestValue(value: unknown): string | null {
  return value === null ? null : digestValue(value);
}

function qidList(value: unknown): readonly string[] {
  if (!Array.isArray(value) || value.length > 1024) invalid();
  const ids = value.map((item) => {
    if (typeof item !== "string" || !QID_PATTERN.test(item)) invalid();
    return item;
  });
  if (
    new Set(ids).size !== ids.length ||
    ids.some((id, index) => index > 0 && ids[index - 1].localeCompare(id) > 0)
  ) {
    invalid();
  }
  return Object.freeze(ids);
}

function unorderedQidList(value: unknown): readonly string[] {
  if (!Array.isArray(value) || value.length > 1024) invalid();
  const ids = value.map((item) => {
    if (typeof item !== "string" || !QID_PATTERN.test(item)) invalid();
    return item;
  });
  if (new Set(ids).size !== ids.length) invalid();
  return Object.freeze(ids);
}

function parseIdentityRegistry(value: unknown): readonly string[] {
  if (!isRecord(value)) invalid();
  exactKeys(value, [
    "schemaVersion",
    "activeQids",
    "retiredQids",
    "lineageDigest",
    "digest",
  ]);
  if (value.schemaVersion !== "authored-element-identity-registry/v1") invalid();
  const active = qidList(value.activeQids);
  const retired = qidList(value.retiredQids);
  if (active.some((qid) => retired.includes(qid))) invalid();
  nullableDigestValue(value.lineageDigest);
  nullableDigestValue(value.digest);
  return active;
}

function parseArtifactPointer(value: unknown): string {
  if (!isRecord(value)) invalid();
  exactKeys(value, ["schemaVersion", "artifactRef", "digest", "byteLength", "mediaType"]);
  if (
    value.schemaVersion !== "authored-artifact-pointer/v1" ||
    typeof value.artifactRef !== "string" ||
    !/^artifact:\/\/sha256\/[a-f0-9]{64}$/.test(value.artifactRef) ||
    !Number.isSafeInteger(value.byteLength) ||
    (value.byteLength as number) < 0 ||
    typeof value.mediaType !== "string" ||
    !value.mediaType
  ) {
    invalid();
  }
  return digestValue(value.digest);
}

function parseSemanticComponent(value: unknown): {
  semanticKind: ParsedBundleV1["semanticKind"];
  declaredElementIds: readonly string[];
  declaredElements: readonly DeclaredSemanticElementForObservation[];
  fontFamilies: readonly string[];
  presentationId: string;
  slideId: string;
  slideRevision: number;
  operationId: string;
  authoredHtmlDigest: string;
} {
  if (!isRecord(value) || typeof value.componentKind !== "string") invalid();
  if (value.componentKind === "authoredSemanticSource") {
    exactKeys(value, [
      "schemaVersion",
      "componentKind",
      "presentationId",
      "slideId",
      "slideRevision",
      "operationId",
      "origin",
      "authoredHtmlDigest",
      "elements",
      "identityRegistry",
      "digest",
    ]);
    if (
      value.schemaVersion !== "authored-semantic-source/v1" ||
      value.origin !== "authored-declared-v1" ||
      !Array.isArray(value.elements) ||
      value.elements.length === 0 ||
      value.elements.length > 1024
    ) {
      invalid();
    }
    const presentationId = stringValue(value.presentationId);
    const slideId = stringValue(value.slideId);
    const operationId = stringValue(value.operationId);
    const slideRevision = revisionValue(value.slideRevision);
    const authoredHtmlDigest = digestValue(value.authoredHtmlDigest);
    nullableDigestValue(value.digest);
    const active = parseIdentityRegistry(value.identityRegistry);
    const fontFamilies = new Set<string>();
    const declaredElements = value.elements.map((element) => {
      if (
        !isRecord(element) ||
        typeof element.elementId !== "string" ||
        typeof element.kind !== "string" ||
        ![
          "text", "image", "shape", "connector", "group", "table", "chart",
          "complexIsland",
        ].includes(element.kind) ||
        !Array.isArray(element.childIds) ||
        !Array.isArray(element.requiredFeatures)
      ) {
        invalid();
      }
      if (!QID_PATTERN.test(element.elementId)) invalid();
      const parentId = element.parentId;
      if (parentId !== null && (typeof parentId !== "string" || !QID_PATTERN.test(parentId))) {
        invalid();
      }
      const childIds = unorderedQidList(element.childIds);
      const requiredFeatures = element.requiredFeatures.map((feature) =>
        stringValue(feature));
      const assetContentDigest =
        element.kind === "image" &&
        isRecord(element.payload) &&
        typeof element.payload.assetDigest === "string"
          ? digestValue(element.payload.assetDigest)
          : undefined;
      const chartPayload = element.kind === "chart"
        ? parseAuthoredNativeChartPayload(element.payload)
        : null;
      const tablePayload = element.kind === "table"
        ? parseAuthoredNativeTablePayload(element.payload)
        : null;
      if (
        element.kind === "text" &&
        isRecord(element.payload) &&
        Array.isArray(element.payload.paragraphs)
      ) {
        for (const paragraph of element.payload.paragraphs) {
          if (!isRecord(paragraph) || !Array.isArray(paragraph.runs)) continue;
          for (const run of paragraph.runs) {
            if (isRecord(run) && typeof run.fontFamily === "string" && run.fontFamily.trim()) {
              fontFamilies.add(run.fontFamily.trim());
            }
          }
        }
      }
      return Object.freeze({
        elementId: element.elementId,
        kind: element.kind as DeclaredSemanticElementForObservation["kind"],
        parentId: parentId as string | null,
        childIds,
        sourceDigest: contentDigest(element),
        ...(assetContentDigest ? { assetContentDigest } : {}),
        ...(chartPayload
          ? { payload: chartPayload as unknown as Readonly<Record<string, unknown>> }
          : tablePayload
            ? { payload: tablePayload as unknown as Readonly<Record<string, unknown>> }
            : {}),
        requiredFeatures: Object.freeze(requiredFeatures),
      });
    });
    const elementIds = declaredElements.map((element) => element.elementId);
    if (
      new Set(elementIds).size !== elementIds.length ||
      [...elementIds].sort().some((id, index) => id !== active[index])
    ) {
      invalid();
    }
    return {
      semanticKind: "authoredSemanticSource",
      declaredElementIds: active,
      declaredElements: Object.freeze(declaredElements),
      fontFamilies: Object.freeze([...fontFamilies].sort()),
      presentationId,
      slideId,
      slideRevision,
      operationId,
      authoredHtmlDigest,
    };
  }
  if (value.componentKind === "semanticUnavailable") {
    exactKeys(value, [
      "schemaVersion",
      "componentKind",
      "presentationId",
      "slideId",
      "slideRevision",
      "operationId",
      "originGenerationAttemptId",
      "authoredHtmlDigest",
      "state",
      "failureCode",
      "boundedAttemptCount",
      "compilerVersion",
      "policyVersion",
      "digest",
    ]);
    if (
      value.schemaVersion !== "semantic-unavailable-receipt/v1" ||
      value.state !== "degradedFidelityOnly"
    ) {
      invalid();
    }
    const presentationId = stringValue(value.presentationId);
    const slideId = stringValue(value.slideId);
    const slideRevision = revisionValue(value.slideRevision);
    const operationId = stringValue(value.operationId);
    const authoredHtmlDigest = digestValue(value.authoredHtmlDigest);
    nullableDigestValue(value.digest);
    return {
      semanticKind: "semanticUnavailable",
      declaredElementIds: [],
      declaredElements: [],
      fontFamilies: [],
      presentationId,
      slideId,
      slideRevision,
      operationId,
      authoredHtmlDigest,
    };
  }
  if (value.componentKind === "compatibilityObservation") {
    exactKeys(value, [
      "schemaVersion",
      "componentKind",
      "presentationId",
      "slideId",
      "slideRevision",
      "operationId",
      "authoredHtmlDigest",
      "extractorVersion",
      "orderedRemapDigest",
      "elements",
      "digest",
    ]);
    if (
      value.schemaVersion !== "compatibility-semantic-observation/v1" ||
      !Array.isArray(value.elements)
    ) {
      invalid();
    }
    const presentationId = stringValue(value.presentationId);
    const slideId = stringValue(value.slideId);
    const slideRevision = revisionValue(value.slideRevision);
    const operationId = stringValue(value.operationId);
    const authoredHtmlDigest = digestValue(value.authoredHtmlDigest);
    nullableDigestValue(value.orderedRemapDigest);
    nullableDigestValue(value.digest);
    return {
      semanticKind: "compatibilityObservation",
      declaredElementIds: [],
      declaredElements: [],
      fontFamilies: [],
      presentationId,
      slideId,
      slideRevision,
      operationId,
      authoredHtmlDigest,
    };
  }
  return invalid();
}

function parseBundle(value: unknown): ParsedBundleV1 {
  if (!isRecord(value)) invalid();
  const bundleKind = value.bundleKind;
  if (bundleKind !== "authored" && bundleKind !== "legacyHtml") invalid();
  const componentKey =
    bundleKind === "authored" ? "semanticComponent" : "compatibilityComponent";
  exactKeys(value, [
    "schemaVersion",
    "bundleKind",
    "presentationId",
    "slideId",
    "slideRevision",
    "operationId",
    "originGenerationAttemptId",
    "authoredHtml",
    "assetManifest",
    "fontManifest",
    "masterContractDigest",
    "compositionDigest",
    "elementIdentityRegistryDigest",
    "qualityPolicyVersion",
    "bundleDigest",
    componentKey,
  ]);
  if (
    value.schemaVersion !==
    (bundleKind === "authored"
      ? "authored-emission-bundle/v1"
      : "legacy-html-bundle/v1")
  ) {
    invalid();
  }
  const presentationId = stringValue(value.presentationId);
  const slideId = stringValue(value.slideId);
  const slideRevision = revisionValue(value.slideRevision);
  const operationId = stringValue(value.operationId);
  digestValue(value.masterContractDigest);
  digestValue(value.compositionDigest);
  digestValue(value.elementIdentityRegistryDigest);
  stringValue(value.qualityPolicyVersion);
  const bundleDigest = digestValue(value.bundleDigest);
  const authoredHtmlDigest = parseArtifactPointer(value.authoredHtml);
  parseArtifactPointer(value.assetManifest);
  const fontManifestDigest = parseArtifactPointer(value.fontManifest);
  const parsedComponent = parseSemanticComponent(value[componentKey]);
  if (
    (bundleKind === "authored" &&
      parsedComponent.semanticKind === "compatibilityObservation") ||
    (bundleKind === "legacyHtml" &&
      parsedComponent.semanticKind === "authoredSemanticSource")
  ) {
    invalid();
  }
  if (
    parsedComponent.presentationId !== presentationId ||
    parsedComponent.slideId !== slideId ||
    parsedComponent.slideRevision !== slideRevision ||
    parsedComponent.operationId !== operationId ||
    parsedComponent.authoredHtmlDigest !== authoredHtmlDigest
  ) {
    invalid();
  }
  const {
    presentationId: _componentPresentationId,
    slideId: _componentSlideId,
    slideRevision: _componentSlideRevision,
    operationId: _componentOperationId,
    authoredHtmlDigest: _componentAuthoredHtmlDigest,
    ...componentProjection
  } = parsedComponent;
  return Object.freeze({
    bundleKind,
    presentationId,
    slideId,
    slideRevision,
    bundleDigest,
    fontManifestDigest,
    ...componentProjection,
  });
}

function parseManifest(value: unknown): ParsedManifestV1 {
  if (!isRecord(value)) invalid();
  exactKeys(value, [
    "schemaVersion",
    "presentationId",
    "presentationRevision",
    "operationId",
    "generationAttemptId",
    "orderedSlides",
    "masterContractDigest",
    "compositionDigest",
    "assetManifestDigest",
    "fontManifestDigest",
    "qualityPolicyVersion",
    "sceneDigest",
    "scene",
    "canonicalDigest",
  ]);
  if (
    value.schemaVersion !== "authored-deck-emission-manifest/v1" ||
    !Array.isArray(value.orderedSlides) ||
    value.orderedSlides.length === 0 ||
    value.orderedSlides.length > 100
  ) {
    invalid();
  }
  const orderedSlides = value.orderedSlides.map((entry) => {
    if (!isRecord(entry)) invalid();
    exactKeys(entry, ["slideId", "slideRevision", "bundleKind", "bundleDigest"]);
    if (entry.bundleKind !== "authored" && entry.bundleKind !== "legacyHtml") {
      invalid();
    }
    return Object.freeze({
      slideId: stringValue(entry.slideId),
      slideRevision: revisionValue(entry.slideRevision),
      bundleKind: entry.bundleKind,
      bundleDigest: digestValue(entry.bundleDigest),
    });
  });
  if (new Set(orderedSlides.map((entry) => entry.slideId)).size !== orderedSlides.length) {
    invalid();
  }
  stringValue(value.operationId);
  digestValue(value.masterContractDigest);
  digestValue(value.compositionDigest);
  digestValue(value.assetManifestDigest);
  digestValue(value.fontManifestDigest);
  stringValue(value.qualityPolicyVersion);
  const presentationRevision = revisionValue(value.presentationRevision);
  const sceneDigest = nullableDigestValue(value.sceneDigest);
  if ((value.scene === null) !== (sceneDigest === null)) invalid();
  if (value.scene !== null) {
    if (!isRecord(value.scene)) invalid();
    if (
      value.scene.schemaVersion !== "deck-scene-ir/v1" ||
      revisionValue(value.scene.revision) !== presentationRevision ||
      digestValue(value.scene.sceneDigest) !== sceneDigest
    ) {
      invalid();
    }
  }
  return Object.freeze({
    presentationId: stringValue(value.presentationId),
    presentationRevision,
    canonicalDigest: digestValue(value.canonicalDigest),
    sceneDigest,
    orderedSlides,
  });
}

export function parseActiveAuthoredEmissionResponseV1(
  value: unknown,
): ActiveAuthoredEmissionResponseV1 {
  if (!isRecord(value)) invalid();
  exactKeys(value, [
    "schemaVersion",
    "presentationId",
    "canonicalMode",
    "presentationRevision",
    "activeGeneration",
    "canonicalDigest",
    "manifest",
    "bundles",
  ]);
  if (
    value.schemaVersion !== "active-authored-emission-response/v1" ||
    (value.canonicalMode !== "legacyHtml" && value.canonicalMode !== "bundleGraph") ||
    !Array.isArray(value.bundles)
  ) {
    invalid();
  }
  const canonicalMode = value.canonicalMode;
  const manifest = value.manifest === null ? null : parseManifest(value.manifest);
  const bundles = value.bundles.map(parseBundle);
  const canonicalDigest = nullableDigestValue(value.canonicalDigest);
  if (
    (canonicalMode === "legacyHtml" &&
      (manifest !== null || bundles.length !== 0)) ||
    (canonicalMode === "bundleGraph" &&
      (manifest === null ||
        canonicalDigest === null ||
        manifest.canonicalDigest !== canonicalDigest ||
        manifest.orderedSlides.length !== bundles.length))
  ) {
    invalid();
  }
  return Object.freeze({
    schemaVersion: "active-authored-emission-response/v1",
    presentationId: stringValue(value.presentationId),
    canonicalMode,
    presentationRevision: revisionValue(value.presentationRevision, true),
    activeGeneration: revisionValue(value.activeGeneration, true),
    canonicalDigest,
    manifest,
    bundles: Object.freeze(bundles),
  });
}

function normalizedAttemptDigest(value: string | undefined): string | undefined {
  if (value === undefined) return undefined;
  const normalized = value.toLowerCase();
  if (DIGEST_PATTERN.test(normalized)) return normalized;
  if (BARE_DIGEST_PATTERN.test(normalized)) return `sha256:${normalized}`;
  throw new Error("authored_export_attempt_canonical_digest_invalid");
}

export function bindActiveAuthoredEmissionForExport(
  active: ActiveAuthoredEmissionResponseV1,
  options: BindActiveEmissionOptions,
): ActiveEmissionExportBinding {
  if (
    active.presentationId !== options.presentationId ||
    active.presentationRevision !== options.presentationRevision
  ) {
    throw new Error("authored_active_manifest_revision_mismatch");
  }
  const attemptDigest = normalizedAttemptDigest(options.canonicalSourceDigest);
  if (active.canonicalMode === "legacyHtml") {
    if (options.mode === "hybrid") {
      throw new Error("authored_hybrid_active_manifest_required");
    }
    if (options.explicitMode && attemptDigest === undefined) {
      throw new Error("authored_fidelity_legacy_attempt_binding_required");
    }
    return Object.freeze({
      schemaVersion: "authored-active-export-binding/browser-v1",
      canonicalMode: "legacyHtml",
      presentationRevision: active.presentationRevision,
      activeGeneration: active.activeGeneration,
      canonicalDigest: attemptDigest ?? active.canonicalDigest,
      sceneDigest: null,
      slides: Object.freeze([]),
    });
  }
  if (!active.manifest || !active.canonicalDigest) {
    throw new Error("authored_active_manifest_response_invalid");
  }
  if (attemptDigest !== undefined && attemptDigest !== active.canonicalDigest) {
    throw new Error("authored_active_manifest_canonical_digest_mismatch");
  }
  if (options.explicitMode && attemptDigest === undefined) {
    throw new Error("authored_export_attempt_binding_required");
  }
  if (
    active.manifest.presentationId !== options.presentationId ||
    active.manifest.presentationRevision !== options.presentationRevision ||
    active.bundles.length !== options.slideIds.length
  ) {
    throw new Error("authored_active_manifest_slide_binding_mismatch");
  }
  const slides = active.bundles.map((bundle, index) => {
    const ordered = active.manifest!.orderedSlides[index];
    if (
      bundle.presentationId !== options.presentationId ||
      ordered.slideId !== options.slideIds[index] ||
      bundle.slideId !== ordered.slideId ||
      bundle.slideRevision !== ordered.slideRevision ||
      bundle.bundleKind !== ordered.bundleKind ||
      bundle.bundleDigest !== ordered.bundleDigest
    ) {
      throw new Error("authored_active_manifest_slide_binding_mismatch");
    }
    if (
      options.mode === "hybrid" &&
      (bundle.bundleKind !== "authored" ||
        bundle.semanticKind !== "authoredSemanticSource")
    ) {
      throw new Error("authored_hybrid_degraded_fidelity_only");
    }
    return Object.freeze({ ...bundle });
  });
  return Object.freeze({
    schemaVersion: "authored-active-export-binding/browser-v1",
    canonicalMode: "bundleGraph",
    presentationRevision: active.presentationRevision,
    activeGeneration: active.activeGeneration,
    canonicalDigest: active.canonicalDigest,
    sceneDigest: active.manifest.sceneDigest,
    slides: Object.freeze(slides),
  });
}

export async function fetchActiveAuthoredEmission(
  presentationId: string,
  cookieHeader: string,
): Promise<ActiveAuthoredEmissionResponseV1> {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), ACTIVE_EMISSION_TIMEOUT_MS);
  try {
    let response: Response;
    try {
      response = await fetch(
        `${getFastApiBaseUrl().replace(/\/+$/, "")}/api/v1/ppt/presentation/${encodeURIComponent(presentationId)}/authored-emission/active`,
        {
          method: "GET",
          headers: {
            Accept: "application/json",
            ...getFastApiAuthHeaders(),
            ...(cookieHeader.trim() ? { Cookie: cookieHeader } : {}),
          },
          cache: "no-store",
          signal: controller.signal,
        },
      );
    } catch (error) {
      throw new Error("authored_active_manifest_lookup_failed", { cause: error });
    }
    if (!response.ok) {
      throw new Error("authored_active_manifest_lookup_failed");
    }
    let body: string;
    try {
      body = await readBoundedResponseText(response, ACTIVE_EMISSION_RESPONSE_BYTES);
    } catch (error) {
      throw new Error("authored_active_manifest_response_invalid", { cause: error });
    }
    try {
      return parseActiveAuthoredEmissionResponseV1(JSON.parse(body));
    } catch (error) {
      if (
        error instanceof Error &&
        error.message === "authored_active_manifest_response_invalid"
      ) {
        throw error;
      }
      throw new Error("authored_active_manifest_response_invalid", { cause: error });
    }
  } finally {
    clearTimeout(timer);
  }
}
