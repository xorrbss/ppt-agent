import { NextResponse } from "next/server";

import { getUploadLimits } from "@/lib/upload-limits";

export const runtime = "nodejs";

export async function GET() {
  return NextResponse.json(getUploadLimits(), {
    headers: { "Cache-Control": "no-store" },
  });
}
