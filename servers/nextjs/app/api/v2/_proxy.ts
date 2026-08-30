import { NextRequest, NextResponse } from "next/server";

import {
  getFastApiAuthHeaders,
  getFastApiBaseUrl,
} from "@/lib/fastapi-internal";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

const FORWARDED_REQUEST_HEADERS = [
  "accept",
  "authorization",
  "cookie",
  "content-type",
  "idempotency-key",
  "if-none-match",
  "if-modified-since",
  "if-range",
  "range",
  "x-request-id",
] as const;

const FORWARDED_RESPONSE_HEADERS = [
  "accept-ranges",
  "cache-control",
  "content-length",
  "content-range",
  "content-disposition",
  "content-type",
  "etag",
  "expires",
  "last-modified",
  "location",
  "retry-after",
  "x-request-id",
] as const;

function requestHeaders(request: NextRequest): Headers {
  const headers = new Headers(getFastApiAuthHeaders());
  for (const name of FORWARDED_REQUEST_HEADERS) {
    const value = request.headers.get(name);
    if (value) headers.set(name, value);
  }
  return headers;
}

function responseHeaders(response: Response): Headers {
  const headers = new Headers();
  for (const name of FORWARDED_RESPONSE_HEADERS) {
    const value = response.headers.get(name);
    if (value) headers.set(name, value);
  }
  headers.set("cache-control", "no-store");
  return headers;
}

function upstreamUnavailableResponse(): NextResponse {
  return NextResponse.json(
    {
      detail: {
        reasonCode: "public_v2_upstream_unreachable",
      },
    },
    { status: 502, headers: { "cache-control": "no-store" } },
  );
}

/** Forward a public v2 request to FastAPI without doing generation or export work. */
export async function proxyV2Request(
  request: NextRequest,
  backendPath: string,
): Promise<NextResponse> {
  const method = request.method.toUpperCase();
  const requestBody = method !== "GET" && method !== "HEAD" ? request.body : null;
  try {
    const response = await fetch(
      `${getFastApiBaseUrl()}${backendPath}${request.nextUrl.search}`,
      {
        method,
        headers: requestHeaders(request),
        cache: "no-store",
        redirect: "manual",
        signal: request.signal,
        ...(requestBody === null
          ? {}
          : { body: requestBody, duplex: "half" as const }),
      },
    );
    return new NextResponse(response.body, {
      status: response.status,
      headers: responseHeaders(response),
    });
  } catch (error) {
    console.error("[api/v2] FastAPI request failed", error);
    return upstreamUnavailableResponse();
  }
}
