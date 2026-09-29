import { NextResponse } from "next/server";
import { getFastApiBaseUrl } from "./fastapi-internal.ts";

export type ServerAuthStatus = {
  configured: boolean;
  authenticated: boolean;
  username: string | null;
  user_id: string | null;
  role: "admin" | "user" | null;
};

export async function authStatusForRequest(
  request: Request
): Promise<ServerAuthStatus> {
  const cookie = request.headers.get("cookie") || "";
  try {
    const response = await fetch(`${getFastApiBaseUrl()}/api/v1/auth/status`, {
      headers: cookie ? { cookie } : undefined,
      cache: "no-store",
    });
    if (!response.ok) {
      return {
        configured: true,
        authenticated: false,
        username: null,
        user_id: null,
        role: null,
      };
    }
    return (await response.json()) as ServerAuthStatus;
  } catch {
    return {
      configured: true,
      authenticated: false,
      username: null,
      user_id: null,
      role: null,
    };
  }
}

export async function requireAdminApi(
  request: Request
): Promise<NextResponse | null> {
  const status = await authStatusForRequest(request);
  if (!status.authenticated) {
    return NextResponse.json({ detail: "Unauthorized" }, { status: 401 });
  }
  if (status.role !== "admin") {
    return NextResponse.json(
      { detail: "Admin access required" },
      { status: 403 }
    );
  }
  return null;
}
