import { NextResponse } from "next/server";
import fs from "fs";
import { LLMConfig } from "@/types/llm_config";

const userConfigPath = process.env.USER_CONFIG_PATH!;
const canChangeKeys = process.env.CAN_CHANGE_KEYS !== "false";

export async function GET() {
  if (!canChangeKeys) {
    return NextResponse.json({
      error: "You are not allowed to access this resource",
      status: 403,
    });
  }
  if (!userConfigPath) {
    return NextResponse.json({
      error: "User config path not found",
      status: 500,
    });
  }

  if (!fs.existsSync(userConfigPath)) {
    return NextResponse.json({});
  }
  const configData = fs.readFileSync(userConfigPath, "utf-8");
  return NextResponse.json(JSON.parse(configData));
}

export async function POST(request: Request) {
  if (!canChangeKeys) {
    return NextResponse.json({
      error: "You are not allowed to access this resource",
    });
  }

  const userConfig = await request.json();

  console.log('userConfig', userConfig);
  let existingConfig: LLMConfig = {};
  if (fs.existsSync(userConfigPath)) {
    const configData = fs.readFileSync(userConfigPath, "utf-8");

    existingConfig = JSON.parse(configData);
  }
  const definedIncomingEntries = Object.entries(userConfig).filter(
    ([, value]) => value !== undefined
  );
  const mergedConfig: LLMConfig = {
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
  };
  fs.writeFileSync(userConfigPath, JSON.stringify(mergedConfig));
  return NextResponse.json(mergedConfig);
}
