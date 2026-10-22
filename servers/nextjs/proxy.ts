import { NextRequest, NextResponse } from "next/server";
import { getFastApiBaseUrl } from "@/lib/fastapi-internal";

/**
 * API-only: session required for all /api/* except auth and the other exemptions below.
 * Page routes are protected in server layouts (unknown URLs still 404; login uses relative redirects).
 */
function isFastApiApiPath(pathname: string): boolean {
  return (
    pathname === "/api/v1" ||
    pathname.startsWith("/api/v1/") ||
    pathname === "/api/v2" ||
    pathname.startsWith("/api/v2/")
  );
}

function rewriteToFastApi(request: NextRequest): NextResponse {
  const destination = new URL(
    `${request.nextUrl.pathname}${request.nextUrl.search}`,
    `${getFastApiBaseUrl()}/`
  );
  return NextResponse.rewrite(destination);
}

type AuthStatus = {
  configured: boolean;
  authenticated: boolean;
};

function isAuthDisabled(): boolean {
  const raw = process.env.DISABLE_AUTH?.trim().toLowerCase();
  return raw === "1" || raw === "true" || raw === "yes" || raw === "on";
}

// A hung backend must not hold every API request open. A timeout is handled
// by the same catch as a network error: unauthenticated, never fail-open.
const AUTH_STATUS_TIMEOUT_MS = 5_000;

async function getAuthStatus(request: NextRequest): Promise<AuthStatus> {
  const cookieHeader = request.headers.get("cookie");
  const authStatusUrl = `${getFastApiBaseUrl()}/api/v1/auth/status`;
  try {
    const response = await fetch(authStatusUrl, {
      method: "GET",
      headers: cookieHeader ? { Cookie: cookieHeader } : undefined,
      cache: "no-store",
      signal: AbortSignal.timeout(AUTH_STATUS_TIMEOUT_MS),
    });
    if (!response.ok) {
      return { configured: true, authenticated: false };
    }
    const payload = (await response.json()) as Partial<AuthStatus>;
    return {
      configured: Boolean(payload.configured),
      authenticated: Boolean(payload.authenticated),
    };
  } catch {
    return { configured: true, authenticated: false };
  }
}

function isApiAuthExempt(pathname: string): boolean {
  return (
    pathname.startsWith("/api/v1/auth/") ||
    // Liveness probe (EXT-08): container/process healthchecks hit this
    // through nginx with no session. It returns only a static status, so
    // exempting it discloses nothing.
    pathname === "/api/health" ||
    pathname.startsWith("/api/export-presentation-data/") ||
    /** Read-only public share view (token-scoped; FastAPI re-checks on the
     * normalized path, so a traversal here still fails auth there). */
    pathname.startsWith("/api/v1/ppt/presentation/public/") ||
    // CSP violation report sink (next.config.mjs report-uri): a browser can
    // legitimately POST here with no session, e.g. from the anonymous /p/
    // share view, so this must not require one. It only logs; see route.ts.
    pathname === "/api/csp-report"
  );
}

export async function proxy(request: NextRequest) {
  const { pathname } = request.nextUrl;

  if (isAuthDisabled()) {
    return NextResponse.next();
  }

  if (request.method === "OPTIONS" || isApiAuthExempt(pathname)) {
    return NextResponse.next();
  }

  const authorization = request.headers.get("authorization") || "";
  if (authorization.toLowerCase().startsWith("bearer sk-presenton-")) {
    // FastAPI validates admin-owned API keys. Do not treat them as browser
    // sessions or expose them to local Next.js configuration routes.
    return isFastApiApiPath(pathname)
      ? rewriteToFastApi(request)
      : NextResponse.json(
          { detail: "API keys are only accepted by the Presenton API" },
          { status: 403 }
        );
  }

  const authStatus = await getAuthStatus(request);
  if (authStatus.authenticated) {
    return NextResponse.next();
  }
  if (!authStatus.configured) {
    return NextResponse.json(
      { detail: "Login setup is required", setup_required: true },
      { status: 428, headers: { "Cache-Control": "no-store" } }
    );
  }
  return NextResponse.json(
    { detail: "Unauthorized" },
    { status: 401, headers: { "Cache-Control": "no-store" } }
  );
}

export const config = {
  matcher: ["/api/:path*"],
};
