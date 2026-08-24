import { getApiUrl } from "@/utils/api";
import { getHeader } from "./header";

const ROOT = "/api/v1/ppt/presentation";

export type HybridExportHistoryResolution =
  | "active"
  | "resolved"
  | "superseded";
export type HybridExportEvidenceStatus = "complete" | "incomplete" | "expired";
export type HybridVisualParity = "preflight" | "passed" | "failed" | "not-verified";

export interface HybridExportEvidenceProjection {
  status: HybridExportEvidenceStatus;
  attemptId: string;
  presentationRevision: number;
  canonicalSourceDigest?: string;
  rendererSourceDigest?: string;
  pptxSha256?: string;
  pptxSizeBytes?: number;
  coverageManifestSha256?: string;
  renderManifestSha256?: string;
  reportManifestSha256?: string;
  bundleManifestSha256?: string;
  slidePngSha256: string[];
  visualParity: HybridVisualParity;
  expiresAt?: string;
  nativeText?: number;
  nativeImage?: number;
  nativeShape?: number;
  nativeTable?: number;
  nativeChart?: number;
  rasterRemaining?: number;
  typographyIssues?: string[];
  fallbackReasons?: string[];
}

export interface HybridExportHistoryAttempt {
  exportAttemptId: string;
  state: "succeeded" | "failed";
  resolution: HybridExportHistoryResolution;
  supersededByAttemptId?: string;
  presentationRevision: number;
  format: "pptx";
  createdAt?: string;
  completedAt?: string;
  failureCode?: string;
  evidence: HybridExportEvidenceProjection;
}

export interface HybridExportHistory {
  schemaVersion: "presenton.authored-hybrid-export-history/v1";
  retention: {
    attemptRetentionDays?: number;
    evidenceRetentionDays?: number;
    expiresAt?: string;
    policy?: string;
    deletesResolvedAttempts?: boolean;
    expiryAction?: string;
    deletionAuthorized?: boolean;
  };
  attempts: HybridExportHistoryAttempt[];
}

export class HybridExportHistoryError extends Error {
  constructor(readonly status: number, readonly code?: string) {
    super(`Hybrid export history request failed (${status})`);
    this.name = "HybridExportHistoryError";
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

function integer(value: unknown): number | undefined {
  return typeof value === "number" && Number.isSafeInteger(value) && value >= 0
    ? value
    : undefined;
}

function requiredText(value: unknown, name: string): string {
  const normalized = text(value);
  if (!normalized) throw new Error(`Hybrid export history has invalid ${name}`);
  return normalized;
}

function requiredInteger(value: unknown, name: string): number {
  const normalized = integer(value);
  if (normalized === undefined) {
    throw new Error(`Hybrid export history has invalid ${name}`);
  }
  return normalized;
}

function first(raw: RecordValue, ...names: string[]): unknown {
  for (const name of names) {
    if (raw[name] !== undefined && raw[name] !== null) return raw[name];
  }
  return undefined;
}

function optionalDigest(value: unknown): string | undefined {
  const digest = text(value)?.toLowerCase();
  return digest && /^[a-f0-9]{64}$/.test(digest) ? digest : undefined;
}

function digestList(value: unknown): string[] {
  if (!Array.isArray(value)) return [];
  return value.flatMap((item) => {
    const digest = optionalDigest(item);
    return digest ? [digest] : [];
  });
}

function safeEnum<T extends string>(
  value: unknown,
  accepted: readonly T[],
  field: string,
): T {
  if (typeof value === "string" && accepted.includes(value as T)) {
    return value as T;
  }
  throw new Error(`Hybrid export history has invalid ${field}`);
}

function isPast(value: string | undefined): boolean {
  return value !== undefined && Date.parse(value) <= Date.now();
}

function optionalDate(value: unknown, field: string): string | undefined {
  const result = text(value);
  if (result !== undefined && !Number.isFinite(Date.parse(result))) {
    throw new Error(`Hybrid export history has invalid ${field}`);
  }
  return result;
}

function normalizeEvidence(value: unknown): HybridExportEvidenceProjection {
  const raw = record(value);
  const status = safeEnum(raw.status, ["complete", "incomplete", "expired"], "evidence.status");
  const visualParity = safeEnum(
    raw.visualParity,
    ["preflight", "passed", "failed", "not-verified"],
    "evidence.visualParity",
  );
  const values = (name: string) => integer(raw[name]);
  const reasons = (name: string) => {
    const value = raw[name];
    if (Array.isArray(value)) {
      return value.flatMap((item) => {
        const direct = text(item);
        if (direct) return [direct];
        const issue = record(item);
        const code = text(issue.code);
        const elementId = text(issue.elementId);
        return code ? [(elementId ?? "unknown") + ": " + code] : [];
      });
    }
    return Object.entries(record(value)).flatMap(([reason, count]) => {
      const label = text(reason);
      const occurrences = integer(count);
      return label ? [`${label}${occurrences === undefined ? "" : ` (${occurrences})`}`] : [];
    });
  };
  return {
    status,
    attemptId: requiredText(first(raw, "attemptId"), "evidence.attemptId"),
    presentationRevision: requiredInteger(first(raw, "presentationRevision", "revision"), "evidence.presentationRevision"),
    canonicalSourceDigest: optionalDigest(first(raw, "canonicalSourceDigest", "canonicalDigest")),
    rendererSourceDigest: optionalDigest(first(raw, "rendererSourceDigest", "rendererDigest")),
    pptxSha256: optionalDigest(raw.pptxSha256),
    pptxSizeBytes: values("pptxSizeBytes"),
    coverageManifestSha256: optionalDigest(first(raw, "coverageManifestSha256", "coverageSidecarSha256")),
    renderManifestSha256: optionalDigest(raw.renderManifestSha256),
    reportManifestSha256: optionalDigest(first(raw, "reportManifestSha256", "qualitySidecarSha256")),
    bundleManifestSha256: optionalDigest(raw.bundleManifestSha256),
    slidePngSha256: digestList(first(raw, "slidePngSha256", "renderedPngSha256")),
    visualParity,
    expiresAt: optionalDate(raw.expiresAt, "evidence.expiresAt"),
    nativeText: values("nativeText"),
    nativeImage: values("nativeImage"),
    nativeShape: values("nativeShape"),
    nativeTable: values("nativeTable"),
    nativeChart: values("nativeChart"),
    rasterRemaining: values("rasterRemaining"),
    typographyIssues: reasons("typographyIssues"),
    fallbackReasons: reasons("fallbackReasons").concat(reasons("fallbackReasonCounts")),
  };
}

export function normalizeHybridExportHistory(value: unknown): HybridExportHistory {
  const raw = record(value);
  if (raw.schemaVersion !== "presenton.authored-hybrid-export-history/v1") {
    throw new Error("Unsupported Hybrid export history schema");
  }
  const retention = record(raw.retention);
  if (!Array.isArray(raw.attempts)) {
    throw new Error("Hybrid export history has invalid attempts");
  }
  return {
    schemaVersion: "presenton.authored-hybrid-export-history/v1",
    retention: {
      attemptRetentionDays: integer(retention.attemptRetentionDays),
      evidenceRetentionDays: integer(retention.evidenceRetentionDays),
      expiresAt: text(retention.expiresAt),
      policy: text(retention.policy),
      deletesResolvedAttempts:
        typeof retention.deletesResolvedAttempts === "boolean"
          ? retention.deletesResolvedAttempts
          : undefined,
      expiryAction: text(retention.expiryAction),
      deletionAuthorized:
        typeof retention.deletionAuthorized === "boolean"
          ? retention.deletionAuthorized
          : undefined,
    },
    attempts: raw.attempts.map((item) => {
      const attempt = record(item);
      const exportAttemptId = requiredText(attempt.exportAttemptId, "attempt.exportAttemptId");
      const presentationRevision = requiredInteger(attempt.presentationRevision, "attempt.presentationRevision");
      const evidence = normalizeEvidence(attempt.evidence);
      if (evidence.attemptId !== exportAttemptId) {
        throw new Error("Hybrid export history has mismatched evidence.attemptId");
      }
      if (evidence.presentationRevision !== presentationRevision) {
        throw new Error("Hybrid export history has mismatched evidence.presentationRevision");
      }
      if (evidence.status === "complete" && isPast(evidence.expiresAt)) {
        throw new Error("Hybrid export history has stale complete evidence");
      }
      return {
        exportAttemptId,
        state: safeEnum(attempt.state, ["succeeded", "failed"], "attempt.state"),
        resolution: safeEnum(attempt.resolution, ["active", "resolved", "superseded"], "attempt.resolution"),
        supersededByAttemptId: text(attempt.supersededByAttemptId),
        presentationRevision,
        format: safeEnum(attempt.format, ["pptx"], "attempt.format"),
        createdAt: text(attempt.createdAt),
        completedAt: text(attempt.completedAt),
        failureCode: text(attempt.failureCode),
        evidence,
      };
    }),
  };
}

export async function getHybridExportHistory(
  presentationId: string,
  signal?: AbortSignal,
): Promise<HybridExportHistory> {
  const response = await fetch(
    getApiUrl(`${ROOT}/${encodeURIComponent(presentationId)}/export-attempts/hybrid/history`),
    { headers: getHeader(), credentials: "include", signal, cache: "no-store" },
  );
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) {
    const body = record(payload);
    throw new HybridExportHistoryError(
      response.status,
      text(body.detail) ?? text(body.code),
    );
  }
  return normalizeHybridExportHistory(payload);
}
