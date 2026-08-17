import { getApiUrl } from "@/utils/api";

import {
  AUTHORED_VNEXT_MASTER_ENDPOINTS,
  assertDeckMasterContract,
  isMasterDigest,
  isRecord,
  type CreateDeckRequest,
  type CreateDraftRequest,
  type ApplyMasterToPresentationRequest,
  type AiMasterAnalysis,
  type AiMasterCandidateDecision,
  type AiMasterEvidence,
  type AiMasterLayoutAction,
  type AiMasterLayoutCandidate,
  type AiMasterLayoutDifference,
  type AiMasterPurpose,
  type BrandAsset,
  type CompanyStandardDefault,
  type DeckSnapshot,
  type MasterDigest,
  type MasterCatalogPage,
  type MasterCatalogSummary,
  type MasterPreviewEnvelope,
  type MasterRevisionEnvelope,
  type MasterStatus,
  type MasterValidation,
  type PreviewRequest,
  type RegenerateBodyRequest,
  type RegenerateBodyResponse,
  type RegisterMasterRequest,
  type RevisionCasRequest,
  type SaveAiMasterAnalysisRequest,
  type UpdateAiMasterAnalysisRequest,
  type ValidationIssue,
  type VisualDirection,
} from "./contracts.ts";

type FetchLike = typeof fetch;

const MISSING_SERVER_VALIDATION: MasterValidation = {
  // A browser preflight is helpful for editor feedback, but it is never proof
  // that the server sealed the same contract.  Keep an omitted or malformed
  // server result fail-closed so it cannot be used as a publication receipt.
  valid: false,
  errors: [{
    code: "server_validation_missing",
    path: "validation",
    message: "서버 검증 결과가 없어 저장하거나 게시할 수 없습니다.",
  }],
  furnitureBodyDuplicates: 0,
  writableOverlaps: 0,
  titleFitFailures: 0,
};

/**
 * A successful server validation is bound to the exact immutable master
 * revision and digest that were supplied to the validation endpoint.
 */
export interface ValidatedMasterDraft {
  masterId: string;
  expectedRevision: number;
  expectedDigest: MasterDigest;
  validation: MasterValidation;
}

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
  const path = stringValue(value, "path") ?? fieldPath(value.loc) ?? "contract";
  const serverMessage = stringValue(value, "message", "safeMessage", "safe_message") ?? code;
  // Retain the server's diagnostic verbatim after a Korean user-facing
  // explanation.  In particular, do not replace its field path with a
  // generic form label: the path identifies the exact canonical field.
  const message = serverMessage.startsWith("검증 오류")
    ? serverMessage
    : `검증 오류: ${serverMessage}`;
  return { code, path, message };
}

export function normalizeMasterValidation(value: unknown): MasterValidation {
  if (!isRecord(value)) return structuredClone(MISSING_SERVER_VALIDATION);
  if (typeof value.valid !== "boolean" || !Array.isArray(value.errors)) {
    return structuredClone(MISSING_SERVER_VALIDATION);
  }
  const errors = Array.isArray(value.errors)
    ? value.errors.map(validationIssue).filter((issue): issue is ValidationIssue => issue !== null)
    : [];
  if (value.errors.length !== errors.length) {
    return {
      ...structuredClone(MISSING_SERVER_VALIDATION),
      errors: [{
        code: "server_validation_malformed",
        path: "validation.errors",
        message: "서버 검증 오류 형식이 올바르지 않아 저장하거나 게시할 수 없습니다.",
      }],
    };
  }
  return {
    // The server's decision is authoritative.  Do not infer validity from an
    // empty array because that would silently turn a broken response into a
    // successful save/publish path.
    valid: value.valid,
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

export function normalizeCompanyStandardDefault(
  payload: unknown,
): CompanyStandardDefault {
  const data = responseData(payload);
  const schemaVersion = stringValue(data, "schemaVersion", "schema_version");
  const source = stringValue(data, "source");
  const name = data.name;
  const pin = data.companyStandard ?? data.company_standard ?? null;
  if (
    schemaVersion !== "company-standard-default/v1"
    || source !== "serverDefault"
    || (name !== null && name !== undefined && typeof name !== "string")
  ) {
    throw new Error("Master API returned an invalid company-standard default");
  }
  if (pin === null) {
    return {
      schemaVersion,
      source,
      companyStandard: null,
      name: name ?? null,
    };
  }
  if (!isRecord(pin)) {
    throw new Error("Master API returned an invalid company-standard pin");
  }
  const masterId = stringValue(pin, "masterId", "master_id");
  const revision = numberValue(pin, "revision");
  const digest = pin.digest;
  if (
    !masterId
    || revision === undefined
    || !Number.isInteger(revision)
    || revision < 1
    || !isMasterDigest(digest)
  ) {
    throw new Error("Master API returned an invalid company-standard pin");
  }
  return {
    schemaVersion,
    source,
    companyStandard: { masterId, revision, digest },
    name: name ?? null,
  };
}

function normalizeMasterCatalogSummary(value: unknown): MasterCatalogSummary {
  if (!isRecord(value)) throw new Error("Master API returned an invalid catalog entry");
  const masterId = stringValue(value, "masterId", "master_id");
  const name = stringValue(value, "name");
  const semanticVersion = stringValue(value, "semanticVersion", "semantic_version");
  const primaryFont = stringValue(value, "primaryFont", "primary_font");
  const updatedTime = stringValue(value, "updatedTime", "updated_time", "updatedAt", "updated_at");
  const layoutCount = numberValue(value, "layoutCount", "layout_count");
  const publishedRevision = value.publishedRevision ?? value.published_revision ?? null;
  const publishedDigest = value.publishedDigest ?? value.published_digest ?? null;
  if (
    !masterId || !name || !semanticVersion || !primaryFont || !updatedTime ||
    layoutCount === undefined || layoutCount < 0 ||
    (publishedRevision !== null && (!Number.isInteger(publishedRevision) || publishedRevision < 1)) ||
    (publishedDigest !== null && !isMasterDigest(publishedDigest))
  ) {
    throw new Error("Master API returned an invalid catalog entry");
  }
  return {
    masterId,
    name,
    semanticVersion,
    publishedRevision: publishedRevision as number | null,
    publishedDigest: publishedDigest as MasterDigest | null,
    hasDraft: Boolean(value.hasDraft ?? value.has_draft),
    logo: isRecord(value.logo) ? value.logo as MasterCatalogSummary["logo"] : null,
    primaryFont,
    layoutCount,
    updatedTime,
  };
}

export function normalizeMasterCatalogPage(payload: unknown): MasterCatalogPage {
  const data = responseData(payload);
  const items = data.items;
  const nextCursor = data.nextCursor ?? data.next_cursor ?? null;
  if (!Array.isArray(items) || (nextCursor !== null && typeof nextCursor !== "string")) {
    throw new Error("Master API returned an invalid catalog page");
  }
  return { items: items.map(normalizeMasterCatalogSummary), nextCursor };
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

function errorMessage(payload: unknown, code: string): string {
  if (!isRecord(payload) || code !== "request_validation_error" || !Array.isArray(payload.detail)) {
    return code;
  }
  const issue = payload.detail.find(isRecord);
  if (!issue) return code;
  const message = stringValue(issue, "msg");
  const location = fieldPath(issue.loc) ?? "";
  if (!message) return code;
  return `입력값을 확인해주세요${location ? ` (${location})` : ""}: ${message}`;
}

function fieldPath(value: unknown): string | null {
  if (!Array.isArray(value)) return null;
  const parts = value
    .filter((part): part is string | number => typeof part === "string" || typeof part === "number")
    .filter((part, index) => !(index === 0 && part === "body"));
  return parts.length > 0 ? parts.join(".") : null;
}

function assertRevisionCas(request: RevisionCasRequest): void {
  if (!Number.isInteger(request.expectedRevision) || request.expectedRevision < 1) {
    throw new Error("검증할 마스터 revision이 올바르지 않습니다.");
  }
  if (!isMasterDigest(request.expectedDigest)) {
    throw new Error("검증할 마스터 digest가 올바르지 않습니다.");
  }
}

function assertValidatedDraft(ticket: ValidatedMasterDraft): void {
  if (!ticket.masterId || !ticket.masterId.startsWith("master:")) {
    throw new Error("검증된 마스터 식별자가 올바르지 않습니다.");
  }
  assertRevisionCas(ticket);
  if (!ticket.validation.valid) {
    const details = ticket.validation.errors
      .map((issue) => `${issue.path}: ${issue.message}`)
      .join("; ");
    throw new Error(`서버 검증을 통과하지 못해 게시할 수 없습니다.${details ? ` ${details}` : ""}`);
  }
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
    throw new MasterApiError(errorMessage(payload, code), response.status, code);
  }
  return payload;
}

function recordArray(value: unknown): Record<string, unknown>[] {
  return Array.isArray(value) ? value.filter(isRecord) : [];
}

function numberArray(value: unknown): number[] {
  return Array.isArray(value)
    ? value.filter((item): item is number => Number.isInteger(item) && item > 0)
    : [];
}

function normalizeAiEvidence(value: unknown): AiMasterEvidence | null {
  if (!isRecord(value)) return null;
  const slideNumber = numberValue(value, "slideNumber", "slide_number");
  if (!slideNumber || slideNumber < 1) return null;
  const bounds = isRecord(value.bounds) ? value.bounds : null;
  const rotation = numberValue(value, "rotation");
  const zIndex = numberValue(value, "zIndex", "z_index");
  if (!bounds || rotation === undefined || zIndex === undefined) return null;
  return {
    slideNumber,
    bounds: bounds as unknown as AiMasterEvidence["bounds"],
    rotation,
    zIndex,
    ...(stringValue(value, "layoutPart", "layout_part") ? { layoutPart: stringValue(value, "layoutPart", "layout_part") } : {}),
    ...(stringValue(value, "masterPart", "master_part") ? { masterPart: stringValue(value, "masterPart", "master_part") } : {}),
    ...(stringValue(value, "themePart", "theme_part") ? { themePart: stringValue(value, "themePart", "theme_part") } : {}),
  };
}

function normalizeAiDecision(value: unknown): AiMasterCandidateDecision | null {
  if (!isRecord(value)) return null;
  const candidateId = stringValue(value, "candidateId", "candidate_id");
  const action = stringValue(value, "action");
  if (
    !candidateId ||
    !["pending", "fixed", "variable", "aiZone", "remove"].includes(action ?? "")
  ) return null;
  const emptyBehavior = stringValue(value, "emptyBehavior", "empty_behavior");
  return {
    candidateId,
    action: action as AiMasterCandidateDecision["action"],
    ...(stringValue(value, "instruction") ? { instruction: stringValue(value, "instruction") } : {}),
    ...(stringValue(value, "exampleText", "example_text") ? { exampleText: stringValue(value, "exampleText", "example_text") } : {}),
    ...(["hidden", "blank", "fallback"].includes(emptyBehavior ?? "")
      ? { emptyBehavior: emptyBehavior as NonNullable<AiMasterCandidateDecision["emptyBehavior"]> }
      : {}),
    ...(stringValue(value, "fallbackText", "fallback_text") ? { fallbackText: stringValue(value, "fallbackText", "fallback_text") } : {}),
    ...(numberValue(value, "maxChars", "max_chars") !== undefined ? { maxChars: numberValue(value, "maxChars", "max_chars") } : {}),
    ...(stringValue(value, "overrideText", "override_text") ? { overrideText: stringValue(value, "overrideText", "override_text") } : {}),
    ...(stringValue(value, "variableName", "variable_name") ? { variableName: stringValue(value, "variableName", "variable_name") } : {}),
  };
}

function normalizeAiLayoutAction(value: unknown): AiMasterLayoutAction | null {
  if (!isRecord(value)) return null;
  const action = stringValue(value, "action");
  const layoutIdsValue = value.layoutIds ?? value.layout_ids;
  if (!Array.isArray(layoutIdsValue) || layoutIdsValue.some((item) => typeof item !== "string") || !["merge", "split", "exclude", "include", "reanalyze"].includes(action ?? "")) return null;
  return {
    action: action as AiMasterLayoutAction["action"],
    layoutIds: layoutIdsValue as string[],
    slideNumbers: numberArray(value.slideNumbers ?? value.slide_numbers),
    ...(stringValue(value, "name") ? { name: stringValue(value, "name") } : {}),
  };
}

function normalizeAiDifference(value: unknown): AiMasterLayoutDifference | null {
  if (typeof value === "string") return { code: "difference", message: value };
  if (!isRecord(value)) return null;
  const code = stringValue(value, "code") ?? "difference";
  const message = stringValue(value, "message", "description") ?? code;
  const severity = stringValue(value, "severity");
  return {
    code,
    message,
    ...(severity === "info" || severity === "warning" ? { severity } : {}),
  };
}

function normalizeAiLayout(value: Record<string, unknown>): AiMasterLayoutCandidate | null {
  const id = stringValue(value, "id");
  const name = stringValue(value, "name");
  const kind = stringValue(value, "kind");
  const representativeSlide = numberValue(value, "representativeSlide", "representative_slide");
  const confidence = numberValue(value, "confidence");
  if (!id || !name || !kind || !representativeSlide || confidence === undefined) return null;
  return {
    id,
    name,
    kind: kind as AiMasterLayoutCandidate["kind"],
    representativeSlide,
    slideNumbers: numberArray(value.slideNumbers ?? value.slide_numbers),
    confidence,
    differences: (Array.isArray(value.differences) ? value.differences : [])
      .map(normalizeAiDifference)
      .filter((item): item is AiMasterLayoutDifference => item !== null),
    slots: Array.isArray(value.slots) ? value.slots as AiMasterLayoutCandidate["slots"] : [],
    ...(stringValue(value, "layoutPart", "layout_part") ? { layoutPart: stringValue(value, "layoutPart", "layout_part") } : {}),
    ...(stringValue(value, "masterPart", "master_part") ? { masterPart: stringValue(value, "masterPart", "master_part") } : {}),
    ...(stringValue(value, "themePart", "theme_part") ? { themePart: stringValue(value, "themePart", "theme_part") } : {}),
    excluded: Boolean(value.excluded),
  };
}

export function normalizeAiMasterAnalysis(payload: unknown): AiMasterAnalysis {
  const data = responseData(payload);
  const analysisId = stringValue(data, "analysisId", "analysis_id");
  const status = stringValue(data, "status");
  const purpose = stringValue(data, "purpose");
  const source = isRecord(data.source) ? data.source : null;
  const summary = isRecord(data.summary) ? data.summary : null;
  const contractDraft = data.contractDraft ?? data.contract_draft;
  if (
    !analysisId ||
    !["completed", "partial"].includes(status ?? "") ||
    !["faithful", "cleanup", "rebrand"].includes(purpose ?? "") ||
    !source ||
    !summary ||
    !isRecord(contractDraft)
  ) throw new Error("Master analysis API returned an invalid analysis");
  const filename = stringValue(source, "filename");
  const digest = source.digest;
  const slideCount = numberValue(source, "slideCount", "slide_count");
  const widthPx = numberValue(source, "widthPx", "width_px");
  const heightPx = numberValue(source, "heightPx", "height_px");
  if (!filename || !isMasterDigest(digest) || slideCount === undefined || widthPx === undefined || heightPx === undefined) {
    throw new Error("Master analysis API returned an invalid source receipt");
  }
  const integerSummary = (camel: string, snake: string) => numberValue(summary, camel, snake) ?? 0;
  const masterRevisionValue = data.masterRevision ?? data.master_revision;
  const validationValue = data.validation;
  return {
    analysisId,
    status: status as AiMasterAnalysis["status"],
    purpose: purpose as AiMasterPurpose,
    source: {
      filename,
      digest,
      slideCount,
      widthPx,
      heightPx,
      assets: recordArray(source.assets).flatMap((asset) => {
        const assetId = stringValue(asset, "assetId", "asset_id");
        const assetDigest = asset.digest;
        const mediaType = stringValue(asset, "mediaType", "media_type");
        const byteLength = numberValue(asset, "byteLength", "byte_length");
        const partName = stringValue(asset, "partName", "part_name");
        return assetId && isMasterDigest(assetDigest) && mediaType && byteLength !== undefined && partName
          ? [{ assetId, digest: assetDigest, mediaType, byteLength, partName }]
          : [];
      }),
      assetRelationships: recordArray(source.assetRelationships ?? source.asset_relationships).flatMap((relationship) => {
        const slideNumber = numberValue(relationship, "slideNumber", "slide_number");
        const relationshipId = stringValue(relationship, "relationshipId", "relationship_id");
        const assetId = stringValue(relationship, "assetId", "asset_id");
        const fit = stringValue(relationship, "fit");
        if (!slideNumber || !relationshipId || !assetId || (fit !== "contain" && fit !== "cover")) return [];
        return [{
          slideNumber,
          relationshipId,
          assetId,
          fit,
          ...(isRecord(relationship.crop) ? { crop: relationship.crop as unknown as NonNullable<AiMasterAnalysis["source"]["assetRelationships"][number]["crop"]> } : {}),
        }];
      }),
    },
    summary: {
      designFamilies: integerSummary("designFamilies", "design_families"),
      layoutCandidates: integerSummary("layoutCandidates", "layout_candidates"),
      fontCount: integerSummary("fontCount", "font_count"),
      colorCount: integerSummary("colorCount", "color_count"),
      repeatedElementCount: integerSummary("repeatedElementCount", "repeated_element_count"),
      aiZoneCandidateCount: integerSummary("aiZoneCandidateCount", "ai_zone_candidate_count"),
      lowConfidenceCount: integerSummary("lowConfidenceCount", "low_confidence_count"),
    },
    fonts: recordArray(data.fonts).map((item) => ({ family: stringValue(item, "family") ?? "", count: numberValue(item, "count") ?? 0, source: stringValue(item, "source") as AiMasterAnalysis["fonts"][number]["source"] })),
    colors: recordArray(data.colors).map((item) => ({ hex: stringValue(item, "hex") ?? "", count: numberValue(item, "count") ?? 0, source: stringValue(item, "source") as AiMasterAnalysis["colors"][number]["source"] })),
    repeatedElements: recordArray(data.repeatedElements ?? data.repeated_elements).flatMap((item) => {
      const id = stringValue(item, "id", "candidateId", "candidate_id");
      const confidence = numberValue(item, "confidence");
      if (!id || confidence === undefined) return [];
      const type = stringValue(item, "type");
      return [{
        id,
        type: (["text", "image", "shape", "pageNumber"].includes(type ?? "") ? type : "text") as AiMasterAnalysis["repeatedElements"][number]["type"],
        role: stringValue(item, "role") ?? "repeatedText",
        confidence,
        ...(stringValue(item, "text") ? { text: stringValue(item, "text") } : {}),
        ...(stringValue(item, "assetId", "asset_id") ? { assetId: stringValue(item, "assetId", "asset_id") } : {}),
        ...(isMasterDigest(item.digest) ? { digest: item.digest } : {}),
        ...(stringValue(item, "mediaType", "media_type") ? { mediaType: stringValue(item, "mediaType", "media_type") } : {}),
        ...(isRecord(item.crop) ? { crop: item.crop as unknown as NonNullable<AiMasterAnalysis["repeatedElements"][number]["crop"]> } : {}),
        ...(["contain", "cover"].includes(stringValue(item, "fit") ?? "") ? { fit: stringValue(item, "fit") as "contain" | "cover" } : {}),
        differences: Array.isArray(item.differences) ? item.differences.filter((value): value is string => typeof value === "string") : [],
        aiZoneCandidate: Boolean(item.aiZoneCandidate ?? item.ai_zone_candidate),
        evidence: (Array.isArray(item.evidence) ? item.evidence : []).map(normalizeAiEvidence).filter((entry): entry is AiMasterEvidence => entry !== null),
      }];
    }),
    layoutCandidates: recordArray(data.layoutCandidates ?? data.layout_candidates)
      .map(normalizeAiLayout)
      .filter((item): item is AiMasterLayoutCandidate => item !== null),
    decisions: (Array.isArray(data.decisions) ? data.decisions : []).map(normalizeAiDecision).filter((item): item is AiMasterCandidateDecision => item !== null),
    layoutActions: (Array.isArray(data.layoutActions ?? data.layout_actions) ? data.layoutActions ?? data.layout_actions : []).map(normalizeAiLayoutAction).filter((item): item is AiMasterLayoutAction => item !== null),
    warnings: Array.isArray(data.warnings) ? data.warnings.filter((item): item is string => typeof item === "string") : [],
    lowConfidence: Array.isArray(data.lowConfidence ?? data.low_confidence) ? (data.lowConfidence ?? data.low_confidence as unknown[]).filter((item): item is string => typeof item === "string") : [],
    tolerances: isRecord(data.tolerances) ? Object.fromEntries(Object.entries(data.tolerances).filter((entry): entry is [string, number] => typeof entry[1] === "number")) : {},
    contractDraft: contractDraft as unknown as AiMasterAnalysis["contractDraft"],
    ...(isRecord(validationValue)
      ? { validation: {
          valid: typeof validationValue.valid === "boolean" ? validationValue.valid : true,
          issues: recordArray(validationValue.issues).map((warning) => ({
            code: stringValue(warning, "code") ?? "reapply_warning",
            message: stringValue(warning, "message") ?? "reapply_warning",
            severity: (["info", "warning", "error"].includes(stringValue(warning, "severity") ?? "") ? stringValue(warning, "severity") : "warning") as "info" | "warning" | "error",
            ...(numberValue(warning, "slideNumber", "slide_number") ? { slideNumber: numberValue(warning, "slideNumber", "slide_number") } : {}),
            ...(stringValue(warning, "layoutId", "layout_id") ? { layoutId: stringValue(warning, "layoutId", "layout_id") } : {}),
            ...(stringValue(warning, "candidateId", "candidate_id") ? { elementId: stringValue(warning, "candidateId", "candidate_id") } : {}),
          })),
          checkedSlideCount: numberValue(validationValue, "checkedSlideCount", "checked_slide_count") ?? 0,
        } }
      : {}),
    ...(isRecord(masterRevisionValue) ? { masterRevision: normalizeMasterRevision(masterRevisionValue) } : {}),
  };
}

async function rasterizeMasterImage(file: File): Promise<File> {
  const isSvg = file.type === "image/svg+xml" || /\.svg$/i.test(file.name);
  let source: Blob = file;
  if (isSvg) {
    const svg = await file.text();
    if (/<(?:script|foreignObject)\b|(?:href|xlink:href)\s*=\s*["']\s*(?:javascript:|https?:|file:)/i.test(svg)) {
      throw new Error("Master SVG contains unsupported external or active content");
    }
    source = new Blob([svg], { type: "image/svg+xml" });
  }
  const objectUrl = URL.createObjectURL(source);
  try {
    const image = await new Promise<HTMLImageElement>((resolve, reject) => {
      const candidate = new Image();
      candidate.onload = () => resolve(candidate);
      candidate.onerror = () => reject(new Error("Master image could not be rendered"));
      candidate.src = objectUrl;
    });
    const naturalWidth = Math.max(1, image.naturalWidth || image.width);
    const naturalHeight = Math.max(1, image.naturalHeight || image.height);
    const scale = Math.min(4096 / Math.max(naturalWidth, naturalHeight), Math.max(1, 1024 / naturalWidth));
    const width = Math.max(1, Math.round(naturalWidth * scale));
    const height = Math.max(1, Math.round(naturalHeight * scale));
    const canvas = document.createElement("canvas");
    canvas.width = width;
    canvas.height = height;
    const context = canvas.getContext("2d");
    if (!context) throw new Error("Master image canvas is unavailable");
    context.drawImage(image, 0, 0, width, height);
    const png = await new Promise<Blob>((resolve, reject) =>
      canvas.toBlob(
        (value) => value ? resolve(value) : reject(new Error("Master image rasterization failed")),
        "image/png",
      ),
    );
    return new File(
      [png],
      `${file.name.replace(/\.(?:svg|jpe?g|webp)$/i, "") || "master-image"}.png`,
      { type: "image/png" },
    );
  } finally {
    URL.revokeObjectURL(objectUrl);
  }
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
    const upload = file.type === "image/png" && /\.png$/i.test(file.name)
      ? file
      : await rasterizeMasterImage(file);
    const form = new FormData();
    form.append("file", upload, upload.name);
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

  async getCompanyStandardDefault(
    signal?: AbortSignal,
  ): Promise<CompanyStandardDefault> {
    return normalizeCompanyStandardDefault(
      await this.request(AUTHORED_VNEXT_MASTER_ENDPOINTS.companyStandardDefault, {
        signal,
      }),
    );
  }

  async analyzePptx(
    file: File,
    purpose: AiMasterPurpose,
    signal?: AbortSignal,
  ): Promise<AiMasterAnalysis> {
    const form = new FormData();
    form.append("file", file, file.name);
    form.append("purpose", purpose);
    const response = await this.fetchImpl(
      getApiUrl(AUTHORED_VNEXT_MASTER_ENDPOINTS.aiImportAnalyses),
      {
        method: "POST",
        body: form,
        credentials: "include",
        signal,
        headers: { Accept: "application/json" },
      },
    );
    return normalizeAiMasterAnalysis(await parseJson(response));
  }

  async getAiAnalysis(analysisId: string, signal?: AbortSignal): Promise<AiMasterAnalysis> {
    return normalizeAiMasterAnalysis(await this.request(
      AUTHORED_VNEXT_MASTER_ENDPOINTS.aiImportAnalysis(analysisId),
      { signal },
    ));
  }

  async updateAiAnalysis(
    analysisId: string,
    request: UpdateAiMasterAnalysisRequest,
    signal?: AbortSignal,
  ): Promise<AiMasterAnalysis> {
    return normalizeAiMasterAnalysis(await this.request(
      AUTHORED_VNEXT_MASTER_ENDPOINTS.aiImportAnalysis(analysisId),
      { method: "PATCH", body: JSON.stringify(request), signal },
    ));
  }

  async validateAiAnalysis(analysisId: string, signal?: AbortSignal): Promise<AiMasterAnalysis> {
    return normalizeAiMasterAnalysis(await this.request(
      AUTHORED_VNEXT_MASTER_ENDPOINTS.aiImportValidate(analysisId),
      { method: "POST", signal },
    ));
  }

  async saveAiAnalysis(
    analysisId: string,
    request: SaveAiMasterAnalysisRequest = {},
    signal?: AbortSignal,
  ): Promise<AiMasterAnalysis> {
    return normalizeAiMasterAnalysis(await this.request(
      AUTHORED_VNEXT_MASTER_ENDPOINTS.aiImportSave(analysisId),
      { method: "POST", body: JSON.stringify(request), signal },
    ));
  }

  assetUrl(asset: BrandAsset): string {
    if (!asset.assetId.startsWith("asset:") || !isMasterDigest(asset.digest)) {
      throw new Error("Master asset reference is invalid");
    }
    return getApiUrl(
      AUTHORED_VNEXT_MASTER_ENDPOINTS.asset(asset.assetId, asset.digest),
    );
  }

  async getRevision(masterId: string, revision: number, signal?: AbortSignal): Promise<MasterRevisionEnvelope> {
    return normalizeMasterRevision(await this.request(
      AUTHORED_VNEXT_MASTER_ENDPOINTS.revision(masterId, revision),
      { signal },
    ));
  }

  async getHead(masterId: string, signal?: AbortSignal): Promise<MasterRevisionEnvelope | null> {
    const response = await this.fetchImpl(
      getApiUrl(AUTHORED_VNEXT_MASTER_ENDPOINTS.head(masterId)),
      { cache: "no-store", headers: { Accept: "application/json" }, signal },
    );
    if (response.status === 404) return null;
    return normalizeMasterRevision(await parseJson(response));
  }

  async listMasters(
    options: { cursor?: string; limit?: number } = {},
    signal?: AbortSignal,
  ): Promise<MasterCatalogPage> {
    const query = new URLSearchParams();
    if (options.cursor) query.set("cursor", options.cursor);
    if (options.limit !== undefined) query.set("limit", String(options.limit));
    const path = query.size
      ? `${AUTHORED_VNEXT_MASTER_ENDPOINTS.masters}?${query.toString()}`
      : AUTHORED_VNEXT_MASTER_ENDPOINTS.masters;
    return normalizeMasterCatalogPage(await this.request(path, { signal }));
  }

  async registerMaster(
    request: RegisterMasterRequest,
    signal?: AbortSignal,
  ): Promise<MasterRevisionEnvelope> {
    assertDeckMasterContract(request.contract);
    return normalizeMasterRevision(await this.request(AUTHORED_VNEXT_MASTER_ENDPOINTS.register, {
      method: "POST",
      body: JSON.stringify(request),
      signal,
    }));
  }

  async getPublished(masterId: string, signal?: AbortSignal): Promise<MasterRevisionEnvelope> {
    const published = normalizeMasterRevision(await this.request(
      AUTHORED_VNEXT_MASTER_ENDPOINTS.published(masterId),
      { signal },
    ));
    if (published.status !== "published") {
      throw new Error("Master API returned a non-published master");
    }
    return published;
  }

  async applyToPresentation(
    presentationId: string,
    request: ApplyMasterToPresentationRequest,
    signal?: AbortSignal,
  ): Promise<void> {
    await this.request(
      AUTHORED_VNEXT_MASTER_ENDPOINTS.applyToPresentation(presentationId),
      { method: "POST", body: JSON.stringify(request), signal },
    );
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
    assertRevisionCas(request);
    const payload = await this.request(AUTHORED_VNEXT_MASTER_ENDPOINTS.validate(masterId), {
      method: "POST",
      body: JSON.stringify(request),
      signal,
    });
    const data = responseData(payload);
    return normalizeMasterValidation(data.validation ?? data);
  }

  /**
   * Validate the exact server-sealed draft that will be published.  Consumers
   * must carry this receipt instead of deriving a success condition from the
   * editable browser contract.
   */
  async validateCanonicalDraft(
    masterId: string,
    request: RevisionCasRequest,
    signal?: AbortSignal,
  ): Promise<ValidatedMasterDraft> {
    const validation = await this.validateDraft(masterId, request, signal);
    return {
      masterId,
      expectedRevision: request.expectedRevision,
      expectedDigest: request.expectedDigest,
      validation,
    };
  }

  async publishDraft(masterId: string, request: RevisionCasRequest, signal?: AbortSignal): Promise<MasterRevisionEnvelope> {
    assertRevisionCas(request);
    const published = normalizeMasterRevision(await this.request(AUTHORED_VNEXT_MASTER_ENDPOINTS.publish(masterId), {
      method: "POST",
      body: JSON.stringify(request),
      signal,
    }));
    if (
      published.status !== "published"
      || !published.validation.valid
      || published.validation.errors.length > 0
    ) {
      throw new Error("서버 검증된 canonical 마스터가 아니어서 게시할 수 없습니다.");
    }
    return published;
  }

  /**
   * Fail closed unless validation and publication use the same canonical CAS
   * pair.  The API revalidates during publication as a second server-side
   * fence, so a stale or forged receipt cannot publish a different revision.
   */
  async publishValidatedDraft(
    ticket: ValidatedMasterDraft,
    signal?: AbortSignal,
  ): Promise<MasterRevisionEnvelope> {
    assertValidatedDraft(ticket);
    return this.publishDraft(ticket.masterId, {
      expectedRevision: ticket.expectedRevision,
      expectedDigest: ticket.expectedDigest,
    }, signal);
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
