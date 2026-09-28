import { NextResponse } from "next/server";
import { proxyToFastApi } from "@/lib/fastapi-proxy";
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

function forwardProviderSettings(
  request: Request,
  method: "GET" | "PUT",
  failureMessage: string,
  body?: string
) {
  return proxyToFastApi(request, "/api/v1/admin/provider-settings", {
    timeoutMs: PROVIDER_SETTINGS_TIMEOUT_MS,
    method,
    // The admin browser session terminates here; only its cookie is forwarded.
    includeServerAuth: false,
    forwardRequestHeaders: ["cookie"],
    headers: body ? { "content-type": "application/json" } : {},
    body: body ?? null,
    forwardSearch: false,
    forwardResponseHeaders: ["content-type"],
    defaultContentType: "application/json",
    cacheControl: null,
    logLabel: "[user-config]",
    // Upstream (FastAPI) failure or timeout, not a fault in this route:
    // 502, consistent with every other bounded-fetch route in app/api/*.
    onFailure: () =>
      NextResponse.json({ error: failureMessage, status: 502 }, { status: 502 }),
  });
}

export async function GET(request: Request) {
  const denied = await requireAdminApi(request);
  if (denied) return denied;
  if (!canChangeKeys) return immutableResponse();

  return forwardProviderSettings(
    request,
    "GET",
    "Unable to read provider settings"
  );
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
    return await forwardProviderSettings(
      request,
      "PUT",
      "Unable to save provider settings",
      body
    );
  } catch (error) {
    if (error instanceof SyntaxError) {
      return NextResponse.json(
        { error: "Invalid user config JSON body", status: 400 },
        { status: 400 }
      );
    }
    // Failure reading the request body itself; upstream failures are handled
    // by the forwarder's onFailure.
    return NextResponse.json(
      { error: "Unable to save provider settings", status: 502 },
      { status: 502 }
    );
  }
}
