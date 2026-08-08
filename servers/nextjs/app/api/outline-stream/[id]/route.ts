import { NextRequest, NextResponse } from "next/server";

import {
  getFastApiAuthHeaders,
  getFastApiBaseUrl,
} from "@/lib/fastapi-internal";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";
export const maxDuration = 600;

export async function GET(
  request: NextRequest,
  context: { params: Promise<{ id: string }> },
) {
  const { id } = await context.params;
  if (!id) {
    return NextResponse.json(
      { detail: "Missing presentation id" },
      { status: 400 },
    );
  }

  const headers = new Headers(getFastApiAuthHeaders());
  const cookie = request.headers.get("cookie");
  const authorization = request.headers.get("authorization");

  headers.set("Accept", "text/event-stream");
  headers.set("Cache-Control", "no-cache");
  if (cookie) headers.set("Cookie", cookie);
  if (authorization) headers.set("Authorization", authorization);

  try {
    const response = await fetch(
      `${getFastApiBaseUrl()}/api/v1/ppt/outlines/stream/${encodeURIComponent(id)}`,
      {
        method: "GET",
        headers,
        cache: "no-store",
      },
    );

    if (!response.ok) {
      return new NextResponse(response.body, {
        status: response.status,
        headers: {
          "Cache-Control": "no-store",
          "Content-Type":
            response.headers.get("content-type") ?? "application/json",
        },
      });
    }

    return new NextResponse(response.body, {
      status: response.status,
      headers: {
        "Cache-Control": "no-cache, no-transform",
        Connection: "keep-alive",
        "Content-Type": "text/event-stream; charset=utf-8",
        "X-Accel-Buffering": "no",
      },
    });
  } catch (error) {
    console.error("[outline-stream] Failed to reach the outline service", error);
    return NextResponse.json(
      { detail: "Outline generation service is unavailable" },
      { status: 502 },
    );
  }
}
