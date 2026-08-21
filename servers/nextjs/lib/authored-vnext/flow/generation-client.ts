import { getApiUrl } from "@/utils/api";

import type { MasterDigest, SlideSpecV2 } from "../master/contracts.ts";

export interface GenerationMasterPin {
  masterId: `master:${string}`;
  masterRevision: number;
  masterDigest: MasterDigest;
  layoutRegistryDigest: MasterDigest;
  fontManifestDigest: MasterDigest;
}

export interface ValidatedGenerationSlide {
  schemaVersion: "validated-slidespec/v2";
  slide: SlideSpecV2;
  checkpoint: {
    masterDigest: MasterDigest;
    sourceDigest: MasterDigest;
    bodySlotId: string;
  };
}

export interface GenerationDeckSnapshot {
  schemaVersion: "deck-generation-snapshot/v1";
  deckId: `deck:${string}`;
  revision: number;
  digest: MasterDigest;
  parentRevisionDigest?: MasterDigest | null;
  master: GenerationMasterPin;
  sourceDigest: MasterDigest;
  scene?: {
    schemaVersion: "deck-scene-ir/v1";
    deckId: `deck:${string}`;
    revision: number;
    sceneDigest: MasterDigest;
    compositionDigest: MasterDigest;
    slides: Array<{
      schemaVersion: "slide-scene-ir/v1";
      slideId: `slide:${string}`;
      revision: number;
      slideDigest: MasterDigest;
      elements: Array<Record<string, unknown>>;
    }>;
    assetManifest: Array<Record<string, unknown>>;
    dataManifest: Array<Record<string, unknown>>;
    capabilities: Array<Record<string, unknown>>;
  } | null;
  slides: ValidatedGenerationSlide[];
  createdAt: string;
}

export interface GenerationResult {
  schemaVersion: "generation-result/v1";
  operationId: `operation:${string}`;
  checkpoint: Record<string, unknown> & {
    checkpointDigest: MasterDigest;
    master: GenerationMasterPin;
  };
  deck: GenerationDeckSnapshot;
  quality: Record<string, unknown> & { passed: boolean };
  exportArtifactDigest: MasterDigest;
}

export interface RegenerationCandidate {
  lifecycle: "detached";
  candidateDigest: MasterDigest;
  parentDeckDigest: MasterDigest;
  slide: ValidatedGenerationSlide;
  slideGate: Record<string, unknown> & { passed: boolean };
}

export interface RegenerationResult {
  schemaVersion: "regeneration-result/v1";
  operationId: `operation:${string}`;
  candidate: RegenerationCandidate;
  deck: GenerationDeckSnapshot;
  quality: Record<string, unknown> & { passed: boolean };
  publishedByCas: true;
}

export interface SceneMutationResult {
  schemaVersion: "scene-mutation-result/v1";
  operationId: `operation:${string}`;
  mutation: "text.replace" | "geometry.update" | "asset.set" | "data.update";
  slideId: `slide:${string}`;
  elementId: `element:${string}`;
  slideDigest: MasterDigest;
  sceneDigest: MasterDigest;
  deck: GenerationDeckSnapshot;
  quality: Record<string, unknown> & { passed: boolean };
  publishedByCas: true;
}

export interface ExportJob {
  schemaVersion: "authored-vnext-export-job/v1";
  jobId: string;
  deckId: string;
  state: "queued" | "running" | "succeeded" | "failed" | "cancelled";
  operationFence: number;
  errorCode?: string | null;
  artifact?: { publicationRevision: number; digest: MasterDigest } | null;
}

type FetchLike = typeof fetch;

function record(value: unknown): Record<string, unknown> {
  if (typeof value !== "object" || value === null || Array.isArray(value)) {
    throw new Error("authored_vnext_invalid_response");
  }
  return value as Record<string, unknown>;
}

async function payload(response: Response): Promise<unknown> {
  const body = await response.json().catch(() => ({}));
  if (!response.ok) {
    const source = record(body);
    const detail = typeof source.detail === "object" && source.detail !== null
      ? source.detail as Record<string, unknown>
      : source;
    const code = typeof detail.code === "string"
      ? detail.code
      : typeof source.detail === "string"
        ? source.detail
        : "authored_vnext_request_failed";
    const error = new Error(code) as Error & { status?: number; code?: string };
    error.status = response.status;
    error.code = code;
    throw error;
  }
  return body;
}

export class AuthoredVnextGenerationClient {
  constructor(private readonly fetchImpl: FetchLike = globalThis.fetch.bind(globalThis)) {}

  private async request(path: string, init?: RequestInit): Promise<unknown> {
    return payload(await this.fetchImpl(getApiUrl(path), {
      cache: "no-store",
      credentials: "include",
      ...init,
      headers: {
        Accept: "application/json",
        ...(init?.body ? { "Content-Type": "application/json" } : {}),
        ...init?.headers,
      },
    }));
  }

  masterPin(masterId: string, revision: number): Promise<GenerationMasterPin> {
    return this.request(
      `/api/v1/ppt/authored-vnext/generation/master-pins/${encodeURIComponent(masterId)}/${revision}`,
    ) as Promise<GenerationMasterPin>;
  }

  generate(command: Record<string, unknown>, idempotencyKey: string): Promise<GenerationResult> {
    return this.request("/api/v1/ppt/authored-vnext/generation/decks", {
      method: "POST", body: JSON.stringify({ idempotencyKey, command }),
    }) as Promise<GenerationResult>;
  }

  resume(command: Record<string, unknown>, idempotencyKey: string): Promise<GenerationResult> {
    return this.request("/api/v1/ppt/authored-vnext/generation/resume", {
      method: "POST", body: JSON.stringify({ idempotencyKey, command }),
    }) as Promise<GenerationResult>;
  }

  getDeck(deckId: string): Promise<{ deck: GenerationDeckSnapshot }> {
    return this.request(
      `/api/v1/ppt/authored-vnext/generation/decks/${encodeURIComponent(deckId)}`,
    ) as Promise<{ deck: GenerationDeckSnapshot }>;
  }

  prepareRegeneration(
    deckId: string,
    slideId: string,
    command: Record<string, unknown>,
    idempotencyKey: string,
  ): Promise<{ operationId: string; candidate: RegenerationCandidate }> {
    return this.request(
      `/api/v1/ppt/authored-vnext/generation/decks/${encodeURIComponent(deckId)}/slides/${encodeURIComponent(slideId)}/regeneration-candidates`,
      { method: "POST", body: JSON.stringify({ idempotencyKey, command }) },
    ) as Promise<{ operationId: string; candidate: RegenerationCandidate }>;
  }

  publishRegeneration(
    deckId: string,
    candidateDigest: string,
    operationId: string,
    idempotencyKey: string,
  ): Promise<RegenerationResult> {
    return this.request(
      `/api/v1/ppt/authored-vnext/generation/decks/${encodeURIComponent(deckId)}/regeneration-candidates/${encodeURIComponent(candidateDigest)}/publish`,
      { method: "POST", body: JSON.stringify({ idempotencyKey, operationId }) },
    ) as Promise<RegenerationResult>;
  }

  rollback(deckId: string, command: Record<string, unknown>, idempotencyKey: string) {
    return this.request(
      `/api/v1/ppt/authored-vnext/generation/decks/${encodeURIComponent(deckId)}/rollback`,
      { method: "POST", body: JSON.stringify({ idempotencyKey, command }) },
    ) as Promise<{ deck: GenerationDeckSnapshot }>;
  }

  reorder(deckId: string, command: Record<string, unknown>, idempotencyKey: string) {
    return this.request(
      `/api/v1/ppt/authored-vnext/generation/decks/${encodeURIComponent(deckId)}/reorder`,
      { method: "POST", body: JSON.stringify({ idempotencyKey, command }) },
    ) as Promise<{ deck: GenerationDeckSnapshot }>;
  }

  mutateScene(
    deckId: string,
    command: Record<string, unknown>,
    idempotencyKey: string,
  ): Promise<SceneMutationResult> {
    return this.request(
      `/api/v1/ppt/authored-vnext/generation/decks/${encodeURIComponent(deckId)}/scene-mutations`,
      { method: "POST", body: JSON.stringify({ idempotencyKey, command }) },
    ) as Promise<SceneMutationResult>;
  }

  createExport(command: Record<string, unknown>): Promise<ExportJob> {
    return this.request("/api/v1/ppt/authored-vnext/export/jobs", {
      method: "POST", body: JSON.stringify(command),
    }) as Promise<ExportJob>;
  }

  runExport(jobId: string): Promise<ExportJob> {
    return this.request(
      `/api/v1/ppt/authored-vnext/export/jobs/${encodeURIComponent(jobId)}/run`,
      { method: "POST" },
    ) as Promise<ExportJob>;
  }

  artifactUrl(deckId: string): string {
    return getApiUrl(
      `/api/v1/ppt/authored-vnext/export/decks/${encodeURIComponent(deckId)}/artifact`,
    );
  }
}
