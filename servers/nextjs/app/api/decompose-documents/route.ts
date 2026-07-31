import { NextRequest, NextResponse } from "next/server";

import {
  getFastApiAuthHeaders,
  getFastApiBaseUrl,
} from "@/lib/fastapi-internal";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";
export const maxDuration = 600;

export async function POST(request: NextRequest) {
  const headers = new Headers(getFastApiAuthHeaders());
  const contentType = request.headers.get("content-type");
  const cookie = request.headers.get("cookie");
  const authorization = request.headers.get("authorization");

  headers.set("Accept", "application/json");
  if (contentType) headers.set("Content-Type", contentType);
  if (cookie) headers.set("Cookie", cookie);
  if (authorization) headers.set("Authorization", authorization);

  try {
    const response = await fetch(
      `${getFastApiBaseUrl()}/api/v1/ppt/files/decompose`,
      {
        method: "POST",
        headers,
        body: await request.text(),
        cache: "no-store",
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
    console.error(
      "[decompose-documents] Failed to reach the document service",
      error,
    );
    return NextResponse.json(
      { detail: "Document processing service is unavailable" },
      { status: 502 },
    );
  }
}
