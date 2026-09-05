import { NextRequest, NextResponse } from "next/server";

// Sink for the report-only CSP's `report-uri` (see next.config.mjs). A
// browser POSTs a violation report here whenever the app-page policy would
// have blocked something, so there is data to decide on enforcing instead of
// console-only noise nobody collects. This route only logs and acknowledges:
// it must never fail the request back to the browser, and it is reachable
// without a session (proxy.ts exempts it) because a report can legitimately
// come from an anonymous visitor, e.g. on the public /p/ share view.
export const dynamic = "force-dynamic";

// A report body is small JSON; bound it well above any real report so a
// misbehaving sender cannot use this endpoint to buffer unbounded data.
const MAX_REPORT_BYTES = 32 * 1024;

export async function POST(request: NextRequest) {
  try {
    const body = await request.text();
    if (body.length > MAX_REPORT_BYTES) {
      console.warn("[csp-report] report exceeded size limit, dropped");
    } else if (body) {
      console.warn("[csp-report]", body);
    }
  } catch {
    // A malformed or unreadable report body is not this route's problem;
    // reporting is best-effort.
  }
  return new NextResponse(null, { status: 204 });
}
