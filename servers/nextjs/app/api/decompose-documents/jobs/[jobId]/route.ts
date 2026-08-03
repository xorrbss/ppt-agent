import { NextRequest, NextResponse } from "next/server";

import {
  getFastApiAuthHeaders,
  getFastApiBaseUrl,
} from "@/lib/fastapi-internal";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

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
  const headers = new Headers(getFastApiAuthHeaders());
  const cookie = request.headers.get("cookie");
  const authorization = request.headers.get("authorization");

  headers.set("Accept", "application/json");
  if (cookie) headers.set("Cookie", cookie);
  if (authorization) headers.set("Authorization", authorization);

  try {
    const response = await fetch(
      `${getFastApiBaseUrl()}/api/v1/ppt/files/decompose/jobs/${encodeURIComponent(jobId)}`,
      {
        method: request.method,
        headers,
        cache: "no-store",
      },
    );
    const responseHeaders = new Headers({
      "Cache-Control": "no-store",
      "Content-Type": response.headers.get("content-type") ?? "application/json",
    });
    const requestId = response.headers.get("x-request-id");
    if (requestId) responseHeaders.set("X-Request-Id", requestId);

    return new NextResponse(response.body, {
      status: response.status,
      headers: responseHeaders,
    });
  } catch (error) {
    console.error("[decompose-documents/jobs] Failed to poll or cancel job", error);
    return unavailableResponse();
  }
}

export async function GET(request: NextRequest, context: RouteContext) {
  return proxyJobRequest(request, context);
}

export async function DELETE(request: NextRequest, context: RouteContext) {
  return proxyJobRequest(request, context);
}
