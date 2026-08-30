import { type NextRequest } from "next/server";

import { proxyV2Request } from "@/app/api/v2/_proxy";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

type RouteContext = { params: Promise<{ artifactId: string }> };

export async function GET(request: NextRequest, context: RouteContext) {
  const { artifactId } = await context.params;
  return proxyV2Request(
    request,
    `/api/v2/artifacts/${encodeURIComponent(artifactId)}/download`,
  );
}
