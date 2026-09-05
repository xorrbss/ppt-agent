import { NextResponse } from "next/server";

// EXT-08: liveness probe for the Next.js process. No auth, no upstream calls
// (FastAPI has its own /health and /ready mounted outside /api; see
// servers/fastapi/api/health.py). nginx routes any path outside /api/v1/* to
// Next.js (see nginx.conf), so this is the route a container healthcheck
// hitting the nginx front (port 80) actually reaches for /api/health.
export const dynamic = "force-dynamic";

export async function GET() {
  return NextResponse.json({ status: "ok" });
}
