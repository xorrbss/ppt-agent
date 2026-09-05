import { NextResponse } from "next/server";
import { getFastApiBaseUrl } from "@/lib/fastapi-internal";
import { fetchTimeoutSignal } from "@/lib/fetch-timeout-signal";
import { requireAdminApi } from "@/lib/server-auth-role";

const canChangeKeys = process.env.CAN_CHANGE_KEYS !== "false";
// Provider settings are a small JSON document; a save also validates keys
// against the provider, which is why this is not a few seconds.
const PROVIDER_SETTINGS_TIMEOUT_MS = 30_000;

function immutableResponse() {
  return NextResponse.json(
    { error: "You are not allowed to access this resource", status: 403 },
    { status: 403 }
  );
}

async function forwardProviderSettings(
  request: Request,
  method: "GET" | "PUT",
  body?: string
) {
  const cookie = request.headers.get("cookie") || "";
  const response = await fetch(
    `${getFastApiBaseUrl()}/api/v1/admin/provider-settings`,
    {
      method,
      headers: {
        ...(cookie ? { cookie } : {}),
        ...(body ? { "content-type": "application/json" } : {}),
      },
      body,
      cache: "no-store",
      signal: fetchTimeoutSignal(PROVIDER_SETTINGS_TIMEOUT_MS, request.signal),
    }
  );
  const payload = await response.text();
  return new NextResponse(payload || null, {
    status: response.status,
    headers: {
      "content-type":
        response.headers.get("content-type") || "application/json",
    },
  });
}

export async function GET(request: Request) {
  const denied = await requireAdminApi(request);
  if (denied) return denied;
  if (!canChangeKeys) return immutableResponse();

  try {
    return await forwardProviderSettings(request, "GET");
  } catch {
    // Upstream (FastAPI) failure or timeout, not a fault in this route:
    // 502, consistent with every other bounded-fetch route in app/api/*.
    return NextResponse.json(
      { error: "Unable to read provider settings", status: 502 },
      { status: 502 }
    );
  }
}

export async function POST(request: Request) {
  const denied = await requireAdminApi(request);
  if (denied) return denied;
  if (!canChangeKeys) return immutableResponse();

  try {
    const body = await request.text();
    if (!body.trim()) {
      return NextResponse.json(
        { error: "Invalid user config JSON body", status: 400 },
        { status: 400 }
      );
    }
    const parsed = JSON.parse(body) as unknown;
    if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
      return NextResponse.json(
        { error: "Invalid user config JSON body", status: 400 },
        { status: 400 }
      );
    }
    return await forwardProviderSettings(request, "PUT", body);
  } catch (error) {
    if (error instanceof SyntaxError) {
      return NextResponse.json(
        { error: "Invalid user config JSON body", status: 400 },
        { status: 400 }
      );
    }
    // Upstream (FastAPI) failure or timeout, not a fault in this route:
    // 502, consistent with every other bounded-fetch route in app/api/*.
    return NextResponse.json(
      { error: "Unable to save provider settings", status: 502 },
      { status: 502 }
    );
  }
}
