import { type NextRequest } from "next/server";

import { proxyV2Request } from "@/app/api/v2/_proxy";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

type RouteContext = { params: Promise<{ presentationId: string }> };

export async function POST(request: NextRequest, context: RouteContext) {
  const { presentationId } = await context.params;
  return proxyV2Request(
    request,
    `/api/v2/presentations/${encodeURIComponent(presentationId)}/exports`,
  );
}
