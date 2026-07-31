import type { JsonRecord } from "./template-v2-studio.ts";

export const TEMPLATE_V2_IMAGE_MAX_BYTES = 10 * 1024 * 1024;
export const TEMPLATE_V2_IMAGE_MAX_DIMENSION = 8192;
export const TEMPLATE_V2_IMAGE_MAX_PIXELS = 40_000_000;

export const TEMPLATE_V2_LOCAL_ASSET_METADATA_KEY =
  "__template_v2_local_asset";

export type TemplateV2AllowedImageType =
  | "image/jpeg"
  | "image/png"
  | "image/webp";

export type TemplateV2ImageValidationCode =
  | "template_v2_local_image_filename_invalid"
  | "template_v2_local_image_type_not_allowed"
  | "template_v2_local_image_empty"
  | "template_v2_local_image_bytes_exceeded"
  | "template_v2_local_image_magic_mismatch"
  | "template_v2_local_image_decode_failed"
  | "template_v2_local_image_dimension_exceeded"
  | "template_v2_local_image_pixels_exceeded";

export interface TemplateV2ValidatedLocalImage {
  assetId: string;
  filename: string;
  mediaType: TemplateV2AllowedImageType;
  extension: "jpg" | "png" | "webp";
  sizeBytes: number;
  width: number;
  height: number;
  pixelCount: number;
  sha256: string;
  dataUrl: string;
}

export type TemplateV2ImageValidationResult =
  | {
      ok: true;
      code: "template_v2_local_image_valid";
      asset: TemplateV2ValidatedLocalImage;
    }
  | {
      ok: false;
      code: TemplateV2ImageValidationCode;
    };

export interface TemplateV2LocalAssetRecord {
  id: string;
  reference: string;
  provenance: {
    source: "local-upload";
    original_filename: string;
    media_type: TemplateV2AllowedImageType;
    size_bytes: number;
    width: number;
    height: number;
    sha256: string;
  };
}

export interface TemplateV2AssetRetentionIntent {
  previous_reference: string;
  replacement_reference: string;
  previous_asset_record: JsonRecord | null;
  defer_orphan_cleanup: true;
  delete_immediately: false;
}

export interface TemplateV2CropCandidate {
  candidateId: string;
  strategy: "center" | "adaptive_focus" | "rule_of_thirds";
  reasonCode:
    | "CENTER_SAFE_CROP"
    | "LANDSCAPE_SAFE_CROP"
    | "PORTRAIT_SAFE_CROP"
    | "SQUARE_SAFE_CROP"
    | "RULE_OF_THIRDS_CROP";
  focusX: number;
  focusY: number;
  cropScale: number;
  renderDigest: string;
}

export interface TemplateV2ImageReplacementPreview {
  previewId: string;
  sourceDigest: string;
  expectedRevision: number;
  idempotencyKey: string;
  beforeReference: string;
  afterReference: string;
  asset: TemplateV2ValidatedLocalImage;
  assetRecord: TemplateV2LocalAssetRecord;
  retention: TemplateV2AssetRetentionIntent;
  cropCandidates: readonly TemplateV2CropCandidate[];
}

export interface TemplateV2ImageReplacementPatch {
  data: string;
  focus_x: number;
  focus_y: number;
  crop_scale: number;
  [TEMPLATE_V2_LOCAL_ASSET_METADATA_KEY]: {
    contract: "presenton.template-v2-local-asset/v1";
    asset_record: TemplateV2LocalAssetRecord;
    retention: TemplateV2AssetRetentionIntent;
    replacement_preview_id: string;
    crop_candidate_id: string;
    crop_render_digest: string;
    expected_revision: number;
    idempotency_key: string;
  };
}

export type TemplateV2ImageReplacementApplyResult =
  | { ok: true; patch: TemplateV2ImageReplacementPatch }
  | {
      ok: false;
      code:
        | "template_v2_local_image_target_not_image"
        | "template_v2_local_image_stale_revision"
        | "template_v2_local_image_preview_stale"
        | "template_v2_local_image_crop_candidate_unknown";
    };

function isRecord(value: unknown): value is JsonRecord {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function safeFilename(filename: string): string | null {
  if (
    !filename.trim() ||
    filename.includes("://") ||
    /[\u0000-\u001f\u007f]/.test(filename)
  ) {
    return null;
  }
  const basename = filename.replaceAll("\\", "/").split("/").at(-1)?.trim();
  return basename && basename !== "." && basename !== ".." && basename.length <= 180
    ? basename
    : null;
}

function sniffMediaType(
  bytes: Uint8Array,
): TemplateV2AllowedImageType | null {
  if (
    bytes.length >= 8 &&
    bytes[0] === 0x89 &&
    bytes[1] === 0x50 &&
    bytes[2] === 0x4e &&
    bytes[3] === 0x47 &&
    bytes[4] === 0x0d &&
    bytes[5] === 0x0a &&
    bytes[6] === 0x1a &&
    bytes[7] === 0x0a
  ) {
    return "image/png";
  }
  if (
    bytes.length >= 3 &&
    bytes[0] === 0xff &&
    bytes[1] === 0xd8 &&
    bytes[2] === 0xff
  ) {
    return "image/jpeg";
  }
  if (
    bytes.length >= 12 &&
    textAt(bytes, 0, 4) === "RIFF" &&
    textAt(bytes, 8, 4) === "WEBP"
  ) {
    return "image/webp";
  }
  return null;
}

function textAt(bytes: Uint8Array, offset: number, length: number): string {
  return String.fromCharCode(...bytes.subarray(offset, offset + length));
}

function pngDimensions(bytes: Uint8Array): [number, number] | null {
  if (bytes.length < 24 || textAt(bytes, 12, 4) !== "IHDR") return null;
  const view = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
  return [view.getUint32(16), view.getUint32(20)];
}

function jpegDimensions(bytes: Uint8Array): [number, number] | null {
  let offset = 2;
  while (offset + 3 < bytes.length) {
    if (bytes[offset] !== 0xff) {
      offset += 1;
      continue;
    }
    while (offset < bytes.length && bytes[offset] === 0xff) offset += 1;
    const marker = bytes[offset];
    offset += 1;
    if (marker === 0xd8 || marker === 0xd9) continue;
    if (marker === 0xda) return null;
    if (offset + 2 > bytes.length) return null;
    const segmentLength = (bytes[offset] << 8) | bytes[offset + 1];
    if (segmentLength < 2 || offset + segmentLength > bytes.length) return null;
    if (
      [
        0xc0, 0xc1, 0xc2, 0xc3, 0xc5, 0xc6, 0xc7, 0xc9, 0xca, 0xcb,
        0xcd, 0xce, 0xcf,
      ].includes(marker)
    ) {
      if (segmentLength < 7) return null;
      const height = (bytes[offset + 3] << 8) | bytes[offset + 4];
      const width = (bytes[offset + 5] << 8) | bytes[offset + 6];
      return [width, height];
    }
    offset += segmentLength;
  }
  return null;
}

function uint24le(bytes: Uint8Array, offset: number): number {
  return bytes[offset] | (bytes[offset + 1] << 8) | (bytes[offset + 2] << 16);
}

function webpDimensions(bytes: Uint8Array): [number, number] | null {
  if (bytes.length < 30) return null;
  const chunk = textAt(bytes, 12, 4);
  if (chunk === "VP8X") {
    return [uint24le(bytes, 24) + 1, uint24le(bytes, 27) + 1];
  }
  if (
    chunk === "VP8 " &&
    bytes[23] === 0x9d &&
    bytes[24] === 0x01 &&
    bytes[25] === 0x2a
  ) {
    return [
      ((bytes[27] << 8) | bytes[26]) & 0x3fff,
      ((bytes[29] << 8) | bytes[28]) & 0x3fff,
    ];
  }
  if (chunk === "VP8L" && bytes.length >= 25 && bytes[20] === 0x2f) {
    return [
      1 + (((bytes[22] & 0x3f) << 8) | bytes[21]),
      1 + (((bytes[24] & 0x0f) << 10) | (bytes[23] << 2) | (bytes[22] >> 6)),
    ];
  }
  return null;
}

function dimensions(
  mediaType: TemplateV2AllowedImageType,
  bytes: Uint8Array,
): [number, number] | null {
  if (mediaType === "image/png") return pngDimensions(bytes);
  if (mediaType === "image/jpeg") return jpegDimensions(bytes);
  return webpDimensions(bytes);
}

function bytesToBase64(bytes: Uint8Array): string {
  let binary = "";
  const chunkSize = 0x8000;
  for (let index = 0; index < bytes.length; index += chunkSize) {
    binary += String.fromCharCode(...bytes.subarray(index, index + chunkSize));
  }
  return btoa(binary);
}

async function sha256(value: Uint8Array | string): Promise<string> {
  const bytes =
    typeof value === "string" ? new TextEncoder().encode(value) : value;
  const digestBytes = new Uint8Array(bytes);
  const digest = await crypto.subtle.digest("SHA-256", digestBytes.buffer);
  return Array.from(new Uint8Array(digest), (byte) =>
    byte.toString(16).padStart(2, "0"),
  ).join("");
}

function stableJson(value: unknown): string {
  if (Array.isArray(value)) return `[${value.map(stableJson).join(",")}]`;
  if (isRecord(value)) {
    return `{${Object.keys(value)
      .sort()
      .map((key) => `${JSON.stringify(key)}:${stableJson(value[key])}`)
      .join(",")}}`;
  }
  return JSON.stringify(value) ?? "null";
}

export type TemplateV2ImageDecoder = (input: {
  bytes: Uint8Array;
  mediaType: TemplateV2AllowedImageType;
}) => Promise<{ width: number; height: number }>;

export async function decodeTemplateV2ImageInBrowser(input: {
  bytes: Uint8Array;
  mediaType: TemplateV2AllowedImageType;
}): Promise<{ width: number; height: number }> {
  if (
    typeof window === "undefined" ||
    typeof Image === "undefined" ||
    typeof URL?.createObjectURL !== "function"
  ) {
    throw new Error("Browser image decoder is unavailable.");
  }

  const blobBytes = new Uint8Array(input.bytes);
  const objectUrl = URL.createObjectURL(
    new Blob([blobBytes.buffer], { type: input.mediaType }),
  );
  try {
    return await new Promise((resolve, reject) => {
      const image = new Image();
      image.onload = () => {
        if (image.naturalWidth < 1 || image.naturalHeight < 1) {
          reject(new Error("Decoded image has invalid dimensions."));
          return;
        }
        resolve({
          width: image.naturalWidth,
          height: image.naturalHeight,
        });
      };
      image.onerror = () => reject(new Error("Browser image decode failed."));
      image.src = objectUrl;
    });
  } finally {
    URL.revokeObjectURL(objectUrl);
  }
}

export async function validateTemplateV2LocalImage(input: {
  filename: string;
  declaredMediaType: string;
  bytes: Uint8Array;
  decode?: TemplateV2ImageDecoder;
}): Promise<TemplateV2ImageValidationResult> {
  const filename = safeFilename(input.filename);
  if (!filename) {
    return { ok: false, code: "template_v2_local_image_filename_invalid" };
  }
  if (
    !["image/jpeg", "image/png", "image/webp"].includes(
      input.declaredMediaType,
    )
  ) {
    return { ok: false, code: "template_v2_local_image_type_not_allowed" };
  }
  if (input.bytes.length === 0) {
    return { ok: false, code: "template_v2_local_image_empty" };
  }
  if (input.bytes.length > TEMPLATE_V2_IMAGE_MAX_BYTES) {
    return { ok: false, code: "template_v2_local_image_bytes_exceeded" };
  }
  const declaredMediaType =
    input.declaredMediaType as TemplateV2AllowedImageType;
  if (sniffMediaType(input.bytes) !== declaredMediaType) {
    return { ok: false, code: "template_v2_local_image_magic_mismatch" };
  }
  const size = dimensions(declaredMediaType, input.bytes);
  if (!size) {
    return { ok: false, code: "template_v2_local_image_decode_failed" };
  }
  const [width, height] = size;
  if (input.decode) {
    try {
      const decoded = await input.decode({
        bytes: input.bytes,
        mediaType: declaredMediaType,
      });
      if (decoded.width !== width || decoded.height !== height) {
        return { ok: false, code: "template_v2_local_image_decode_failed" };
      }
    } catch {
      return { ok: false, code: "template_v2_local_image_decode_failed" };
    }
  }
  if (
    width < 1 ||
    height < 1 ||
    width > TEMPLATE_V2_IMAGE_MAX_DIMENSION ||
    height > TEMPLATE_V2_IMAGE_MAX_DIMENSION
  ) {
    return { ok: false, code: "template_v2_local_image_dimension_exceeded" };
  }
  const pixelCount = width * height;
  if (pixelCount > TEMPLATE_V2_IMAGE_MAX_PIXELS) {
    return { ok: false, code: "template_v2_local_image_pixels_exceeded" };
  }
  const digest = await sha256(input.bytes);
  const extension =
    declaredMediaType === "image/jpeg"
      ? "jpg"
      : declaredMediaType === "image/png"
        ? "png"
        : "webp";
  return {
    ok: true,
    code: "template_v2_local_image_valid",
    asset: {
      assetId: `local-${digest.slice(0, 24)}`,
      filename,
      mediaType: declaredMediaType,
      extension,
      sizeBytes: input.bytes.length,
      width,
      height,
      pixelCount,
      sha256: digest,
      dataUrl: `data:${declaredMediaType};base64,${bytesToBase64(input.bytes)}`,
    },
  };
}

function existingAssetRecord(element: JsonRecord): JsonRecord | null {
  const metadata = element[TEMPLATE_V2_LOCAL_ASSET_METADATA_KEY];
  return isRecord(metadata) && isRecord(metadata.asset_record)
    ? { ...metadata.asset_record }
    : null;
}

async function cropCandidate(input: {
  assetSha256: string;
  strategy: TemplateV2CropCandidate["strategy"];
  reasonCode: TemplateV2CropCandidate["reasonCode"];
  focusX: number;
  focusY: number;
  cropScale: number;
}): Promise<TemplateV2CropCandidate> {
  const patch = {
    focus_x: input.focusX,
    focus_y: input.focusY,
    crop_scale: input.cropScale,
  };
  const renderDigest = await sha256(
    stableJson({ asset_sha256: input.assetSha256, patch }),
  );
  return {
    candidateId: await sha256(`${input.strategy}:${renderDigest}`),
    strategy: input.strategy,
    reasonCode: input.reasonCode,
    focusX: input.focusX,
    focusY: input.focusY,
    cropScale: input.cropScale,
    renderDigest,
  };
}

export async function createTemplateV2ImageReplacementPreview(input: {
  element: JsonRecord;
  asset: TemplateV2ValidatedLocalImage;
  revision: number;
}): Promise<
  | { ok: true; preview: TemplateV2ImageReplacementPreview }
  | {
      ok: false;
      code:
        | "template_v2_local_image_target_not_image"
        | "template_v2_local_image_revision_invalid"
        | "template_v2_local_image_crop_unsupported_icon";
    }
> {
  if (input.element.type !== "image" || typeof input.element.data !== "string") {
    return { ok: false, code: "template_v2_local_image_target_not_image" };
  }
  if (
    !Number.isInteger(input.revision) ||
    input.revision < 1
  ) {
    return { ok: false, code: "template_v2_local_image_revision_invalid" };
  }
  if (input.element.is_icon === true) {
    return {
      ok: false,
      code: "template_v2_local_image_crop_unsupported_icon",
    };
  }

  const ratio = input.asset.width / input.asset.height;
  const adaptive =
    ratio > 1.2
      ? ([50, 42, 1.15, "LANDSCAPE_SAFE_CROP"] as const)
      : ratio < 1 / 1.2
        ? ([50, 38, 1.15, "PORTRAIT_SAFE_CROP"] as const)
        : ([50, 45, 1.1, "SQUARE_SAFE_CROP"] as const);
  const thirds =
    ratio > 1.2
      ? ([33.333, 50, 1.25] as const)
      : ratio < 1 / 1.2
        ? ([50, 33.333, 1.25] as const)
        : ([33.333, 33.333, 1.2] as const);
  const cropCandidates = await Promise.all([
    cropCandidate({
      assetSha256: input.asset.sha256,
      strategy: "center",
      reasonCode: "CENTER_SAFE_CROP",
      focusX: 50,
      focusY: 50,
      cropScale: 1,
    }),
    cropCandidate({
      assetSha256: input.asset.sha256,
      strategy: "adaptive_focus",
      reasonCode: adaptive[3],
      focusX: adaptive[0],
      focusY: adaptive[1],
      cropScale: adaptive[2],
    }),
    cropCandidate({
      assetSha256: input.asset.sha256,
      strategy: "rule_of_thirds",
      reasonCode: "RULE_OF_THIRDS_CROP",
      focusX: thirds[0],
      focusY: thirds[1],
      cropScale: thirds[2],
    }),
  ]);
  const sourceDigest = await sha256(stableJson(input.element));
  const afterReference = input.asset.dataUrl;
  const assetRecord: TemplateV2LocalAssetRecord = {
    id: input.asset.assetId,
    reference: afterReference,
    provenance: {
      source: "local-upload",
      original_filename: input.asset.filename,
      media_type: input.asset.mediaType,
      size_bytes: input.asset.sizeBytes,
      width: input.asset.width,
      height: input.asset.height,
      sha256: input.asset.sha256,
    },
  };
  const retention: TemplateV2AssetRetentionIntent = {
    previous_reference: input.element.data,
    replacement_reference: afterReference,
    previous_asset_record: existingAssetRecord(input.element),
    defer_orphan_cleanup: true,
    delete_immediately: false,
  };
  const previewPayload = {
    source_digest: sourceDigest,
    expected_revision: input.revision,
    before_reference: input.element.data,
    after_reference: afterReference,
    asset_sha256: input.asset.sha256,
    candidate_ids: cropCandidates.map((candidate) => candidate.candidateId),
    previous_asset_record: retention.previous_asset_record,
  };
  const previewId = await sha256(stableJson(previewPayload));
  return {
    ok: true,
    preview: {
      previewId,
      sourceDigest,
      expectedRevision: input.revision,
      idempotencyKey: `template-v2-local-image-${previewId}`,
      beforeReference: input.element.data,
      afterReference,
      asset: input.asset,
      assetRecord,
      retention,
      cropCandidates,
    },
  };
}

export async function applyTemplateV2ImageReplacement(input: {
  element: JsonRecord;
  preview: TemplateV2ImageReplacementPreview;
  candidateId: string;
  currentRevision: number;
}): Promise<TemplateV2ImageReplacementApplyResult> {
  if (input.element.type !== "image") {
    return { ok: false, code: "template_v2_local_image_target_not_image" };
  }
  if (input.currentRevision !== input.preview.expectedRevision) {
    return { ok: false, code: "template_v2_local_image_stale_revision" };
  }
  if (
    typeof input.element.data !== "string" ||
    input.element.data !== input.preview.beforeReference ||
    (await sha256(stableJson(input.element))) !== input.preview.sourceDigest
  ) {
    return { ok: false, code: "template_v2_local_image_preview_stale" };
  }
  const candidate = input.preview.cropCandidates.find(
    (item) => item.candidateId === input.candidateId,
  );
  if (!candidate) {
    return {
      ok: false,
      code: "template_v2_local_image_crop_candidate_unknown",
    };
  }
  return {
    ok: true,
    patch: {
      data: input.preview.afterReference,
      focus_x: candidate.focusX,
      focus_y: candidate.focusY,
      crop_scale: candidate.cropScale,
      [TEMPLATE_V2_LOCAL_ASSET_METADATA_KEY]: {
        contract: "presenton.template-v2-local-asset/v1",
        asset_record: input.preview.assetRecord,
        retention: input.preview.retention,
        replacement_preview_id: input.preview.previewId,
        crop_candidate_id: candidate.candidateId,
        crop_render_digest: candidate.renderDigest,
        expected_revision: input.preview.expectedRevision,
        idempotency_key: input.preview.idempotencyKey,
      },
    },
  };
}

export function applyTemplateV2ImageReplacementPatch(
  element: JsonRecord,
  patch: TemplateV2ImageReplacementPatch,
): JsonRecord {
  if (element.type !== "image") return element;
  return { ...element, ...patch };
}
