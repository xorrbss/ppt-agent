import { NextRequest, NextResponse } from "next/server";

import { proxyToFastApi } from "@/lib/fastapi-proxy";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

// The public v2 surface includes export artifact downloads (range requests),
// so the bound covers a full response body on a slow link, not just admission.
const V2_UPSTREAM_TIMEOUT_MS = 10 * 60_000;

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
): Promise<Response> {
  return proxyToFastApi(request, backendPath, {
    timeoutMs: V2_UPSTREAM_TIMEOUT_MS,
    redirect: "manual",
    logLabel: "[api/v2]",
    // Timeouts and transport failures share one public envelope.
    onFailure: upstreamUnavailableResponse,
  });
}
