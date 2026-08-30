import { type NextRequest } from "next/server";

import { proxyV2Request } from "@/app/api/v2/_proxy";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

export async function POST(request: NextRequest) {
  return proxyV2Request(request, "/api/v2/presentations/generations");
}
