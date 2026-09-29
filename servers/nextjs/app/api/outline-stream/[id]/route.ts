import { NextRequest, NextResponse } from "next/server";
import { Agent } from "undici";

import { errorPropagatingStream, fetchFastApi } from "@/lib/fastapi-proxy";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";
export const maxDuration = 600;

// Undici's default Agent applies a 300s headersTimeout and a 300s
// bodyTimeout to every response it manages. The outline/brief stage can
// legitimately stay silent (no SSE bytes) for longer than that while the
// backend is composing a slide, so the default dispatcher would abort a
// perfectly healthy stream mid-flight. This route owns a single dedicated
// dispatcher (created once at module scope, not per request, so we do not
// leak a connection pool per call) with both timeouts disabled — the route's
// own `maxDuration` above remains the real upper bound.
const outlineStreamDispatcher = new Agent({
  headersTimeout: 0,
  bodyTimeout: 0,
});

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

  try {
    // No proxy-side deadline: the route's `maxDuration` is the upper bound, and
    // a client disconnect ends the stream through the response body's cancel.
    const response = await fetchFastApi(
      request,
      `/api/v1/ppt/outlines/stream/${encodeURIComponent(id)}`,
      {
        timeoutMs: null,
        method: "GET",
        headers: { Accept: "text/event-stream", "Cache-Control": "no-cache" },
        forwardRequestHeaders: ["cookie", "authorization"],
        forwardSearch: false,
        linkClientAbort: false,
        dispatcher: outlineStreamDispatcher,
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

    const outgoingBody = response.body
      ? errorPropagatingStream(response.body, "[outline-stream]")
      : null;

    return new NextResponse(outgoingBody, {
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
