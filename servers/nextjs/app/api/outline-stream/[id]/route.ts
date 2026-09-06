import { NextRequest, NextResponse } from "next/server";
import { Agent } from "undici";

import {
  getFastApiAuthHeaders,
  getFastApiBaseUrl,
} from "@/lib/fastapi-internal";

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

/**
 * Wraps an upstream fetch body in a pass-through stream that forwards the
 * error to the outgoing response instead of swallowing it.
 *
 * Passing `response.body` straight into `new NextResponse(...)` lets a
 * mid-stream failure (e.g. the upstream socket resetting, or — before this
 * fix — the undici body timeout firing) end the outgoing stream cleanly from
 * the browser's point of view: the EventSource sees a normal close, not a
 * transport error, so its reconnect logic never runs. Reading explicitly and
 * calling `controller.error(...)` on read rejection instead errors the
 * outgoing stream, which aborts the HTTP response non-gracefully so the
 * browser observes a real connection failure and reconnects.
 */
function toErrorPropagatingStream(
  upstreamBody: ReadableStream<Uint8Array>,
): ReadableStream<Uint8Array> {
  const reader = upstreamBody.getReader();
  return new ReadableStream<Uint8Array>({
    async pull(controller) {
      try {
        const { done, value } = await reader.read();
        if (done) {
          controller.close();
          return;
        }
        controller.enqueue(value);
      } catch (error) {
        console.error(
          "[outline-stream] Upstream body stream errored mid-flight",
          error,
        );
        controller.error(
          error instanceof Error ? error : new Error(String(error)),
        );
      }
    },
    cancel(reason) {
      return reader.cancel(reason);
    },
  });
}

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
    const response = await fetchWithDispatcher(
      `${getFastApiBaseUrl()}/api/v1/ppt/outlines/stream/${encodeURIComponent(id)}`,
      {
        method: "GET",
        headers,
        cache: "no-store",
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
      ? toErrorPropagatingStream(response.body)
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
