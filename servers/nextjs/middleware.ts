import { NextRequest, NextResponse } from "next/server";

const PUBLIC_PATHS = new Set([
  "/",
  "/favicon.ico",
  "/apple-icon.png",
  "/icon1.svg",
  "/icon2.png",
  "/api/telemetry-status",
]);

const PUBLIC_PREFIXES = ["/_next/", "/api/v1/auth/"];

/**
 * Build the URL the browser used to reach the app. When nginx proxies to
 * Next on :3000, `request.nextUrl.origin` is often `http://localhost:3000`
 * (wrong for redirects). Prefer reverse-proxy headers instead.
 */
function getExternalOrigin(request: NextRequest): string {
  const xfHost =
    request.headers.get("x-forwarded-host")?.split(",")[0]?.trim() ?? "";
  if (xfHost) {
    const xfProto =
      request.headers.get("x-forwarded-proto")?.split(",")[0]?.trim().toLowerCase() ??
      "";
    const proto =
      xfProto === "https" || xfProto === "http" ? xfProto : "http";
    return `${proto}://${xfHost}`;
  }
  return request.nextUrl.origin;
}

function isPublicRequest(pathname: string): boolean {
  if (PUBLIC_PATHS.has(pathname)) {
    return true;
  }

  if (PUBLIC_PREFIXES.some((prefix) => pathname.startsWith(prefix))) {
    return true;
  }

  // Allow requests for static files in /public.
  if (/\.[a-zA-Z0-9]+$/.test(pathname)) {
    return true;
  }

  return false;
}

function getFastApiBaseUrl(request: NextRequest): string {
  // Server-side-only override. Used by the Docker runtime so the Next.js
  // middleware can reach FastAPI directly inside the container (nginx's
  // port is not reachable from inside the Next.js process).
  const internal = process.env.FAST_API_INTERNAL_URL?.trim();
  if (internal) {
    return internal.replace(/\/+$/, "");
  }

  const configured = process.env.NEXT_PUBLIC_FAST_API?.trim();
  if (configured) {
    return configured.replace(/\/+$/, "");
  }

  if (process.env.NODE_ENV === "development") {
    return "http://127.0.0.1:8000";
  }

  // Fallback: reuse the incoming origin (works when Next.js and FastAPI
  // are served from the same origin, e.g. behind nginx on the same host).
  return request.nextUrl.origin;
}

type AuthStatus = {
  configured: boolean;
  authenticated: boolean;
};

async function getAuthStatus(request: NextRequest): Promise<AuthStatus> {
  const cookieHeader = request.headers.get("cookie");
  const authStatusUrl = `${getFastApiBaseUrl(request)}/api/v1/auth/status`;

  try {
    const response = await fetch(authStatusUrl, {
      method: "GET",
      headers: cookieHeader ? { Cookie: cookieHeader } : undefined,
      cache: "no-store",
    });

    if (!response.ok) {
      return {
        configured: true,
        authenticated: false,
      };
    }

    const payload = (await response.json()) as Partial<AuthStatus>;
    return {
      configured: Boolean(payload.configured),
      authenticated: Boolean(payload.authenticated),
    };
  } catch {
    return {
      configured: true,
      authenticated: false,
    };
  }
}

export async function middleware(request: NextRequest) {
  const { pathname } = request.nextUrl;

  if (request.method === "OPTIONS" || isPublicRequest(pathname)) {
    return NextResponse.next();
  }

  const authStatus = await getAuthStatus(request);
  if (authStatus.authenticated) {
    return NextResponse.next();
  }

  if (pathname.startsWith("/api/")) {
    const statusCode = authStatus.configured ? 401 : 428;
    const detail = authStatus.configured
      ? "Unauthorized"
      : "Login setup is required";

    return NextResponse.json(
      { detail },
      {
        status: statusCode,
        headers: {
          "Cache-Control": "no-store, must-revalidate",
        },
      }
    );
  }

  const redirectUrl = new URL("/", getExternalOrigin(request));
  if (pathname !== "/") {
    redirectUrl.searchParams.set("reason", "unauthorized");
  }
  return NextResponse.redirect(redirectUrl);
}

export const config = {
  matcher: ["/:path*"],
};
