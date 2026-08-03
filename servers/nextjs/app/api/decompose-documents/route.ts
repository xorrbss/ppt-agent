import { NextRequest, NextResponse } from "next/server";
import { Agent } from "undici";

import {
  getFastApiAuthHeaders,
  getFastApiBaseUrl,
} from "@/lib/fastapi-internal";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

// Keep this order when changing the document service deadline:
// FastAPI DECOMPOSE_TIMEOUT_SECONDS (600s) < proxy timeout (630s) < route
// maxDuration (660s). The dedicated dispatcher avoids Undici's 300s default
// headers timeout while FastAPI is converting/OCRing large documents.
const FASTAPI_DECOMPOSE_TIMEOUT_MS = 600_000;
const PROXY_DECOMPOSE_TIMEOUT_MS = FASTAPI_DECOMPOSE_TIMEOUT_MS + 30_000;
export const maxDuration = 660;

const documentServiceDispatcher = new Agent({
  headersTimeout: PROXY_DECOMPOSE_TIMEOUT_MS,
  bodyTimeout: PROXY_DECOMPOSE_TIMEOUT_MS,
});

type NodeFetchInit = RequestInit & { dispatcher: Agent };

function fetchWithDispatcher(
  input: RequestInfo | URL,
  init?: NodeFetchInit,
): Promise<Response> {
  const nodeFetch = fetch as unknown as (
    request: RequestInfo | URL,
    options?: NodeFetchInit,
  ) => Promise<Response>;
  return nodeFetch(input, init);
}

const TIMEOUT_ERROR_CODES = new Set([
  "UND_ERR_HEADERS_TIMEOUT",
  "UND_ERR_BODY_TIMEOUT",
  "UND_ERR_CONNECT_TIMEOUT",
]);

function isUpstreamTimeout(error: unknown, signal: AbortSignal): boolean {
  if (signal.aborted) return true;
  if (!(error instanceof Error)) return false;
  if (error.name === "AbortError") return true;

  const cause = error.cause;
  return (
    typeof cause === "object" &&
    cause !== null &&
    "code" in cause &&
    typeof cause.code === "string" &&
    TIMEOUT_ERROR_CODES.has(cause.code)
  );
}

function upstreamFailureResponse(status: 502 | 504) {
  const timedOut = status === 504;
  const message = timedOut
    ? "Document processing timed out"
    : "Document processing service is unavailable";

  return NextResponse.json(
    {
      detail: message,
      error: {
        code: timedOut
          ? "DOCUMENT_DECOMPOSE_TIMEOUT"
          : "DOCUMENT_SERVICE_UNAVAILABLE",
        message,
        status_code: status,
        retryable: false,
      },
    },
    { status },
  );
}

export async function POST(request: NextRequest) {
  const headers = new Headers(getFastApiAuthHeaders());
  const contentType = request.headers.get("content-type");
  const cookie = request.headers.get("cookie");
  const authorization = request.headers.get("authorization");

  headers.set("Accept", "application/json");
  if (contentType) headers.set("Content-Type", contentType);
  if (cookie) headers.set("Cookie", cookie);
  if (authorization) headers.set("Authorization", authorization);

  const controller = new AbortController();
  const deadline = setTimeout(
    () => controller.abort(),
    PROXY_DECOMPOSE_TIMEOUT_MS,
  );

  try {
    const response = await fetchWithDispatcher(
      `${getFastApiBaseUrl()}/api/v1/ppt/files/decompose`,
      {
        method: "POST",
        headers,
        body: await request.text(),
        cache: "no-store",
        signal: controller.signal,
        // This POST can create temporary conversion artifacts. Do not add a
        // retry policy here unless the backend provides an idempotency key.
        dispatcher: documentServiceDispatcher,
      },
    );

    const responseHeaders = new Headers({
      "Cache-Control": "no-store",
      "Content-Type":
        response.headers.get("content-type") ?? "application/json",
    });
    const requestId = response.headers.get("x-request-id");
    if (requestId) responseHeaders.set("X-Request-Id", requestId);

    return new NextResponse(response.body, {
      status: response.status,
      headers: responseHeaders,
    });
  } catch (error) {
    const status = isUpstreamTimeout(error, controller.signal) ? 504 : 502;
    console.error(
      `[decompose-documents] Document service request failed (${status})`,
      error,
    );
    return upstreamFailureResponse(status);
  } finally {
    clearTimeout(deadline);
  }
}
