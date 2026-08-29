import { NextRequest, NextResponse } from "next/server";

import {
  getFastApiAuthHeaders,
  getFastApiBaseUrl,
} from "@/lib/fastapi-internal";
import {
  parsePublicExportAdmission,
  parsePublicExportReceipt,
  PublicExportAdmissionError,
} from "@/lib/public-export-admission";

const MAX_REQUEST_BYTES = 64 * 1024;

function safeBackendStatus(status: number): number {
  if ([400, 401, 403, 404, 409, 429].includes(status)) return status;
  return status >= 500 ? 502 : 400;
}

export async function POST(request: NextRequest) {
  try {
    const contentLength = Number(request.headers.get("content-length") ?? "0");
    if (Number.isFinite(contentLength) && contentLength > MAX_REQUEST_BYTES) {
      throw new PublicExportAdmissionError("Export request is too large", 413);
    }

    let raw: unknown;
    try {
      raw = await request.json();
    } catch {
      throw new PublicExportAdmissionError("Invalid JSON body", 400);
    }
    const admission = parsePublicExportAdmission(
      raw,
      request.headers.get("idempotency-key"),
    );
    const cookie = request.headers.get("cookie")?.trim();
    const response = await fetch(
      `${getFastApiBaseUrl()}/api/v1/ppt/workspace/presentations/${encodeURIComponent(admission.presentationId)}/commands`,
      {
        method: "POST",
        headers: {
          Accept: "application/json",
          "Content-Type": "application/json",
          ...getFastApiAuthHeaders(),
          // The browser session terminates at PPT Core. It is never copied into
          // the durable operation payload or forwarded to a worker.
          ...(cookie ? { Cookie: cookie } : {}),
        },
        body: JSON.stringify({
          command:
            admission.commandType === "export.pptx"
              ? "Export presentation as PowerPoint"
              : "Export presentation as PDF",
          command_type: admission.commandType,
          idempotency_key: admission.idempotencyKey,
          expected_revision: admission.expectedRevision,
          context: admission.context,
          asset_ids: [],
        }),
        cache: "no-store",
      },
    );
    if (!response.ok) {
      throw new PublicExportAdmissionError(
        "Export operation was rejected",
        safeBackendStatus(response.status),
      );
    }
    const receipt = parsePublicExportReceipt(await response.json());
    return NextResponse.json(
      {
        success: true,
        status: receipt.status,
        operationId: receipt.operationId,
        receiptId: receipt.receiptId,
        duplicate: receipt.duplicate,
      },
      { status: 202 },
    );
  } catch (error) {
    if (error instanceof PublicExportAdmissionError) {
      return NextResponse.json(
        { success: false, error: error.message },
        { status: error.status },
      );
    }
    console.error("[export-presentation:admission]", error);
    return NextResponse.json(
      { success: false, error: "Export operation service is unavailable" },
      { status: 502 },
    );
  }
}
