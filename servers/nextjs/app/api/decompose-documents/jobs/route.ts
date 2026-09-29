import { NextRequest, NextResponse } from "next/server";

import { JSON_RESPONSE_HEADERS, proxyToFastApi } from "@/lib/fastapi-proxy";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

// Queuing a job is a short admission call; the processing itself is polled.
const START_JOB_TIMEOUT_MS = 30_000;

function unavailableResponse() {
  return NextResponse.json(
    {
      detail: "Document processing service is unavailable",
      error: {
        code: "DOCUMENT_SERVICE_UNAVAILABLE",
        message: "Document processing service is unavailable",
        status_code: 502,
        retryable: true,
      },
    },
    { status: 502 },
  );
}

/** Starts a short-lived request that queues document processing in FastAPI. */
export async function POST(request: NextRequest) {
  return proxyToFastApi(request, "/api/v1/ppt/files/decompose/jobs", {
    timeoutMs: START_JOB_TIMEOUT_MS,
    method: "POST",
    headers: { Accept: "application/json" },
    forwardRequestHeaders: [
      "content-type",
      "cookie",
      "authorization",
      "idempotency-key",
    ],
    body: await request.text(),
    forwardSearch: false,
    forwardResponseHeaders: JSON_RESPONSE_HEADERS,
    defaultContentType: "application/json",
    logLabel: "[decompose-documents/jobs]",
    // Timeouts and transport failures share one envelope on this route.
    onFailure: unavailableResponse,
  });
}
