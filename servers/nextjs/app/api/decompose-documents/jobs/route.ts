import { NextRequest, NextResponse } from "next/server";

import {
  getFastApiAuthHeaders,
  getFastApiBaseUrl,
} from "@/lib/fastapi-internal";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

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
  const headers = new Headers(getFastApiAuthHeaders());
  const contentType = request.headers.get("content-type");
  const cookie = request.headers.get("cookie");
  const authorization = request.headers.get("authorization");
  const idempotencyKey = request.headers.get("idempotency-key");

  headers.set("Accept", "application/json");
  if (contentType) headers.set("Content-Type", contentType);
  if (cookie) headers.set("Cookie", cookie);
  if (authorization) headers.set("Authorization", authorization);
  if (idempotencyKey) headers.set("Idempotency-Key", idempotencyKey);

  try {
    const response = await fetch(
      `${getFastApiBaseUrl()}/api/v1/ppt/files/decompose/jobs`,
      {
        method: "POST",
        headers,
        body: await request.text(),
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
    console.error("[decompose-documents/jobs] Failed to start job", error);
    return unavailableResponse();
  }
}
