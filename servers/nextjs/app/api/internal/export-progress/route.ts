import { NextResponse, type NextRequest } from "next/server";

import { readExportProgress } from "@/lib/authored-hybrid/export-progress";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

/** Matches the workspace operation identifier the export request carried. */
const OPERATION_ID_PATTERN =
  /^[0-9a-f]{8}-?[0-9a-f]{4}-?[0-9a-f]{4}-?[0-9a-f]{4}-?[0-9a-f]{12}$/i;

/**
 * Read-only progress for an in-flight authored-hybrid export.
 *
 * The export itself is produced by `POST /api/export-presentation`; this route
 * exists only so the caller waiting on that request can narrate it. It is
 * therefore side-effect free, and a miss is an ordinary 404 rather than an
 * error: progress is in-process state that a restart legitimately drops while
 * the export keeps running.
 *
 * Authentication is the standard session check applied to every `/api/` path by
 * `proxy.ts` — this route is deliberately absent from that file's exempt list.
 */
export async function GET(request: NextRequest) {
  const operationId = request.nextUrl.searchParams.get("operationId")?.trim();
  if (!operationId || !OPERATION_ID_PATTERN.test(operationId)) {
    return NextResponse.json(
      { error: "invalid_operation_id" },
      { status: 400 },
    );
  }

  const progress = readExportProgress(operationId);
  if (!progress) {
    return NextResponse.json({ error: "progress_unavailable" }, { status: 404 });
  }

  return NextResponse.json(
    { operationId, ...progress },
    { headers: { "cache-control": "no-store" } },
  );
}
