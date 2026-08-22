import { getApiUrl } from "@/utils/api";
import { getHeader } from "./header";

const ROOT = "/api/v1/ppt/presentation";
const SCHEMA_VERSION = "workbench-capability-projection/v1";
const READINESS_V2_SCHEMA_VERSION = "workbench-capability-projection/v2";

export type AuthoredCanonicalMode = "legacyHtml" | "bundleGraph";
export type AuthoredCapabilityState = "disabled" | "shadow" | "ready" | "blocked";
export type AuthoredReleaseCapabilityId =
  | AuthoredSemanticCapabilityId
  | "receipt.cjk"
  | "receipt.chart"
  | "receipt.zBand"
  | "receipt.degradation"
  | "renderer.attestation"
  | "evidence.visual";
export type AuthoredRendererAttestationState = "unattested" | "attested" | "expired" | "blocked";
export type AuthoredSemanticCapabilityId =
  | "shape.gradient"
  | "shape.shadow"
  | "layout.transform"
  | "layout.flex"
  | "layout.grid"
  | "shape.borderRadius"
  | "shape.opacity"
  | "shape.clip";
export type AuthoredSemanticReceiptId = "cjk" | "chart" | "zBand" | "degradation";
export type AuthoredSemanticDecision =
  | "native"
  | "representationFallback"
  | "identityFailure"
  | "ownershipFailure"
  | "assetPublicationFailure"
  | "blocked";

export interface AuthoredSemanticCapabilityDecision {
  capabilityId: AuthoredSemanticCapabilityId;
  state: AuthoredCapabilityState;
  /** Server-issued operation result; never inferred from a browser feature. */
  decision: AuthoredSemanticDecision;
  /** Stable, server-issued machine reason code. */
  reasonCode: string;
  receiptDigest: string;
}

export interface AuthoredSemanticReceiptDecision {
  receiptId: AuthoredSemanticReceiptId;
  state: AuthoredCapabilityState;
  /** Distinguishes normal representation fallback from publication authority failures. */
  decision: AuthoredSemanticDecision;
  reasonCode: string;
  receiptDigest: string;
}

export interface AuthoredEmissionCapabilityProjection {
  schemaVersion: typeof SCHEMA_VERSION;
  presentationId: string;
  presentationRevision: number;
  canonicalMode: AuthoredCanonicalMode;
  /** The immutable deck manifest identity consumed by both export choices. */
  canonicalDigest?: string;
  manifestDigest?: string;
  degradedFidelityOnly: boolean;
  fidelity: {
    state: AuthoredCapabilityState;
    receiptDigest?: string;
    reason?: string;
  };
  hybrid: {
    text: AuthoredNativeCapability;
    image: AuthoredNativeCapability;
    shape: AuthoredNativeCapability;
  };
  /**
   * Complete, server-issued serializer decision matrix. The browser only
   * displays it; missing entries never become client-side capability defaults.
   */
  capabilityMatrix: AuthoredSemanticCapabilityDecision[];
  /** Server-issued CJK, chart, z-band, and degradation receipt decisions. */
  receipts: AuthoredSemanticReceiptDecision[];
  readiness: {
    authoredCreationReady: boolean;
    fidelityExportReady: boolean;
    fullNativeParityReady: false;
    independentVnextGenerationReady: false;
    promotionReady: false;
  };
  killSwitches: Array<{
    capabilityId: string;
    active: boolean;
    reason?: string;
    expiresAt?: string;
  }>;
  elements: Array<{
    elementId: string;
    editableFields: string[];
    readOnlyReason?: string;
  }>;
  projectionDigest?: string;
}

export interface AuthoredReleaseReadinessCapability {
  capabilityId: AuthoredReleaseCapabilityId;
  state: AuthoredCapabilityState;
  reasonCode: string;
  receiptDigest?: string;
  scope: { platform: string; renderer: string; archetypes: string[] };
  serializerId: string;
  serializerVersion: string;
  expiresAt?: string;
}

export interface AuthoredReleaseReadinessProjectionV2 {
  schemaVersion: typeof READINESS_V2_SCHEMA_VERSION;
  presentationId: string;
  presentationRevision: number;
  canonicalMode: AuthoredCanonicalMode;
  canonicalDigest?: string;
  manifestDigest?: string;
  degradedFidelityOnly: boolean;
  releaseProfile: {
    schemaVersion: "authored-release-profile/v2";
    profileId: string;
    platformScope: { platform: string; renderer: string; archetypes: string[] };
    evaluatedAt: string;
    expiresAt: string;
    promotionReady: boolean;
    boundedNativeParityReady: boolean;
    independentGenerationReady: boolean;
    promotionCapabilityIds: AuthoredReleaseCapabilityId[];
    requiredCapabilityIds: AuthoredReleaseCapabilityId[];
    boundedNativeCapabilityIds: AuthoredReleaseCapabilityId[];
    readyReceiptDigests: string[];
    profileDefinitionDigest: string;
    promotionReceiptDigests: string[];
    boundedNativeReceiptDigests: string[];
    reasonCode: string;
  };
  capabilities: AuthoredReleaseReadinessCapability[];
  rendererAttestation: {
    state: AuthoredRendererAttestationState;
    reasonCode: string;
    attestationDigest?: string;
    environment: Record<string, string>;
    expiresAt?: string;
  };
  killSwitches: Array<{
    capabilityId: string;
    active: boolean;
    reasonCode: string;
    expiresAt?: string;
  }>;
  releaseAuthorityReceiptDigests: string[];
  releaseProjectionDigest?: string;
  exportAuthorityClaim?: {
    schemaVersion: "authored-export-authority-claim/v2";
    kind: "promotion" | "boundedNative";
    profileId: string;
    profileDefinitionDigest: string;
    projectionDigest: string;
    receiptDigests: string[];
    state: "ready";
  };
}

export type AuthoredEmissionReadinessStatus =
  | { version: "v2"; projection: AuthoredReleaseReadinessProjectionV2 }
  | { version: "v1-fallback"; projection: AuthoredEmissionCapabilityProjection };

export interface AuthoredNativeCapability {
  state: AuthoredCapabilityState;
  receiptDigest?: string;
  reason?: string;
}

export class AuthoredEmissionCapabilityError extends Error {
  constructor(readonly status: number, readonly code?: string) {
    super(`Authored emission capability request failed (${status})`);
    this.name = "AuthoredEmissionCapabilityError";
  }
}

type RecordValue = Record<string, unknown>;

function record(value: unknown): RecordValue {
  return value !== null && typeof value === "object" && !Array.isArray(value)
    ? (value as RecordValue)
    : {};
}

function text(value: unknown): string | undefined {
  return typeof value === "string" && value.trim() ? value.trim() : undefined;
}

function requiredText(value: unknown, field: string): string {
  const result = text(value);
  if (!result) throw new Error(`Capability projection has invalid ${field}`);
  return result;
}

function requiredNonNegativeInteger(value: unknown, field: string): number {
  if (typeof value !== "number" || !Number.isSafeInteger(value) || value < 0) {
    throw new Error(`Capability projection has invalid ${field}`);
  }
  return value;
}

function requiredDigest(value: unknown, field: string): string {
  const digest = requiredText(value, field).toLowerCase();
  if (!/^sha256:[a-f0-9]{64}$/.test(digest)) {
    throw new Error(`Capability projection has invalid ${field}`);
  }
  return digest;
}

function requiredDate(value: unknown, field: string): string {
  const result = requiredText(value, field);
  if (!Number.isFinite(Date.parse(result))) {
    throw new Error(`Capability projection has invalid ${field}`);
  }
  return result;
}

function booleanField(value: unknown, field: string): boolean {
  if (typeof value !== "boolean") {
    throw new Error(`Capability projection has invalid ${field}`);
  }
  return value;
}

function capabilityState(value: unknown, field: string): AuthoredCapabilityState {
  if (value === "disabled" || value === "shadow" || value === "ready" || value === "blocked") {
    return value;
  }
  throw new Error(`Capability projection has invalid ${field}`);
}

const CAPABILITY_IDS = [
  "shape.gradient",
  "shape.shadow",
  "layout.transform",
  "layout.flex",
  "layout.grid",
  "shape.borderRadius",
  "shape.opacity",
  "shape.clip",
] as const satisfies readonly AuthoredSemanticCapabilityId[];

const RECEIPT_IDS = ["cjk", "chart", "zBand", "degradation"] as const satisfies readonly AuthoredSemanticReceiptId[];
// Type guard only. The active capability sets are server-loaded from the
// release profile and are deliberately not duplicated in this client.
const KNOWN_RELEASE_CAPABILITY_IDS = [
  ...CAPABILITY_IDS,
  "receipt.cjk", "receipt.chart", "receipt.zBand", "receipt.degradation",
  "renderer.attestation", "evidence.visual",
] as const satisfies readonly AuthoredReleaseCapabilityId[];

function requiredReasonCode(value: unknown, field: string): string {
  const code = requiredText(value, field);
  if (!/^[a-z][a-z0-9._-]{1,127}$/.test(code)) {
    throw new Error(`Capability projection has invalid ${field}`);
  }
  return code;
}

function semanticDecision(value: unknown, field: string): AuthoredSemanticDecision {
  if (
    value === "native" ||
    value === "representationFallback" ||
    value === "identityFailure" ||
    value === "ownershipFailure" ||
    value === "assetPublicationFailure" ||
    value === "blocked"
  ) {
    return value;
  }
  throw new Error(`Capability projection has invalid ${field}`);
}

function requiredReleaseCapabilityId(value: unknown, field: string): AuthoredReleaseCapabilityId {
  const id = requiredText(value, field) as AuthoredReleaseCapabilityId;
  if (!(KNOWN_RELEASE_CAPABILITY_IDS as readonly string[]).includes(id)) {
    throw new Error(`Capability projection has invalid ${field}`);
  }
  return id;
}

function rendererAttestationState(value: unknown, field: string): AuthoredRendererAttestationState {
  if (value === "unattested" || value === "attested" || value === "expired" || value === "blocked") return value;
  throw new Error(`Capability projection has invalid ${field}`);
}

function stringRecord(value: unknown, field: string): Record<string, string> {
  const raw = record(value);
  const entries = Object.entries(raw);
  if (entries.length === 0 || entries.some(([key, item]) => !key || typeof item !== "string" || !item.trim())) {
    throw new Error(`Capability projection has invalid ${field}`);
  }
  return Object.fromEntries(entries.map(([key, item]) => [key, item.trim()]));
}

function matrixEntries<T extends string>(
  value: unknown,
  field: string,
  ids: readonly T[],
  key: "capabilityId" | "receiptId",
): Array<{
  [K in typeof key]: T;
} & {
  state: AuthoredCapabilityState;
  decision: AuthoredSemanticDecision;
  reasonCode: string;
  receiptDigest: string;
}> {
  if (!Array.isArray(value) || value.length !== ids.length) {
    throw new Error(`Capability projection has incomplete ${field}`);
  }
  const expected = new Set<string>(ids);
  const seen = new Set<string>();
  return value.map((item, index) => {
    const raw = record(item);
    const id = requiredText(raw[key], `${field}[${index}].${key}`) as T;
    if (!expected.has(id) || seen.has(id)) {
      throw new Error(`Capability projection has invalid ${field}[${index}].${key}`);
    }
    seen.add(id);
    return {
      [key]: id,
      state: capabilityState(raw.state, `${field}[${index}].state`),
      decision: semanticDecision(raw.decision, `${field}[${index}].decision`),
      reasonCode: requiredReasonCode(raw.reasonCode, `${field}[${index}].reasonCode`),
      receiptDigest: requiredDigest(raw.receiptDigest, `${field}[${index}].receiptDigest`),
    } as {
      [K in typeof key]: T;
    } & {
      state: AuthoredCapabilityState;
      decision: AuthoredSemanticDecision;
      reasonCode: string;
      receiptDigest: string;
    };
  });
}

function normalizeNativeCapability(value: unknown, field: string): AuthoredNativeCapability {
  const raw = record(value);
  return {
    state: capabilityState(raw.state, `${field}.state`),
    receiptDigest:
      raw.receiptDigest === undefined || raw.receiptDigest === null
        ? undefined
        : requiredDigest(raw.receiptDigest, `${field}.receiptDigest`),
    reason: text(raw.reason),
  };
}

/**
 * Treat an incomplete or future response as unavailable. It is safer to keep
 * Fidelity available than to advertise native editing from an unpinned receipt.
 */
export function normalizeAuthoredEmissionCapabilityProjection(
  value: unknown,
): AuthoredEmissionCapabilityProjection {
  const raw = record(value);
  const data = raw.data && typeof raw.data === "object" ? record(raw.data) : raw;
  if (data.schemaVersion !== SCHEMA_VERSION) {
    throw new Error("Unsupported authored emission capability schema");
  }
  const canonicalMode = data.canonicalMode;
  if (canonicalMode !== "legacyHtml" && canonicalMode !== "bundleGraph") {
    throw new Error("Capability projection has invalid canonicalMode");
  }
  const hybrid = record(data.hybrid);
  const readiness = record(data.readiness);
  const killSwitches = data.killSwitches;
  if (!Array.isArray(killSwitches)) {
    throw new Error("Capability projection has invalid killSwitches");
  }
  const readinessValues = {
    authoredCreationReady: booleanField(readiness.authoredCreationReady, "readiness.authoredCreationReady"),
    fidelityExportReady: booleanField(readiness.fidelityExportReady, "readiness.fidelityExportReady"),
    fullNativeParityReady: booleanField(readiness.fullNativeParityReady, "readiness.fullNativeParityReady"),
    independentVnextGenerationReady: booleanField(readiness.independentVnextGenerationReady, "readiness.independentVnextGenerationReady"),
    promotionReady: booleanField(readiness.promotionReady, "readiness.promotionReady"),
  };
  // These are intentionally never client-promotable. A server response that
  // says otherwise is rejected rather than silently widening product claims.
  if (
    readinessValues.fullNativeParityReady ||
    readinessValues.independentVnextGenerationReady ||
    readinessValues.promotionReady
  ) {
    throw new Error("Capability projection attempted an unsupported readiness promotion");
  }
  const normalizedReadiness = {
    authoredCreationReady: readinessValues.authoredCreationReady,
    fidelityExportReady: readinessValues.fidelityExportReady,
    promotionReady: false as const,
    fullNativeParityReady: false as const,
    independentVnextGenerationReady: false as const,
  };
  return {
    schemaVersion: SCHEMA_VERSION,
    presentationId: requiredText(data.presentationId, "presentationId"),
    presentationRevision: requiredNonNegativeInteger(data.presentationRevision, "presentationRevision"),
    canonicalMode,
    canonicalDigest:
      data.canonicalDigest === undefined || data.canonicalDigest === null
        ? undefined
        : requiredDigest(data.canonicalDigest, "canonicalDigest"),
    manifestDigest:
      data.manifestDigest === undefined || data.manifestDigest === null
        ? undefined
        : requiredDigest(data.manifestDigest, "manifestDigest"),
    degradedFidelityOnly: booleanField(data.degradedFidelityOnly, "degradedFidelityOnly"),
    fidelity: normalizeNativeCapability(data.fidelity, "fidelity"),
    hybrid: {
      text: normalizeNativeCapability(hybrid.text, "hybrid.text"),
      image: normalizeNativeCapability(hybrid.image, "hybrid.image"),
      shape: normalizeNativeCapability(hybrid.shape, "hybrid.shape"),
    },
    capabilityMatrix: matrixEntries(
      data.capabilityMatrix,
      "capabilityMatrix",
      CAPABILITY_IDS,
      "capabilityId",
    ) as AuthoredSemanticCapabilityDecision[],
    receipts: matrixEntries(
      data.receipts,
      "receipts",
      RECEIPT_IDS,
      "receiptId",
    ) as AuthoredSemanticReceiptDecision[],
    readiness: normalizedReadiness,
    killSwitches: killSwitches.map((item, index) => {
      const switchValue = record(item);
      return {
        capabilityId: requiredText(switchValue.capabilityId, `killSwitches[${index}].capabilityId`),
        active: booleanField(switchValue.active, `killSwitches[${index}].active`),
        reason: text(switchValue.reason),
        expiresAt: text(switchValue.expiresAt),
      };
    }),
    elements: (() => {
      if (!Array.isArray(data.elements)) {
        throw new Error("Capability projection has invalid elements");
      }
      return data.elements.map((item, index) => {
        const element = record(item);
        if (!Array.isArray(element.editableFields) || !element.editableFields.every((field) => typeof field === "string" && field.trim())) {
          throw new Error(`Capability projection has invalid elements[${index}].editableFields`);
        }
        return {
          elementId: requiredText(element.elementId, `elements[${index}].elementId`),
          editableFields: element.editableFields.map((field) => field.trim()),
          readOnlyReason: text(element.readOnlyReason),
        };
      });
    })(),
    projectionDigest:
      data.projectionDigest === undefined || data.projectionDigest === null
        ? undefined
        : requiredDigest(data.projectionDigest, "projectionDigest"),
  };
}

/** Strictly parse the separate release-readiness endpoint. This is a display
 * projection, never a browser authority to promote a serializer or release. */
export function normalizeAuthoredReleaseReadinessProjectionV2(value: unknown): AuthoredReleaseReadinessProjectionV2 {
  const raw = record(value);
  const data = raw.data && typeof raw.data === "object" ? record(raw.data) : raw;
  if (data.schemaVersion !== READINESS_V2_SCHEMA_VERSION) {
    throw new Error("Unsupported authored release readiness schema");
  }
  const profile = record(data.releaseProfile);
  if (profile.schemaVersion !== "authored-release-profile/v2") {
    throw new Error("Capability projection has invalid releaseProfile.schemaVersion");
  }
  const requiredCapabilityIds = profile.requiredCapabilityIds;
  if (!Array.isArray(requiredCapabilityIds) || requiredCapabilityIds.length === 0) {
    throw new Error("Capability projection has incomplete releaseProfile.requiredCapabilityIds");
  }
  const requiredIds = requiredCapabilityIds.map((id, index) => requiredReleaseCapabilityId(id, `releaseProfile.requiredCapabilityIds[${index}]`));
  if (new Set(requiredIds).size !== requiredIds.length) throw new Error("Capability projection has duplicate releaseProfile.requiredCapabilityIds");
  const promotionCapabilityIds = profile.promotionCapabilityIds;
  if (!Array.isArray(promotionCapabilityIds) || promotionCapabilityIds.length === 0) {
    throw new Error("Capability projection has incomplete releaseProfile.promotionCapabilityIds");
  }
  const promotionIds = promotionCapabilityIds.map((id, index) => requiredReleaseCapabilityId(id, `releaseProfile.promotionCapabilityIds[${index}]`));
  if (!promotionIds.every((id, index) => id === requiredIds[index])) {
    throw new Error("Capability projection has mismatched releaseProfile.promotionCapabilityIds");
  }
  const boundedNativeCapabilityIds = profile.boundedNativeCapabilityIds;
  if (!Array.isArray(boundedNativeCapabilityIds) || boundedNativeCapabilityIds.length === 0) {
    throw new Error("Capability projection has incomplete releaseProfile.boundedNativeCapabilityIds");
  }
  const boundedNativeIds = boundedNativeCapabilityIds.map((id, index) => requiredReleaseCapabilityId(id, `releaseProfile.boundedNativeCapabilityIds[${index}]`));
  if (new Set(boundedNativeIds).size !== boundedNativeIds.length) throw new Error("Capability projection has duplicate releaseProfile.boundedNativeCapabilityIds");
  if (!promotionIds.every((id) => boundedNativeIds.includes(id))) {
    throw new Error("Capability projection has invalid releaseProfile.promotionCapabilityIds");
  }
  const readyReceiptDigests = profile.readyReceiptDigests;
  if (!Array.isArray(readyReceiptDigests)) throw new Error("Capability projection has invalid releaseProfile.readyReceiptDigests");
  const digestList = (value: unknown, field: string, nonEmpty = false): string[] => {
    if (!Array.isArray(value) || (nonEmpty && value.length === 0)) throw new Error(`Capability projection has invalid ${field}`);
    const digests = value.map((digest, index) => requiredDigest(digest, `${field}[${index}]`));
    if (new Set(digests).size !== digests.length) throw new Error(`Capability projection has duplicate ${field}`);
    return digests;
  };
  const promotionReceiptDigests = digestList(profile.promotionReceiptDigests, "releaseProfile.promotionReceiptDigests");
  const boundedNativeReceiptDigests = digestList(profile.boundedNativeReceiptDigests, "releaseProfile.boundedNativeReceiptDigests");
  const releaseAuthorityReceiptDigests = digestList(data.releaseAuthorityReceiptDigests ?? [], "releaseAuthorityReceiptDigests");
  const exportAuthorityClaim = data.exportAuthorityClaim === undefined || data.exportAuthorityClaim === null
    ? undefined
    : record(data.exportAuthorityClaim);
  const exactDigestList = (left: readonly string[], right: readonly string[]) => left.length === right.length && left.every((item, index) => item === right[index]);
  const profileDefinitionDigest = requiredDigest(profile.profileDefinitionDigest, "releaseProfile.profileDefinitionDigest");
  let releaseProjectionDigest: string | undefined;
  let normalizedExportAuthorityClaim: AuthoredReleaseReadinessProjectionV2["exportAuthorityClaim"];
  if (exportAuthorityClaim) {
    if (exportAuthorityClaim.schemaVersion !== "authored-export-authority-claim/v2") throw new Error("Capability projection has invalid exportAuthorityClaim.schemaVersion");
    if (exportAuthorityClaim.kind !== "promotion" && exportAuthorityClaim.kind !== "boundedNative") throw new Error("Capability projection has invalid exportAuthorityClaim.kind");
    if (exportAuthorityClaim.state !== "ready") throw new Error("Capability projection has invalid exportAuthorityClaim.state");
    const claimReceiptDigests = digestList(exportAuthorityClaim.receiptDigests, "exportAuthorityClaim.receiptDigests", true);
    if (!releaseAuthorityReceiptDigests.length || !exactDigestList(claimReceiptDigests, releaseAuthorityReceiptDigests)) {
      throw new Error("Capability projection has mismatched exportAuthorityClaim.receiptDigests");
    }
    const claimedReceiptSet = exportAuthorityClaim.kind === "promotion" ? promotionReceiptDigests : boundedNativeReceiptDigests;
    if (!exactDigestList(claimReceiptDigests, claimedReceiptSet)) {
      throw new Error("Capability projection has mismatched exportAuthorityClaim receipt pins");
    }
    releaseProjectionDigest = requiredDigest(data.releaseProjectionDigest, "releaseProjectionDigest");
    if (
      requiredText(exportAuthorityClaim.profileId, "exportAuthorityClaim.profileId") !== requiredText(profile.profileId, "releaseProfile.profileId") ||
      requiredDigest(exportAuthorityClaim.profileDefinitionDigest, "exportAuthorityClaim.profileDefinitionDigest") !== profileDefinitionDigest ||
      requiredDigest(exportAuthorityClaim.projectionDigest, "exportAuthorityClaim.projectionDigest") !== releaseProjectionDigest
    ) {
      throw new Error("Capability projection has mismatched exportAuthorityClaim binding pins");
    }
    normalizedExportAuthorityClaim = {
      schemaVersion: "authored-export-authority-claim/v2",
      kind: exportAuthorityClaim.kind,
      profileId: requiredText(exportAuthorityClaim.profileId, "exportAuthorityClaim.profileId"),
      profileDefinitionDigest,
      projectionDigest: releaseProjectionDigest,
      receiptDigests: claimReceiptDigests,
      state: "ready",
    };
  }
  const capabilities = data.capabilities;
  if (!Array.isArray(capabilities) || capabilities.length !== boundedNativeIds.length) {
    throw new Error("Capability projection has incomplete capabilities");
  }
  const seen = new Set<string>();
  const normalizedCapabilities = capabilities.map((item, index) => {
    const capability = record(item);
    const capabilityId = requiredReleaseCapabilityId(capability.capabilityId, `capabilities[${index}].capabilityId`);
    if (seen.has(capabilityId)) throw new Error(`Capability projection has duplicate capabilities[${index}].capabilityId`);
    seen.add(capabilityId);
    const scope = record(capability.scope);
    const archetypes = scope.archetypes;
    if (!Array.isArray(archetypes) || archetypes.length === 0 || !archetypes.every((entry) => typeof entry === "string" && entry.trim())) {
      throw new Error(`Capability projection has invalid capabilities[${index}].scope.archetypes`);
    }
    return {
      capabilityId,
      state: capabilityState(capability.state, `capabilities[${index}].state`),
      reasonCode: requiredReasonCode(capability.reasonCode, `capabilities[${index}].reasonCode`),
      receiptDigest: capability.receiptDigest === undefined || capability.receiptDigest === null
        ? undefined
        : requiredDigest(capability.receiptDigest, `capabilities[${index}].receiptDigest`),
      scope: {
        platform: requiredText(scope.platform, `capabilities[${index}].scope.platform`),
        renderer: requiredText(scope.renderer, `capabilities[${index}].scope.renderer`),
        archetypes: archetypes.map((entry) => entry.trim()),
      },
      serializerId: requiredText(capability.serializerId, `capabilities[${index}].serializerId`),
      serializerVersion: requiredText(capability.serializerVersion, `capabilities[${index}].serializerVersion`),
      expiresAt: capability.expiresAt === undefined || capability.expiresAt === null
        ? undefined
        : requiredDate(capability.expiresAt, `capabilities[${index}].expiresAt`),
    };
  });
  if (!boundedNativeIds.every((id) => seen.has(id))) {
    throw new Error("Capability projection has invalid capabilities");
  }
  const attestation = record(data.rendererAttestation);
  const profileScope = record(profile.platformScope);
  const profileArchetypes = profileScope.archetypes;
  if (!Array.isArray(profileArchetypes) || profileArchetypes.length === 0 || !profileArchetypes.every((entry) => typeof entry === "string" && entry.trim())) {
    throw new Error("Capability projection has invalid releaseProfile.platformScope.archetypes");
  }
  const switches = data.killSwitches;
  if (!Array.isArray(switches)) throw new Error("Capability projection has invalid killSwitches");
  return {
    schemaVersion: READINESS_V2_SCHEMA_VERSION,
    presentationId: requiredText(data.presentationId, "presentationId"),
    presentationRevision: requiredNonNegativeInteger(data.presentationRevision, "presentationRevision"),
    canonicalMode: data.canonicalMode === "legacyHtml" || data.canonicalMode === "bundleGraph"
      ? data.canonicalMode
      : (() => { throw new Error("Capability projection has invalid canonicalMode"); })(),
    canonicalDigest: data.canonicalDigest === undefined || data.canonicalDigest === null ? undefined : requiredDigest(data.canonicalDigest, "canonicalDigest"),
    manifestDigest: data.manifestDigest === undefined || data.manifestDigest === null ? undefined : requiredDigest(data.manifestDigest, "manifestDigest"),
    degradedFidelityOnly: booleanField(data.degradedFidelityOnly, "degradedFidelityOnly"),
    releaseProfile: {
      schemaVersion: "authored-release-profile/v2",
      profileId: requiredText(profile.profileId, "releaseProfile.profileId"),
      platformScope: {
        platform: requiredText(profileScope.platform, "releaseProfile.platformScope.platform"),
        renderer: requiredText(profileScope.renderer, "releaseProfile.platformScope.renderer"),
        archetypes: profileArchetypes.map((entry) => entry.trim()),
      },
      evaluatedAt: requiredDate(profile.evaluatedAt, "releaseProfile.evaluatedAt"),
      expiresAt: requiredDate(profile.expiresAt, "releaseProfile.expiresAt"),
      promotionReady: booleanField(profile.promotionReady, "releaseProfile.promotionReady"),
      boundedNativeParityReady: booleanField(profile.boundedNativeParityReady, "releaseProfile.boundedNativeParityReady"),
      independentGenerationReady: booleanField(profile.independentGenerationReady, "releaseProfile.independentGenerationReady"),
      promotionCapabilityIds: promotionIds,
      requiredCapabilityIds: requiredIds,
      boundedNativeCapabilityIds: boundedNativeIds,
      readyReceiptDigests: readyReceiptDigests.map((digest, index) => requiredDigest(digest, `releaseProfile.readyReceiptDigests[${index}]`)),
      profileDefinitionDigest,
      promotionReceiptDigests,
      boundedNativeReceiptDigests,
      reasonCode: requiredReasonCode(profile.reasonCode, "releaseProfile.reasonCode"),
    },
    capabilities: normalizedCapabilities,
    rendererAttestation: {
      state: rendererAttestationState(attestation.state, "rendererAttestation.state"),
      reasonCode: requiredReasonCode(attestation.reasonCode, "rendererAttestation.reasonCode"),
      attestationDigest: attestation.attestationDigest === undefined || attestation.attestationDigest === null
        ? undefined
        : requiredDigest(attestation.attestationDigest, "rendererAttestation.attestationDigest"),
      environment: stringRecord(attestation.environment, "rendererAttestation.environment"),
      expiresAt: attestation.expiresAt === undefined || attestation.expiresAt === null
        ? undefined
        : requiredDate(attestation.expiresAt, "rendererAttestation.expiresAt"),
    },
    killSwitches: switches.map((item, index) => {
      const current = record(item);
      return {
        capabilityId: requiredText(current.capabilityId, `killSwitches[${index}].capabilityId`),
        active: booleanField(current.active, `killSwitches[${index}].active`),
        reasonCode: requiredReasonCode(current.reasonCode, `killSwitches[${index}].reasonCode`),
        expiresAt: current.expiresAt === undefined || current.expiresAt === null ? undefined : requiredDate(current.expiresAt, `killSwitches[${index}].expiresAt`),
      };
    }),
    releaseAuthorityReceiptDigests,
    ...(releaseProjectionDigest ? { releaseProjectionDigest } : {}),
    ...(normalizedExportAuthorityClaim ? { exportAuthorityClaim: normalizedExportAuthorityClaim } : {}),
  };
}

export async function getAuthoredEmissionCapabilityProjection(
  presentationId: string,
  signal?: AbortSignal,
): Promise<AuthoredEmissionCapabilityProjection> {
  const response = await fetch(
    getApiUrl(`${ROOT}/${encodeURIComponent(presentationId)}/authored-emission/capability-projection`),
    { headers: getHeader(), credentials: "include", signal, cache: "no-store" },
  );
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) {
    const raw = record(payload);
    throw new AuthoredEmissionCapabilityError(response.status, text(raw.code) ?? text(raw.detail));
  }
  return normalizeAuthoredEmissionCapabilityProjection(payload);
}

export async function getAuthoredEmissionReadinessStatus(
  presentationId: string,
  signal?: AbortSignal,
): Promise<AuthoredEmissionReadinessStatus> {
  const v2 = await fetch(
    getApiUrl(`${ROOT}/${encodeURIComponent(presentationId)}/authored-emission/capability-projection-v2`),
    { headers: getHeader(), credentials: "include", signal, cache: "no-store" },
  );
  if (v2.ok) {
    return { version: "v2", projection: normalizeAuthoredReleaseReadinessProjectionV2(await v2.json().catch(() => ({}))) };
  }
  // Only an absent or explicitly unsupported v2 route can use the retained v1
  // false-readiness compatibility path. Authentication and server errors remain
  // unavailable rather than downgrading the release claim.
  if (v2.status !== 404 && v2.status !== 410 && v2.status !== 501) {
    const body = record(await v2.json().catch(() => ({})));
    throw new AuthoredEmissionCapabilityError(v2.status, text(body.code) ?? text(body.detail));
  }
  return { version: "v1-fallback", projection: await getAuthoredEmissionCapabilityProjection(presentationId, signal) };
}
