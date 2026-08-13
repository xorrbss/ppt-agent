import { getApiUrl } from "@/utils/api";

export type PackageDigest = `sha256:${string}`;

export interface OoxmlPackageVersion {
  schemaVersion: "authored-vnext-ooxml-package/v1";
  packageId: string;
  sourcePackageId: string | null;
  state: "imported" | "quarantined" | "editable" | "archived";
  mode: "PRESERVE_EXACT" | "CORPORATE_NORMALIZE";
  origin: "IMPORTED" | "GENERATED";
  revision: number;
  artifactDigest: PackageDigest;
  archiveByteLength: number;
  inspection: {
    classification: "PRESERVE_EXACT" | "PATCH_SLICE" | "GENERATED" | "QUARANTINED";
    slideCount: number;
    masterCount: number;
    layoutCount: number;
    externalRelationshipWarnings: string[];
    quarantineReasons: string[];
  };
}

export interface OoxmlImportReceipt {
  schemaVersion: "authored-vnext-ooxml-import/v1";
  replayed: boolean;
  editable: boolean;
  sourceArchived: true;
  package: OoxmlPackageVersion;
}

export interface OoxmlOperationReceipt {
  operationId: string;
  packageId: string;
  state: "queued" | "running" | "succeeded" | "failed" | "cancelled";
  resultRevision: number | null;
  resultArtifactDigest: PackageDigest | null;
  changedParts: string[];
  silentLossCount: number;
  errorCode: string | null;
}

export interface OoxmlReadBack {
  package: OoxmlPackageVersion;
  sourceArchiveRetained: true;
  unsupportedPartsPassthrough: boolean;
  untouchedPartsVerified: boolean;
  silentLossCount: number;
  changedParts: string[];
}

async function body(response: Response): Promise<unknown> {
  const value = await response.json().catch(() => ({}));
  if (!response.ok) {
    const source = value as { detail?: string | { code?: string } };
    const code = typeof source.detail === "string"
      ? source.detail
      : source.detail?.code ?? "authored_vnext_ooxml_request_failed";
    throw new Error(code);
  }
  return value;
}

export class AuthoredVnextPackageClient {
  constructor(private readonly fetchImpl: typeof fetch = globalThis.fetch.bind(globalThis)) {}

  private request(path: string, init?: RequestInit): Promise<unknown> {
    return this.fetchImpl(getApiUrl(path), {
      cache: "no-store",
      credentials: "include",
      ...init,
      headers: { Accept: "application/json", ...init?.headers },
    }).then(body);
  }

  importPptx(file: File, idempotencyKey: string): Promise<OoxmlImportReceipt> {
    const payload = new FormData();
    payload.append("file", file, file.name);
    return this.request("/api/v1/ppt/authored-vnext/packages/imports", {
      method: "POST",
      headers: { "Idempotency-Key": idempotencyKey, "X-Account-Deletion-Generation": "0" },
      body: payload,
    }) as Promise<OoxmlImportReceipt>;
  }

  fork(
    source: OoxmlPackageVersion,
    mode: "PRESERVE_EXACT" | "CORPORATE_NORMALIZE",
    idempotencyKey: string,
    masterContract?: Record<string, unknown>,
  ): Promise<OoxmlImportReceipt> {
    return this.json(`/${encodeURIComponent(source.packageId)}/forks`, {
      schemaVersion: "authored-vnext-ooxml-fork-command/v1",
      mode,
      expectedRevision: source.revision,
      expectedArtifactDigest: source.artifactDigest,
      idempotencyKey,
      expectedAccountGeneration: 0,
      ...(masterContract ? { masterContract } : {}),
    }) as Promise<OoxmlImportReceipt>;
  }

  async operate(source: OoxmlPackageVersion, operation: string, idempotencyKey: string, extra: Record<string, unknown> = {}): Promise<OoxmlOperationReceipt> {
    const queued = await this.json(`/${encodeURIComponent(source.packageId)}/operations`, {
      schemaVersion: "authored-vnext-ooxml-operation-command/v1",
      operation,
      expectedRevision: source.revision,
      expectedArtifactDigest: source.artifactDigest,
      idempotencyKey,
      expectedAccountGeneration: 0,
      ...extra,
    }) as OoxmlOperationReceipt;
    return this.request(`/api/v1/ppt/authored-vnext/packages/operations/${encodeURIComponent(queued.operationId)}/run`, { method: "POST" }) as Promise<OoxmlOperationReceipt>;
  }

  inspect(packageId: string): Promise<OoxmlPackageVersion> {
    return this.request(`/api/v1/ppt/authored-vnext/packages/${encodeURIComponent(packageId)}`) as Promise<OoxmlPackageVersion>;
  }

  readBack(packageId: string): Promise<OoxmlReadBack> {
    return this.request(`/api/v1/ppt/authored-vnext/packages/${encodeURIComponent(packageId)}/receipt`) as Promise<OoxmlReadBack>;
  }

  rollback(source: OoxmlPackageVersion, targetRevision: number, idempotencyKey: string): Promise<OoxmlPackageVersion> {
    return this.json(`/${encodeURIComponent(source.packageId)}/rollback`, {
      schemaVersion: "authored-vnext-ooxml-rollback-command/v1",
      targetRevision,
      expectedRevision: source.revision,
      expectedArtifactDigest: source.artifactDigest,
      idempotencyKey,
      expectedAccountGeneration: 0,
    }) as Promise<OoxmlPackageVersion>;
  }

  artifactUrl(packageId: string): string {
    return getApiUrl(`/api/v1/ppt/authored-vnext/packages/${encodeURIComponent(packageId)}/artifact`);
  }

  private json(path: string, value: Record<string, unknown>): Promise<unknown> {
    return this.request(`/api/v1/ppt/authored-vnext/packages${path}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(value),
    });
  }
}

export const authoredVnextPackageClient = new AuthoredVnextPackageClient();
