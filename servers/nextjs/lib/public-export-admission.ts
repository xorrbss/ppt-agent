import { createHash } from "node:crypto";

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;
const SENSITIVE_KEYS = new Set([
  "authorization",
  "cookie",
  "cookieheader",
  "ownerid",
  "tenantid",
  "outputpath",
  "path",
  "session",
  "sessioncookie",
  "sessiontoken",
]);

export class PublicExportAdmissionError extends Error {
  readonly status: number;

  constructor(message: string, status: number) {
    super(message);
    this.status = status;
    this.name = "PublicExportAdmissionError";
  }
}

export interface PublicExportAdmission {
  presentationId: string;
  commandType: "export.pptx" | "export.pdf";
  idempotencyKey: string;
  expectedRevision: number;
  context: {
    pptx_mode: "fidelity" | "hybrid";
    font_embedding: boolean;
  };
}

export interface PublicExportReceipt {
  operationId: string;
  receiptId: string;
  status: string;
  duplicate: boolean;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function rejectSensitiveKeys(value: Record<string, unknown>): void {
  for (const key of Object.keys(value)) {
    if (SENSITIVE_KEYS.has(key.replaceAll("_", "").toLowerCase())) {
      throw new PublicExportAdmissionError(
        "Export identity and paths are server-controlled",
        400,
      );
    }
  }
}

function canonicalRequestIdentity(value: {
  presentationId: string;
  format: string;
  expectedRevision: number;
  pptxMode: string;
  fontEmbedding: boolean;
}): string {
  return JSON.stringify([
    value.presentationId,
    value.expectedRevision,
    value.format,
    value.pptxMode,
    value.fontEmbedding,
  ]);
}

export function parsePublicExportAdmission(
  value: unknown,
  idempotencyHeader?: string | null,
): PublicExportAdmission {
  if (!isRecord(value)) {
    throw new PublicExportAdmissionError("Invalid JSON body", 400);
  }
  rejectSensitiveKeys(value);

  const presentationId = value.id;
  const format = value.format;
  const expectedRevision = value.expectedPresentationRevision;
  if (typeof presentationId !== "string" || !UUID.test(presentationId)) {
    throw new PublicExportAdmissionError("Invalid presentation ID", 400);
  }
  if (format !== "pptx" && format !== "pdf") {
    throw new PublicExportAdmissionError("Unsupported export format", 400);
  }
  if (
    typeof expectedRevision !== "number" ||
    !Number.isSafeInteger(expectedRevision) ||
    expectedRevision < 0
  ) {
    throw new PublicExportAdmissionError(
      "expectedPresentationRevision is required",
      400,
    );
  }

  const pptxMode = value.pptxMode ?? "fidelity";
  if (pptxMode !== "fidelity" && pptxMode !== "hybrid") {
    throw new PublicExportAdmissionError("Invalid PPTX export mode", 400);
  }
  const fontEmbedding = value.fontEmbedding ?? false;
  if (typeof fontEmbedding !== "boolean") {
    throw new PublicExportAdmissionError("Invalid font embedding option", 400);
  }
  if (fontEmbedding && (format !== "pptx" || pptxMode !== "hybrid")) {
    throw new PublicExportAdmissionError(
      "Font embedding requires Hybrid PPTX export",
      400,
    );
  }

  const suppliedIdempotencyKey = idempotencyHeader?.trim();
  if (suppliedIdempotencyKey && suppliedIdempotencyKey.length > 255) {
    throw new PublicExportAdmissionError("Idempotency key is too long", 400);
  }
  const digest = createHash("sha256")
    .update(
      canonicalRequestIdentity({
        presentationId,
        format,
        expectedRevision,
        pptxMode,
        fontEmbedding,
      }),
    )
    .digest("hex");

  return {
    presentationId,
    commandType: `export.${format}`,
    idempotencyKey: suppliedIdempotencyKey || `public-export-v1:${digest}`,
    expectedRevision,
    context: {
      pptx_mode: pptxMode,
      font_embedding: fontEmbedding,
    },
  };
}

export function parsePublicExportReceipt(value: unknown): PublicExportReceipt {
  if (!isRecord(value)) {
    throw new PublicExportAdmissionError("Invalid export admission response", 502);
  }
  const operationId = value.operation_id;
  const receiptId = value.receipt_id;
  const status = value.status;
  if (
    typeof operationId !== "string" ||
    !UUID.test(operationId) ||
    typeof receiptId !== "string" ||
    !UUID.test(receiptId) ||
    typeof status !== "string" ||
    !status.trim()
  ) {
    throw new PublicExportAdmissionError("Invalid export admission response", 502);
  }
  return {
    operationId,
    receiptId,
    status: status.trim(),
    duplicate: value.duplicate === true,
  };
}
