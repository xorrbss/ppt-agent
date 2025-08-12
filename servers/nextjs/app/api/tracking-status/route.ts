import { NextResponse } from 'next/server';

export const dynamic = 'force-dynamic';

export async function GET() {
  const isDisabled = process.env.DISABLE_ANONYMOUS_TRACKING === 'true' || process.env.DISABLE_ANONYMOUS_TRACKING === 'True';
  const trackingEnabled = !isDisabled;
  return NextResponse.json({ trackingEnabled });
}


