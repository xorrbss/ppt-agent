import { timingSafeEqual } from "node:crypto";

import { NextResponse, type NextRequest } from "next/server";

import {
  executeWorkerExport,
  OoxmlWorkerError,
  WORKER_SCHEMA_VERSION,
} from "./worker.ts";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

// Retained authored scenes carry digest-pinned native fragments and their
// element assets in the same fail-closed export envelope. The previous 4 MiB
// default rejected valid OSB native-scene requests before the worker could
// verify those digests.
const DEFAULT_MAX_BODY_BYTES = 16 * 1024 * 1024;
const DEFAULT_TIMEOUT_MS = 30_000;

function boundedInteger(value: string | undefined, fallback: number, minimum: number, maximum: number): number {
  if (!value) return fallback;
  const parsed = Number(value);
  return Number.isSafeInteger(parsed) && parsed >= minimum && parsed <= maximum ? parsed : fallback;
}

function bearerToken(header: string | null): string | null {
  if (!header?.startsWith("Bearer ")) return null;
  const token = header.slice("Bearer ".length);
  return token.length > 0 ? token : null;
}

function tokensEqual(received: string, expected: string): boolean {
  const left = Buffer.from(received, "utf8");
  const right = Buffer.from(expected, "utf8");
  return left.byteLength === right.byteLength && timingSafeEqual(left, right);
}

function errorResponse(code: string, status: number, message: string): NextResponse {
  return NextResponse.json(
    {
      schemaVersion: WORKER_SCHEMA_VERSION,
      status: "FAILED",
      error: {
        code,
        message,
        retryable: status >= 500 || status === 408 || status === 429,
        failedClosed: true,
      },
    },
    { status, headers: { "cache-control": "no-store" } },
  );
}

export async function POST(request: NextRequest): Promise<NextResponse> {
  const expectedSecret =
    process.env.AUTHORED_VNEXT_OOXML_WORKER_TOKEN ??
    process.env.OOXML_VNEXT_INTERNAL_SECRET;
  if (!expectedSecret || expectedSecret.length < 32) {
    return errorResponse("WORKER_NOT_CONFIGURED", 503, "Internal OOXML worker is not configured");
  }
  const receivedSecret = bearerToken(request.headers.get("authorization"));
  if (!receivedSecret || !tokensEqual(receivedSecret, expectedSecret)) {
    return errorResponse("UNAUTHORIZED", 401, "Unauthorized");
  }

  const maxBodyBytes = boundedInteger(
    process.env.OOXML_VNEXT_MAX_BODY_BYTES,
    DEFAULT_MAX_BODY_BYTES,
    1_024,
    32 * 1024 * 1024,
  );
  const contentLength = Number(request.headers.get("content-length"));
  if (Number.isFinite(contentLength) && contentLength > maxBodyBytes) {
    return errorResponse("REQUEST_TOO_LARGE", 413, "Request body is too large");
  }

  let body: Uint8Array;
  try {
    body = new Uint8Array(await request.arrayBuffer());
  } catch (error) {
    console.error("[ooxml-vnext] request body read failed", {
      contentLength: Number.isFinite(contentLength) ? contentLength : null,
      error: error instanceof Error ? error.name : "unknown",
    });
    return errorResponse("BODY_READ_FAILED", 400, "Request body could not be read");
  }
  if (body.byteLength === 0 || body.byteLength > maxBodyBytes) {
    return errorResponse("REQUEST_TOO_LARGE", 413, "Request body is empty or too large");
  }

  let input: unknown;
  try {
    input = JSON.parse(Buffer.from(body).toString("utf8"));
  } catch (error) {
    console.error("[ooxml-vnext] request JSON parse failed", {
      contentLength: Number.isFinite(contentLength) ? contentLength : null,
      bodyBytes: body.byteLength,
      error: error instanceof Error ? error.name : "unknown",
    });
    return errorResponse("INVALID_JSON", 400, "Request body must be valid JSON");
  }

  const configuredTimeoutMs = boundedInteger(
    process.env.OOXML_VNEXT_WORKER_TIMEOUT_MS,
    DEFAULT_TIMEOUT_MS,
    100,
    5 * 60_000,
  );
  const requestedTimeoutMs =
    typeof input === "object" && input !== null &&
    Number.isSafeInteger((input as Record<string, unknown>).timeoutMs) &&
    ((input as Record<string, unknown>).timeoutMs as number) >= 100
      ? ((input as Record<string, unknown>).timeoutMs as number)
      : configuredTimeoutMs;
  const deadlineMs =
    typeof input === "object" && input !== null &&
    typeof (input as Record<string, unknown>).deadline === "string"
      ? Date.parse((input as Record<string, unknown>).deadline as string) - Date.now()
      : configuredTimeoutMs;
  const timeoutMs = Math.max(1, Math.min(configuredTimeoutMs, requestedTimeoutMs, deadlineMs));
  const controller = new AbortController();
  const abortFromRequest = () => controller.abort(request.signal.reason);
  if (request.signal.aborted) abortFromRequest();
  else request.signal.addEventListener("abort", abortFromRequest, { once: true });
  const timeout = setTimeout(() => controller.abort(new Error("deadline exceeded")), timeoutMs);
  try {
    const result = await executeWorkerExport(input, { signal: controller.signal });
    return NextResponse.json(result, {
      status: 200,
      headers: { "cache-control": "no-store", "content-type": "application/json" },
    });
  } catch (error) {
    if (controller.signal.aborted) {
      return errorResponse(request.signal.aborted ? "WORKER_CANCELLED" : "WORKER_TIMEOUT", request.signal.aborted ? 499 : 504, request.signal.aborted ? "Worker operation was cancelled" : "Worker operation timed out");
    }
    if (error instanceof OoxmlWorkerError) {
      console.error("[ooxml-vnext] worker rejected request", {
        code: error.code,
        status: error.status,
        message: error.message,
      });
      return errorResponse(error.code, error.status, error.message);
    }
    return errorResponse("INTERNAL_WORKER_ERROR", 500, "Internal OOXML worker failed");
  } finally {
    clearTimeout(timeout);
    request.signal.removeEventListener("abort", abortFromRequest);
  }
}
