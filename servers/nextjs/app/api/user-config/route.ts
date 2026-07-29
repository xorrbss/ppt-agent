import { NextResponse } from "next/server";
import { getFastApiBaseUrl } from "@/lib/fastapi-internal";
import { requireAdminApi } from "@/lib/server-auth-role";

const canChangeKeys = process.env.CAN_CHANGE_KEYS !== "false";

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

export async function GET() {
  if (!canChangeKeys) {
    return NextResponse.json({
      error: "You are not allowed to access this resource",
      status: 403,
    });
  }
  const userConfigPath = getUserConfigPath();
  if (!userConfigPath) {
    return NextResponse.json({
      error: "User config path not found",
      status: 500,
    });
  }

  try {
    return await forwardProviderSettings(request, "GET");
  } catch {
    return NextResponse.json(
      { error: "Unable to read provider settings", status: 500 },
      { status: 500 }
    );
  }
}

export async function POST(request: Request) {
  if (!canChangeKeys) {
    return NextResponse.json({
      error: "You are not allowed to access this resource",
    });
  }

  const userConfigPath = getUserConfigPath();
  if (!userConfigPath) {
    return NextResponse.json(
      { error: "User config path not found", status: 500 },
      { status: 500 }
    );
  }

  try {
    const userConfig = stripAuthFieldsFromIncoming(
      (await request.json()) as Record<string, unknown>
    ) as LLMConfig;
    const definedIncomingEntries = Object.entries(userConfig).filter(
      ([, value]) => value !== undefined
    );
    const mergedConfig = updateUserConfigFile<LLMConfig>(
      userConfigPath,
      (existingConfig) => ({
        ...existingConfig,
        ...Object.fromEntries(definedIncomingEntries),
        USE_CUSTOM_URL:
          userConfig.USE_CUSTOM_URL === undefined
            ? existingConfig.USE_CUSTOM_URL
            : userConfig.USE_CUSTOM_URL,
        OPEN_WEBUI_IMAGE_URL:
          userConfig.OPEN_WEBUI_IMAGE_URL || existingConfig.OPEN_WEBUI_IMAGE_URL,
        OPEN_WEBUI_IMAGE_API_KEY:
          userConfig.OPEN_WEBUI_IMAGE_API_KEY || existingConfig.OPEN_WEBUI_IMAGE_API_KEY,
        CODEX_MODEL: userConfig.CODEX_MODEL || existingConfig.CODEX_MODEL,
        CODEX_ACCESS_TOKEN: existingConfig.CODEX_ACCESS_TOKEN,
        CODEX_REFRESH_TOKEN: existingConfig.CODEX_REFRESH_TOKEN,
        CODEX_TOKEN_EXPIRES: existingConfig.CODEX_TOKEN_EXPIRES,
        CODEX_ACCOUNT_ID: existingConfig.CODEX_ACCOUNT_ID,
        CODEX_USERNAME: existingConfig.CODEX_USERNAME,
        CODEX_EMAIL: existingConfig.CODEX_EMAIL,
        CODEX_IS_PRO: existingConfig.CODEX_IS_PRO,
        DISABLE_IMAGE_GENERATION: Object.prototype.hasOwnProperty.call(
          userConfig,
          "DISABLE_IMAGE_GENERATION"
        )
          ? userConfig.DISABLE_IMAGE_GENERATION
          : existingConfig.DISABLE_IMAGE_GENERATION,
        DISABLE_ANONYMOUS_TRACKING: Object.prototype.hasOwnProperty.call(
          userConfig,
          "DISABLE_ANONYMOUS_TRACKING"
        )
          ? userConfig.DISABLE_ANONYMOUS_TRACKING
          : existingConfig.DISABLE_ANONYMOUS_TRACKING,
      })
    );
    return NextResponse.json(
      stripAuthFields(mergedConfig as Record<string, unknown>)
    );
  } catch {
    return NextResponse.json(
      { error: "Unable to save provider settings", status: 500 },
      { status: 500 }
    );
  }
}
