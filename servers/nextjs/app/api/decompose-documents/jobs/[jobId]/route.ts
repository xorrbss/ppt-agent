import { NextRequest, NextResponse } from "next/server";

import { JSON_RESPONSE_HEADERS, proxyToFastApi } from "@/lib/fastapi-proxy";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

// Poll and cancel are point lookups on a job row.
const JOB_REQUEST_TIMEOUT_MS = 15_000;

type RouteContext = { params: Promise<{ jobId: string }> };

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

async function proxyJobRequest(request: NextRequest, context: RouteContext) {
  const { jobId } = await context.params;
  return proxyToFastApi(
    request,
    `/api/v1/ppt/files/decompose/jobs/${encodeURIComponent(jobId)}`,
    {
      timeoutMs: JOB_REQUEST_TIMEOUT_MS,
      headers: { Accept: "application/json" },
      forwardRequestHeaders: ["cookie", "authorization"],
      body: null,
      forwardSearch: false,
      forwardResponseHeaders: JSON_RESPONSE_HEADERS,
      defaultContentType: "application/json",
      logLabel: "[decompose-documents/jobs]",
      onFailure: unavailableResponse,
    },
  );
}

export async function GET(request: NextRequest, context: RouteContext) {
  return proxyJobRequest(request, context);
}

export async function DELETE(request: NextRequest, context: RouteContext) {
  return proxyJobRequest(request, context);
}
