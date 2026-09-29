import { NextRequest, NextResponse } from "next/server";
import { Agent } from "undici";

import { JSON_RESPONSE_HEADERS, proxyToFastApi } from "@/lib/fastapi-proxy";

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
  return proxyToFastApi(request, "/api/v1/ppt/files/decompose", {
    timeoutMs: PROXY_DECOMPOSE_TIMEOUT_MS,
    method: "POST",
    headers: { Accept: "application/json" },
    forwardRequestHeaders: ["content-type", "cookie", "authorization"],
    body: await request.text(),
    forwardSearch: false,
    // The deadline is the only abort source: a client disconnect must not
    // cancel a conversion that may already have created temporary artifacts.
    // Do not add a retry policy here unless the backend provides an
    // idempotency key.
    linkClientAbort: false,
    dispatcher: documentServiceDispatcher,
    forwardResponseHeaders: JSON_RESPONSE_HEADERS,
    defaultContentType: "application/json",
    logLabel: "[decompose-documents]",
    onFailure: (kind) => upstreamFailureResponse(kind === "timeout" ? 504 : 502),
  });
}
