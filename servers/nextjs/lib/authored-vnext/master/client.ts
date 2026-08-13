import { getApiUrl } from "@/utils/api";

import {
  AUTHORED_VNEXT_MASTER_ENDPOINTS,
  assertDeckMasterContract,
  isMasterDigest,
  isRecord,
  type CreateDeckRequest,
  type CreateDraftRequest,
  type BrandAsset,
  type DeckSnapshot,
  type MasterDigest,
  type MasterPreviewEnvelope,
  type MasterRevisionEnvelope,
  type MasterStatus,
  type MasterValidation,
  type PreviewRequest,
  type RegenerateBodyRequest,
  type RegenerateBodyResponse,
  type RevisionCasRequest,
  type ValidationIssue,
  type VisualDirection,
} from "./contracts.ts";

type FetchLike = typeof fetch;

const EMPTY_VALIDATION: MasterValidation = {
  valid: true,
  errors: [],
  furnitureBodyDuplicates: 0,
  writableOverlaps: 0,
  titleFitFailures: 0,
};

export class MasterApiError extends Error {
  readonly status: number;
  readonly code: string;

  constructor(
    message: string,
    status: number,
    code: string,
  ) {
    super(message);
    this.name = "MasterApiError";
    this.status = status;
    this.code = code;
  }
}

function stringValue(record: Record<string, unknown>, ...keys: string[]): string | undefined {
  for (const key of keys) {
    if (typeof record[key] === "string") return record[key];
  }
  return undefined;
}

function numberValue(record: Record<string, unknown>, ...keys: string[]): number | undefined {
  for (const key of keys) {
    if (typeof record[key] === "number") return record[key];
  }
  return undefined;
}

function responseData(payload: unknown): Record<string, unknown> {
  if (!isRecord(payload)) throw new Error("Master API returned a non-object response");
  return isRecord(payload.data) ? payload.data : payload;
}

function validationIssue(value: unknown): ValidationIssue | null {
  if (!isRecord(value)) return null;
  const code = stringValue(value, "code") ?? "validation_error";
  const path = stringValue(value, "path") ?? "contract";
  const message = stringValue(value, "message", "safeMessage", "safe_message") ?? code;
  return { code, path, message };
}

export function normalizeMasterValidation(value: unknown): MasterValidation {
  if (!isRecord(value)) return { ...EMPTY_VALIDATION };
  const errors = Array.isArray(value.errors)
    ? value.errors.map(validationIssue).filter((issue): issue is ValidationIssue => issue !== null)
    : [];
  return {
    valid: typeof value.valid === "boolean" ? value.valid : errors.length === 0,
    errors,
    furnitureBodyDuplicates:
      numberValue(value, "furnitureBodyDuplicates", "furniture_body_duplicates") ?? 0,
    writableOverlaps: numberValue(value, "writableOverlaps", "writable_overlaps") ?? 0,
    titleFitFailures: numberValue(value, "titleFitFailures", "title_fit_failures") ?? 0,
  };
}

function requireDigest(value: unknown, field: string): MasterDigest {
  if (!isMasterDigest(value)) throw new Error(`Master API returned an invalid ${field}`);
  return value;
}

export function normalizeMasterRevision(payload: unknown): MasterRevisionEnvelope {
  const data = responseData(payload);
  const masterId = stringValue(data, "masterId", "master_id");
  const revision = numberValue(data, "revision");
  const status = stringValue(data, "status");
  if (!masterId || revision === undefined || (status !== "draft" && status !== "published")) {
    throw new Error("Master API returned an invalid revision envelope");
  }
  return {
    masterId,
    revision,
    digest: requireDigest(data.digest, "digest"),
    status: status as MasterStatus,
    validation: normalizeMasterValidation(data.validation),
    ...(isRecord(data.contract)
      ? { contract: data.contract as unknown as MasterRevisionEnvelope["contract"] }
      : {}),
    ...(isRecord(data.visualDirection)
      ? { visualDirection: data.visualDirection as unknown as VisualDirection }
      : isRecord(data.visual_direction)
        ? { visualDirection: data.visual_direction as unknown as VisualDirection }
        : {}),
  };
}

export function normalizeMasterPreview(payload: unknown): MasterPreviewEnvelope {
  const data = responseData(payload);
  const slide = isRecord(data.slide) ? data.slide : null;
  const canonicalInputDigest = requireDigest(
    data.canonicalInputDigest ?? data.canonicalDigest ?? data.canonical_input_digest,
    "canonical input digest",
  );
  return {
    canonicalInputDigest,
    pngRenderRequest: isRecord(data.pngRenderRequest)
      ? data.pngRenderRequest
      : isRecord(data.png_render_request)
        ? data.png_render_request
        : {},
    exportPlan: isRecord(data.exportPlan)
      ? data.exportPlan
      : isRecord(data.export_plan)
        ? data.export_plan
        : {},
    validation: normalizeMasterValidation(data.validation),
    ...(slide ? { slide: slide as unknown as NonNullable<MasterPreviewEnvelope["slide"]> } : {}),
    ...(slide && isRecord(slide.titleFit)
      ? { titleFit: slide.titleFit as unknown as NonNullable<MasterPreviewEnvelope["titleFit"]> }
      : {}),
  };
}

function normalizeSlideSpec(value: unknown): DeckSnapshot["slides"][number] {
  if (!isRecord(value) || value.schemaVersion !== "slidespec/v2") {
    throw new Error("Master API returned an invalid slide spec");
  }
  const slideId = stringValue(value, "slideId", "slide_id");
  const layoutId = stringValue(value, "layoutId", "layout_id");
  const revision = numberValue(value, "revision");
  const body = stringValue(value, "body");
  const masterDigest = value.masterDigest ?? value.master_digest;
  const contentDigest = value.contentDigest ?? value.content_digest;
  const specDigest = value.specDigest ?? value.spec_digest;
  const titleFit = value.titleFit ?? value.title_fit;
  if (
    !slideId?.startsWith("slide:") ||
    !layoutId ||
    revision === undefined ||
    revision < 1 ||
    body === undefined ||
    !isRecord(value.title) ||
    !isRecord(value.ownership) ||
    !isRecord(titleFit) ||
    !isMasterDigest(masterDigest) ||
    !isMasterDigest(contentDigest) ||
    !isMasterDigest(specDigest)
  ) {
    throw new Error("Master API returned an invalid slide spec");
  }
  return value as unknown as DeckSnapshot["slides"][number];
}

export function normalizeDeckSnapshot(payload: unknown): DeckSnapshot {
  const data = responseData(payload);
  const deckId = stringValue(data, "deckId", "deck_id");
  const masterId = stringValue(data, "masterId", "master_id");
  const revision = numberValue(data, "revision");
  const masterRevision = numberValue(data, "masterRevision", "master_revision");
  const digest = data.digest;
  const masterDigest = data.masterDigest ?? data.master_digest;
  if (
    data.schemaVersion !== "deck-document/v1" ||
    !deckId?.startsWith("deck:") ||
    !masterId ||
    revision === undefined ||
    revision < 1 ||
    masterRevision === undefined ||
    masterRevision < 1 ||
    !isMasterDigest(digest) ||
    !isMasterDigest(masterDigest) ||
    !Array.isArray(data.slides) ||
    data.slides.length === 0
  ) {
    throw new Error("Master API returned an invalid deck snapshot");
  }
  return {
    schemaVersion: "deck-document/v1",
    deckId: deckId as DeckSnapshot["deckId"],
    revision,
    digest,
    masterId,
    masterRevision,
    masterDigest,
    ...(isRecord(data.visualDirection)
      ? { visualDirection: data.visualDirection as unknown as DeckSnapshot["visualDirection"] }
      : isRecord(data.visual_direction)
        ? { visualDirection: data.visual_direction as unknown as DeckSnapshot["visualDirection"] }
        : {}),
    slides: data.slides.map(normalizeSlideSpec),
  };
}

export function normalizeRegenerateBodyResponse(payload: unknown): RegenerateBodyResponse {
  const data = responseData(payload);
  const candidateDigest = data.candidateDigest ?? data.candidate_digest;
  if (!isMasterDigest(candidateDigest)) {
    throw new Error("Master API returned an invalid regeneration candidate");
  }
  return {
    candidateDigest,
    validation: normalizeMasterValidation(data.validation),
    deck: normalizeDeckSnapshot(data.deck),
  };
}

function errorCode(payload: unknown): string {
  if (!isRecord(payload)) return "authored_vnext_master_request_failed";
  const detail = isRecord(payload.detail) ? payload.detail : null;
  const error = isRecord(payload.error) ? payload.error : null;
  return (
    stringValue(payload, "code") ??
    (detail && stringValue(detail, "code")) ??
    (error && stringValue(error, "code")) ??
    (typeof payload.detail === "string" ? payload.detail : undefined) ??
    "authored_vnext_master_request_failed"
  );
}

async function parseJson(response: Response): Promise<unknown> {
  let payload: unknown = {};
  try {
    payload = await response.json();
  } catch {
    // A stable local error still protects the UI from an invalid upstream body.
  }
  if (!response.ok) {
    const code = errorCode(payload);
    throw new MasterApiError(code, response.status, code);
  }
  return payload;
}

export class AuthoredVnextMasterClient {
  private readonly fetchImpl: FetchLike;

  constructor(fetchImpl?: FetchLike) {
    // Some browser/test runtimes require fetch to be invoked with its global
    // receiver. Keep injected clients untouched while avoiding an unbound
    // window.fetch reference for the default client.
    this.fetchImpl = fetchImpl ?? ((input, init) => globalThis.fetch(input, init));
  }

  private async request(path: string, init?: RequestInit): Promise<unknown> {
    const response = await this.fetchImpl(getApiUrl(path), {
      cache: "no-store",
      ...init,
      headers: {
        Accept: "application/json",
        ...(init?.body ? { "Content-Type": "application/json" } : {}),
        ...init?.headers,
      },
    });
    return parseJson(response);
  }

  async status(signal?: AbortSignal): Promise<Record<string, unknown>> {
    return responseData(await this.request(AUTHORED_VNEXT_MASTER_ENDPOINTS.status, { signal }));
  }

  async uploadLogo(file: File, signal?: AbortSignal): Promise<BrandAsset> {
    const form = new FormData();
    form.append("file", file, file.name);
    const response = await this.fetchImpl(
      getApiUrl(AUTHORED_VNEXT_MASTER_ENDPOINTS.logoAsset),
      {
        method: "POST",
        body: form,
        credentials: "include",
        signal,
        headers: { Accept: "application/json" },
      },
    );
    const payload = responseData(await parseJson(response));
    const assetId = stringValue(payload, "assetId");
    const digest = payload.digest;
    if (!assetId?.startsWith("asset:") || !isMasterDigest(digest)) {
      throw new Error("Master logo upload returned an invalid receipt");
    }
    return { assetId, digest, altText: file.name };
  }

  async getRevision(masterId: string, revision: number, signal?: AbortSignal): Promise<MasterRevisionEnvelope> {
    return normalizeMasterRevision(await this.request(
      AUTHORED_VNEXT_MASTER_ENDPOINTS.revision(masterId, revision),
      { signal },
    ));
  }

  async createDraft(request: CreateDraftRequest, signal?: AbortSignal): Promise<MasterRevisionEnvelope> {
    assertDeckMasterContract(request.contract);
    return normalizeMasterRevision(await this.request(AUTHORED_VNEXT_MASTER_ENDPOINTS.drafts, {
      method: "POST",
      body: JSON.stringify(request),
      signal,
    }));
  }

  async validateDraft(masterId: string, request: RevisionCasRequest, signal?: AbortSignal): Promise<MasterValidation> {
    const payload = await this.request(AUTHORED_VNEXT_MASTER_ENDPOINTS.validate(masterId), {
      method: "POST",
      body: JSON.stringify(request),
      signal,
    });
    const data = responseData(payload);
    return normalizeMasterValidation(data.validation ?? data);
  }

  async publishDraft(masterId: string, request: RevisionCasRequest, signal?: AbortSignal): Promise<MasterRevisionEnvelope> {
    return normalizeMasterRevision(await this.request(AUTHORED_VNEXT_MASTER_ENDPOINTS.publish(masterId), {
      method: "POST",
      body: JSON.stringify(request),
      signal,
    }));
  }

  async preview(request: PreviewRequest, signal?: AbortSignal): Promise<MasterPreviewEnvelope> {
    const { slideSpec, ...master } = request;
    return normalizeMasterPreview(await this.request(AUTHORED_VNEXT_MASTER_ENDPOINTS.preview, {
      method: "POST",
      body: JSON.stringify({ ...master, slide: slideSpec }),
      signal,
    }));
  }

  async createDeck(request: CreateDeckRequest, signal?: AbortSignal): Promise<DeckSnapshot> {
    return normalizeDeckSnapshot(await this.request(AUTHORED_VNEXT_MASTER_ENDPOINTS.decks, {
      method: "POST",
      body: JSON.stringify(request),
      signal,
    }));
  }

  async regenerateBody(
    deckId: string,
    slideId: string,
    request: RegenerateBodyRequest,
    signal?: AbortSignal,
  ): Promise<RegenerateBodyResponse> {
    if (request.scope !== "bodyOnly") throw new Error("Only bodyOnly regeneration is allowed");
    return normalizeRegenerateBodyResponse(await this.request(AUTHORED_VNEXT_MASTER_ENDPOINTS.regenerateBody(deckId, slideId), {
      method: "POST",
      body: JSON.stringify(request),
      signal,
    }));
  }
}
