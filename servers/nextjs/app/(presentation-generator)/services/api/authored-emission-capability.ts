import { getApiUrl } from "@/utils/api";
import { getHeader } from "./header";

const ROOT = "/api/v1/ppt/presentation";
const SCHEMA_VERSION = "workbench-capability-projection/v1";

export type AuthoredCanonicalMode = "legacyHtml" | "bundleGraph";
export type AuthoredCapabilityState = "disabled" | "shadow" | "ready" | "blocked";

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

function normalizeNativeCapability(value: unknown, field: string): AuthoredNativeCapability {
  const raw = record(value);
  return {
    state: capabilityState(raw.state, `${field}.state`),
    receiptDigest:
      raw.receiptDigest === undefined
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
