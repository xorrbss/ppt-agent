import { NextResponse } from "next/server";
import { getFastApiBaseUrl } from "@/lib/fastapi-internal";
import { fetchTimeoutSignal } from "@/lib/fetch-timeout-signal";

// EXT-08: this is the route a container healthcheck curling
// `http://localhost/api/health` through nginx actually reaches — nginx routes
// everything outside /api/v1/* to Next.js (see nginx.conf), never to
// FastAPI's own /health or /ready (servers/fastapi/api/health.py). A bare
// "ok" reply here would let a dead or hung FastAPI process keep the container
// marked healthy forever, so this route also makes one short, bounded call to
// FastAPI's liveness probe and reflects the result: 200 only when both this
// Next.js process and FastAPI answer; 503 when FastAPI does not.
export const dynamic = "force-dynamic";

const FASTAPI_LIVENESS_TIMEOUT_MS = 3_000;

export async function GET() {
  try {
    const response = await fetch(`${getFastApiBaseUrl()}/health`, {
      cache: "no-store",
      signal: fetchTimeoutSignal(FASTAPI_LIVENESS_TIMEOUT_MS),
    });
    if (!response.ok) {
      return NextResponse.json(
        { status: "degraded", fastapi: "unhealthy" },
        { status: 503 },
      );
    }
  } catch {
    return NextResponse.json(
      { status: "degraded", fastapi: "unreachable" },
      { status: 503 },
    );
  }
  return NextResponse.json({ status: "ok" });
}
