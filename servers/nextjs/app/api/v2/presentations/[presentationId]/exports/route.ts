import { NextRequest, NextResponse } from "next/server";

import { proxyV2Request } from "@/app/api/v2/_proxy";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

type RouteContext = { params: Promise<{ presentationId: string }> };

function record(value: unknown): Record<string, unknown> | null {
  return value !== null && typeof value === "object" && !Array.isArray(value)
    ? value as Record<string, unknown>
    : null;
}

function rejected(reasonCode: string): NextResponse {
  return NextResponse.json(
    { detail: { reasonCode } },
    { status: 400, headers: { "cache-control": "no-store" } },
  );
}

export async function POST(request: NextRequest, context: RouteContext) {
  const payload = await request.json().catch(() => null);
  const body = record(payload);
  const output = body ? record(body.output) : null;
  if (!body || !output) return rejected("public_v2_invalid_export_request");
  // This browser route is intentionally Fidelity-only. Do not let a caller
  // turn an unsupported Hybrid mode into a public capability claim.
  if (
    body.qualityPolicy !== "release-v1" ||
    (output.format !== "pptx" && output.format !== "pdf") ||
    output.mode !== "fidelity-raster" ||
    output.fontEmbedding !== "none"
  ) {
    return rejected("public_v2_fidelity_raster_only");
  }
  const { presentationId } = await context.params;
  const headers = new Headers(request.headers);
  headers.delete("content-length");
  const fidelityRequest = new NextRequest(request.url, {
    method: "POST",
    headers,
    body: JSON.stringify(body),
  });
  return proxyV2Request(
    fidelityRequest,
    `/api/v2/presentations/${encodeURIComponent(presentationId)}/exports`,
  );
}
