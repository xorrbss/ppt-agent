import { createHash } from "node:crypto";
import { mkdtemp, readFile, rm } from "node:fs/promises";
import os from "node:os";
import path from "node:path";

import type {
  WorkerAsset,
  WorkerDigests,
  WorkerRequestEnvelope,
  WorkerResumeReceipt,
  WorkerResponseEnvelope,
} from "../../../../../../contracts/authored_vnext/export/v1/generated/export-contract.ts";

import {
  createOoxmlMasterCompilerPort,
  normalizeDeckMasterContract,
  type DeckMasterContract,
  type OoxmlMasterCompilerPort,
  type MaterializedTextValue,
  type SlideMaterializationRequest,
} from "../../../../lib/ooxml-vnext/index.ts";
import { OoxmlWorkerError } from "./worker-error.ts";

export { OoxmlWorkerError } from "./worker-error.ts";

export const WORKER_SCHEMA_VERSION = "authored-vnext-ooxml-worker/v1" as const;
export const WORKER_OPERATION = "EXPORT_PPTX" as const;

const SHA256 = /^[a-f0-9]{64}$/;
const MAX_OPERATION_ID_LENGTH = 128;
const MAX_SLIDES = 500;
const MAX_VALUES_PER_SLIDE = 256;
const SAFE_IDENTIFIER = /^[A-Za-z0-9._:-]+$/;

type JsonRecord = Record<string, unknown>;

export type OoxmlWorkerDigestSet = WorkerDigests;
export type OoxmlWorkerAssetDescriptor = WorkerAsset;

export interface OoxmlWorkerRequestV1 extends Omit<WorkerRequestEnvelope, "master" | "slides" | "assets"> {
  readonly master: DeckMasterContract;
  readonly slides: readonly SlideMaterializationRequest[];
  readonly assets: readonly OoxmlWorkerAssetDescriptor[];
}

export type OoxmlWorkerSuccessV1 = WorkerResponseEnvelope;

function isRecord(value: unknown): value is JsonRecord {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function assertKeys(value: JsonRecord, allowed: readonly string[], name: string): void {
  const allowedSet = new Set(allowed);
  if (Object.keys(value).some((key) => !allowedSet.has(key))) {
    throw new OoxmlWorkerError("INVALID_ENVELOPE", 400, `${name} contains unsupported fields`);
  }
}

function requireString(value: unknown, name: string, maximumLength = 4096): string {
  if (typeof value !== "string" || value.length === 0 || value.length > maximumLength) {
    throw new OoxmlWorkerError("INVALID_ENVELOPE", 400, `${name} is invalid`);
  }
  return value;
}

function requireSha256(value: unknown, name: string): string {
  const digest = requireString(value, name, 64);
  if (!SHA256.test(digest)) {
    throw new OoxmlWorkerError("INVALID_ENVELOPE", 400, `${name} must be lowercase sha256 hex`);
  }
  return digest;
}

function requireIdentifier(value: unknown, name: string, maximumLength: number): string {
  const identifier = requireString(value, name, maximumLength);
  if (!SAFE_IDENTIFIER.test(identifier)) {
    throw new OoxmlWorkerError("INVALID_ENVELOPE", 400, `${name} contains unsafe characters`);
  }
  return identifier;
}

function requirePositiveInteger(value: unknown, name: string, maximum = Number.MAX_SAFE_INTEGER): number {
  if (!Number.isSafeInteger(value) || (value as number) < 1 || (value as number) > maximum) {
    throw new OoxmlWorkerError("INVALID_ENVELOPE", 400, `${name} must be a positive integer`);
  }
  return value as number;
}

function canonicalJson(value: unknown): string {
  if (value === null || typeof value === "boolean") {
    return JSON.stringify(value);
  }
  if (typeof value === "string") return JSON.stringify(value.normalize("NFC"));
  if (typeof value === "number") {
    if (!Number.isFinite(value)) throw new OoxmlWorkerError("INVALID_ENVELOPE", 400, "Non-finite numbers are forbidden");
    return JSON.stringify(value);
  }
  if (Array.isArray(value)) return `[${value.map(canonicalJson).join(",")}]`;
  if (isRecord(value)) {
    const normalized = new Map<string, unknown>();
    for (const [key, item] of Object.entries(value)) normalized.set(key.normalize("NFC"), item);
    return `{${[...normalized.entries()]
      .sort(([left], [right]) => (left < right ? -1 : left > right ? 1 : 0))
      .map(([key, item]) => `${JSON.stringify(key)}:${canonicalJson(item)}`)
      .join(",")}}`;
  }
  throw new OoxmlWorkerError("INVALID_ENVELOPE", 400, "Unsupported JSON value");
}

export function sha256CanonicalJson(value: unknown): string {
  return createHash("sha256").update(canonicalJson(value), "utf8").digest("hex");
}

function sha256Bytes(value: Uint8Array): string {
  return createHash("sha256").update(value).digest("hex");
}

function assertDigest(actual: string, expected: string, kind: string): void {
  if (actual !== expected) {
    throw new OoxmlWorkerError(`${kind.toUpperCase()}_DIGEST_MISMATCH`, 409, `${kind} digest mismatch`);
  }
}

function parseSlide(value: unknown, index: number): SlideMaterializationRequest {
  if (!isRecord(value)) throw new OoxmlWorkerError("INVALID_ENVELOPE", 400, `slides[${index}] is invalid`);
  assertKeys(value, ["slideId", "layoutId", "values"], `slides[${index}]`);
  const values = value.values;
  if (!isRecord(values) || Object.keys(values).length > MAX_VALUES_PER_SLIDE) {
    throw new OoxmlWorkerError("INVALID_ENVELOPE", 400, `slides[${index}].values is invalid`);
  }
  const normalizedValues: Record<string, MaterializedTextValue> = {};
  for (const [key, item] of Object.entries(values)) {
    normalizedValues[requireString(key, `slides[${index}].values key`, 256)] = parseTextValue(
      item, `slides[${index}].values.${key}`,
    );
  }
  return {
    slideId: requireIdentifier(value.slideId, `slides[${index}].slideId`, 256),
    layoutId: requireIdentifier(value.layoutId, `slides[${index}].layoutId`, 256),
    values: normalizedValues,
  };
}

function requireCanonicalText(value: unknown, name: string): string {
  // Retained authored scenes carry digest-pinned native OOXML fragments and
  // element assets in the canonical body-grammar value. This is not free-form
  // user text, and the scene parser below revalidates every fragment/asset.
  const text = requireString(value, name, 25_000_000);
  if (text.normalize("NFC") !== text || /[\u0000-\u0008\u000B\u000C\u000E-\u001F]/.test(text)) {
    throw new OoxmlWorkerError("TEXT_NOT_CANONICAL", 422, `${name} must be NFC and XML-safe`);
  }
  for (let index = 0; index < text.length; index += 1) {
    const code = text.charCodeAt(index);
    if (code >= 0xd800 && code <= 0xdbff) {
      const next = text.charCodeAt(index + 1);
      if (!(next >= 0xdc00 && next <= 0xdfff)) {
        throw new OoxmlWorkerError("TEXT_NOT_CANONICAL", 422, `${name} has an unpaired surrogate`);
      }
      index += 1;
    } else if (code >= 0xdc00 && code <= 0xdfff) {
      throw new OoxmlWorkerError("TEXT_NOT_CANONICAL", 422, `${name} has an unpaired surrogate`);
    }
  }
  return text;
}

function parseTextValue(value: unknown, name: string): MaterializedTextValue {
  if (typeof value === "string") return requireCanonicalText(value, name);
  if (!isRecord(value)) {
    throw new OoxmlWorkerError("INVALID_ENVELOPE", 400, `${name} must be text or rich runs`);
  }
  assertKeys(value, ["runs"], name);
  if (!Array.isArray(value.runs) || value.runs.length === 0 || value.runs.length > 256) {
    throw new OoxmlWorkerError("INVALID_ENVELOPE", 400, `${name}.runs is invalid`);
  }
  return {
    runs: value.runs.map((run, index) => {
      if (!isRecord(run)) throw new OoxmlWorkerError("INVALID_ENVELOPE", 400, `${name}.runs[${index}] is invalid`);
      assertKeys(run, ["text", "bold", "italic", "fontFamily"], `${name}.runs[${index}]`);
      if (run.bold !== undefined && typeof run.bold !== "boolean") {
        throw new OoxmlWorkerError("INVALID_ENVELOPE", 400, `${name}.runs[${index}].bold is invalid`);
      }
      if (run.italic !== undefined && typeof run.italic !== "boolean") {
        throw new OoxmlWorkerError("INVALID_ENVELOPE", 400, `${name}.runs[${index}].italic is invalid`);
      }
      return {
        text: requireCanonicalText(run.text, `${name}.runs[${index}].text`),
        ...(run.bold === undefined ? {} : { bold: run.bold }),
        ...(run.italic === undefined ? {} : { italic: run.italic }),
        ...(run.fontFamily === undefined ? {} : {
          fontFamily: requireString(run.fontFamily, `${name}.runs[${index}].fontFamily`, 200),
        }),
      };
    }),
  };
}

function flattenedText(value: MaterializedTextValue): string {
  return typeof value === "string" ? value : value.runs.map(({ text }) => text).join("");
}

function assertTextQuality(
  slides: readonly SlideMaterializationRequest[],
  master: DeckMasterContract,
): void {
  const allowedFonts = new Set([
    master.fontManifest.primary,
    master.fontManifest.windowsFallback,
    ...(master.fontManifest.fallbacks ?? []),
    ...Object.values(master.fontManifest.themeScripts).filter((value): value is string => typeof value === "string"),
  ]);
  for (const slide of slides) {
    const layout = master.layouts.find(({ id }) => id === slide.layoutId);
    if (!layout) continue;
    for (const placeholder of layout.placeholders) {
      const role = placeholder.id.slice(placeholder.id.indexOf(":") + 1);
      const value = slide.values[role];
      if (value === undefined) continue;
      const lineCount = flattenedText(value).split("\n").length;
      if (lineCount > placeholder.textStyle.maxLines) {
        const code = placeholder.type === "title" && layout.kind === "standard"
          ? "TITLE_LAYOUT_PROMOTION_REQUIRED"
          : "TEXT_MAX_LINES_EXCEEDED";
        throw new OoxmlWorkerError(code, 422, `${slide.slideId}:${role} exceeds its layout line contract`);
      }
      if (typeof value !== "string") {
        for (const run of value.runs) {
          if (run.fontFamily && !allowedFonts.has(run.fontFamily)) {
            throw new OoxmlWorkerError("FONT_FALLBACK_NOT_PINNED", 422, `${slide.slideId}:${role} uses an unpinned font`);
          }
        }
      }
    }
  }
}

function parseAsset(value: unknown, index: number): OoxmlWorkerAssetDescriptor {
  if (!isRecord(value)) throw new OoxmlWorkerError("INVALID_ENVELOPE", 400, `assets[${index}] is invalid`);
  assertKeys(value, ["assetId", "mediaType", "sha256", "bytesBase64"], `assets[${index}]`);
  const asset: OoxmlWorkerAssetDescriptor = {
    assetId: requireIdentifier(value.assetId, `assets[${index}].assetId`, 256),
    mediaType: requireString(value.mediaType, `assets[${index}].mediaType`, 256),
    sha256: requireSha256(value.sha256, `assets[${index}].sha256`),
    bytesBase64: requireString(value.bytesBase64, `assets[${index}].bytesBase64`, 5_000_000),
  };
  const bytes = Buffer.from(asset.bytesBase64, "base64");
  if (asset.mediaType !== "image/png") {
    throw new OoxmlWorkerError("INVALID_ENVELOPE", 400, `assets[${index}].mediaType is unsupported`);
  }
  if (bytes.toString("base64") !== asset.bytesBase64 || bytes.byteLength === 0) {
    throw new OoxmlWorkerError("INVALID_ENVELOPE", 400, `assets[${index}].bytesBase64 is invalid`);
  }
  assertDigest(sha256Bytes(bytes), asset.sha256, "asset");
  return asset;
}

export function parseWorkerEnvelope(input: unknown): OoxmlWorkerRequestV1 {
  if (!isRecord(input)) throw new OoxmlWorkerError("INVALID_ENVELOPE", 400, "Request body must be an object");
  assertKeys(input, ["schemaVersion", "operation", "operationId", "fencingToken", "jobId", "attempt", "timeoutMs", "deadline", "resumeFromReceipt", "master", "slides", "assets", "digests"], "request");
  if (input.schemaVersion !== WORKER_SCHEMA_VERSION) {
    throw new OoxmlWorkerError("UNSUPPORTED_SCHEMA_VERSION", 400, "Unsupported worker schemaVersion");
  }
  if (input.operation !== WORKER_OPERATION) {
    throw new OoxmlWorkerError("UNSUPPORTED_OPERATION", 400, "Unsupported worker operation");
  }
  if (!Array.isArray(input.slides) || input.slides.length === 0 || input.slides.length > MAX_SLIDES) {
    throw new OoxmlWorkerError("INVALID_ENVELOPE", 400, "slides must be a non-empty bounded array");
  }
  if (!Array.isArray(input.assets) || input.assets.length > 128) {
    throw new OoxmlWorkerError("INVALID_ENVELOPE", 400, "assets must be a bounded array");
  }
  if (!isRecord(input.digests)) {
    throw new OoxmlWorkerError("INVALID_ENVELOPE", 400, "digests must be an object");
  }
  if (input.deadline !== undefined && (
    typeof input.deadline !== "string" ||
    !Number.isFinite(Date.parse(input.deadline))
  )) {
    throw new OoxmlWorkerError("INVALID_ENVELOPE", 400, "deadline must be an ISO date-time string");
  }
  if (input.resumeFromReceipt !== undefined && input.resumeFromReceipt !== null && !isRecord(input.resumeFromReceipt)) {
    throw new OoxmlWorkerError("INVALID_ENVELOPE", 400, "resumeFromReceipt must be an object or null");
  }
  if (typeof input.deadline === "string" && Date.parse(input.deadline) <= Date.now()) {
    throw new OoxmlWorkerError("WORKER_DEADLINE_EXCEEDED", 408, "Worker deadline has already elapsed");
  }
  assertKeys(input.digests, ["masterSha256", "specSha256", "assetManifestSha256", "fontManifestSha256", "packageSha256"], "digests");
  const packageSha256 = input.digests.packageSha256;
  if (packageSha256 !== null && (typeof packageSha256 !== "string" || !SHA256.test(packageSha256))) {
    throw new OoxmlWorkerError("INVALID_ENVELOPE", 400, "digests.packageSha256 is invalid");
  }
  if (isRecord(input.resumeFromReceipt)) {
    throw new OoxmlWorkerError("RESUME_UNSUPPORTED", 422, "This worker version cannot resume from a prior receipt");
  }
  const slides = input.slides.map(parseSlide);
  const assets = input.assets.map(parseAsset);
  if (new Set(slides.map(({ slideId }) => slideId)).size !== slides.length) {
    throw new OoxmlWorkerError("INVALID_ENVELOPE", 400, "slideId values must be unique");
  }
  if (new Set(assets.map(({ assetId }) => assetId)).size !== assets.length) {
    throw new OoxmlWorkerError("INVALID_ENVELOPE", 400, "assetId values must be unique");
  }
  const masterSha256 = requireSha256(input.digests.masterSha256, "digests.masterSha256");
  const specSha256 = requireSha256(input.digests.specSha256, "digests.specSha256");
  const assetManifestSha256 = requireSha256(input.digests.assetManifestSha256, "digests.assetManifestSha256");
  const fontManifestSha256 = requireSha256(input.digests.fontManifestSha256, "digests.fontManifestSha256");
  assertDigest(sha256CanonicalJson(input.master), masterSha256, "master");
  // The digest fences the exact wire payload. Parsing below may rebuild
  // otherwise equivalent objects, so never bind the receipt to that copy.
  assertDigest(sha256CanonicalJson(input.slides), specSha256, "spec");
  assertDigest(
    sha256CanonicalJson(assets.map(({ assetId, mediaType, sha256 }) => ({ assetId, mediaType, sha256 }))),
    assetManifestSha256,
    "asset_manifest",
  );
  if (!isRecord(input.master) || !isRecord(input.master.fontManifest)) {
    throw new OoxmlWorkerError("INVALID_ENVELOPE", 400, "master.fontManifest is invalid");
  }
  assertDigest(sha256CanonicalJson(input.master.fontManifest), fontManifestSha256, "font_manifest");
  let normalizedMaster: DeckMasterContract;
  try {
    normalizedMaster = normalizeDeckMasterContract(input.master);
  } catch {
    throw new OoxmlWorkerError("INVALID_ENVELOPE", 400, "master contract is invalid");
  }
  assertTextQuality(slides, normalizedMaster);
  const request: OoxmlWorkerRequestV1 = {
    schemaVersion: WORKER_SCHEMA_VERSION,
    operation: WORKER_OPERATION,
    operationId: requireIdentifier(input.operationId, "operationId", MAX_OPERATION_ID_LENGTH),
    fencingToken: requirePositiveInteger(input.fencingToken, "fencingToken"),
    ...(input.jobId === undefined ? {} : { jobId: requireIdentifier(input.jobId, "jobId", MAX_OPERATION_ID_LENGTH) }),
    ...(input.attempt === undefined ? {} : { attempt: requirePositiveInteger(input.attempt, "attempt", 10_000) }),
    ...(input.timeoutMs === undefined ? {} : { timeoutMs: requirePositiveInteger(input.timeoutMs, "timeoutMs", 5 * 60_000) }),
    ...(input.deadline === undefined ? {} : { deadline: input.deadline as string }),
    ...(input.resumeFromReceipt === undefined ? {} : { resumeFromReceipt: input.resumeFromReceipt as WorkerResumeReceipt | null }),
    master: normalizedMaster,
    slides,
    assets,
    digests: {
      masterSha256,
      specSha256,
      assetManifestSha256,
      fontManifestSha256,
      packageSha256,
    },
  };
  return Object.freeze(request);
}

function throwIfAborted(signal: AbortSignal): void {
  if (signal.aborted) throw new OoxmlWorkerError("WORKER_CANCELLED", 499, "Worker operation was cancelled");
}

function currentPrecondition(port: OoxmlMasterCompilerPort, document: ReturnType<OoxmlMasterCompilerPort["createGenerated"]>["document"]) {
  return port.readBack(document).precondition;
}

function adapterRejection(error: unknown): OoxmlWorkerError {
  const message = error instanceof Error ? error.message : "";
  const code = message.startsWith("Unknown layout:")
    ? "ADAPTER_UNKNOWN_LAYOUT"
    : message.startsWith("Missing slide value for placeholder role:")
      ? "ADAPTER_MISSING_SLIDE_VALUE"
      : message.startsWith("Duplicate or empty placeholder role:")
        ? "ADAPTER_DUPLICATE_PLACEHOLDER_ROLE"
        : message.startsWith("Duplicate or invalid placeholder idx:")
          ? "ADAPTER_DUPLICATE_PLACEHOLDER_INDEX"
          : message === "OOXML operation revision precondition failed"
            ? "ADAPTER_REVISION_CONFLICT"
            : "ADAPTER_REJECTED";
  if (code === "ADAPTER_REJECTED" && /^[A-Za-z0-9 :._/\[\]-]{1,180}$/.test(message)) {
    console.warn(`authored_ooxml_adapter_rejected reason=${message}`);
  }
  return new OoxmlWorkerError(code, 422, "OOXML adapter rejected the export contract");
}

export interface ExecuteWorkerOptions {
  readonly signal: AbortSignal;
  readonly portFactory?: () => OoxmlMasterCompilerPort;
  readonly temporaryRoot?: string;
}

export async function executeWorkerExport(
  input: unknown,
  options: ExecuteWorkerOptions,
): Promise<OoxmlWorkerSuccessV1> {
  throwIfAborted(options.signal);
  const request = parseWorkerEnvelope(input);
  throwIfAborted(options.signal);
  const port = (options.portFactory ?? createOoxmlMasterCompilerPort)();
  let created;
  try {
    created = port.createGenerated(
      request.master,
      request.assets.map((asset) => ({ ...asset, mediaType: "image/png" as const })),
    );
    for (const slide of request.slides) {
      throwIfAborted(options.signal);
      port.materializeSlide(created.document, slide, currentPrecondition(port, created.document));
    }
  } catch (error) {
    if (error instanceof OoxmlWorkerError) throw error;
    throw adapterRejection(error);
  }

  throwIfAborted(options.signal);
  const temporaryDirectory = await mkdtemp(path.join(options.temporaryRoot ?? os.tmpdir(), "presenton-ooxml-worker-"));
  const candidatePath = path.join(temporaryDirectory, "candidate.pptx");
  try {
    let saved;
    try {
      saved = await port.saveBuffered(
        created.document,
        candidatePath,
        currentPrecondition(port, created.document),
        null,
      );
    } catch {
      throwIfAborted(options.signal);
      throw new OoxmlWorkerError("ADAPTER_PUBLICATION_REJECTED", 422, "OOXML adapter rejected candidate publication");
    }
    throwIfAborted(options.signal);
    const bytes = await readFile(candidatePath);
    const artifactSha256 = sha256Bytes(bytes);
    if (saved.publication.sha256 !== artifactSha256 || saved.publication.publishedReadBackSha256 !== artifactSha256) {
      throw new OoxmlWorkerError("PACKAGE_DIGEST_MISMATCH", 409, "package digest mismatch");
    }
    if (request.digests.packageSha256 !== null) {
      assertDigest(artifactSha256, request.digests.packageSha256, "package");
    }
    const masterReadBack = saved.readBack.master;
    const expectedFurniture = request.master.furniture;
    const actualFurniture = masterReadBack?.semanticMetadata.furniture ?? [];
    const expectedFontFiles = request.master.fontManifest.files ?? [];
    if (
      saved.readBack.preservation.silentLossCount !== 0 ||
      saved.readBack.package.macroDetected ||
      saved.readBack.package.quarantineReasons.length > 0 ||
      saved.readBack.package.slideCount !== request.slides.length ||
      saved.readBack.slides.length !== request.slides.length ||
      !masterReadBack ||
      actualFurniture.length !== expectedFurniture.length ||
      masterReadBack.semanticMetadata.fontFiles.length !== expectedFontFiles.length ||
      saved.readBack.slides.some((slide, index) =>
        slide.slideId !== request.slides[index].slideId ||
        slide.layoutId !== request.slides[index].layoutId ||
        slide.masterDigest !== masterReadBack?.semanticMetadata.contractDigest
      )
    ) {
      throw new OoxmlWorkerError("READ_BACK_REJECTED", 422, "Candidate read-back was rejected");
    }
    return {
      schemaVersion: WORKER_SCHEMA_VERSION,
      operation: WORKER_OPERATION,
      operationId: request.operationId,
      fencingToken: request.fencingToken,
      status: "SUCCEEDED",
      artifact: {
        mediaType: "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        filenameExtension: ".pptx",
        byteLength: bytes.byteLength,
        sha256: artifactSha256,
        contentBase64: bytes.toString("base64"),
      },
      receipt: {
        adapterRevision: saved.revision,
        validation: "candidate-reopened",
        candidateDigestVerified: true,
        package: {
          packageDigest: saved.readBack.package.packageDigest,
          relationshipDigest: saved.readBack.package.relationshipDigest,
          closureDigest: saved.readBack.package.closureDigest,
          slideCount: saved.readBack.package.slideCount,
          masterCount: saved.readBack.package.masterCount,
          layoutCount: saved.readBack.package.layoutCount,
          macroDetected: saved.readBack.package.macroDetected,
          quarantineReasons: saved.readBack.package.quarantineReasons,
        },
        preservation: {
          silentLossCount: saved.readBack.preservation.silentLossCount,
          missingParts: saved.readBack.preservation.missingParts,
          unexpectedChangedParts: saved.readBack.preservation.unexpectedChangedParts,
        },
        master: {
          contractDigest: masterReadBack.semanticMetadata.contractDigest,
          semanticDigest: masterReadBack.semanticMetadata.semanticDigest,
          furnitureCount: actualFurniture.length,
          logoCount: actualFurniture.filter(({ kind }) => kind === "logo").length,
          pageNumberCount: actualFurniture.filter(({ kind }) => kind === "pageNumber").length,
          fontFileCount: masterReadBack.semanticMetadata.fontFiles.length,
          duplicateFurnitureCount: 0,
          fontFilesMode: "REFERENCE_METADATA_VERIFIED",
        },
        slides: saved.readBack.slides,
      },
    };
  } finally {
    await rm(temporaryDirectory, { recursive: true, force: true });
  }
}
