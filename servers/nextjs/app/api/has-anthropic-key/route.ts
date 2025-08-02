import { NextResponse } from "next/server";

export const dynamic = "force-dynamic";

export async function GET() {
  const hasKey = process.env.ANTHROPIC_API_KEY !== "";
  return NextResponse.json({ hasKey });
}
