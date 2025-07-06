import { NextResponse } from 'next/server';
import fs from 'fs';

const userConfigPath = process.env.USER_CONFIG_PATH!;
const canChangeKeys = process.env.CAN_CHANGE_KEYS !== 'false';

export async function GET() {
  if (!canChangeKeys) {
    return NextResponse.json({
      error: 'You are not allowed to access this resource',
    })
  }

  if (!fs.existsSync(userConfigPath)) {
    return NextResponse.json({})
  }
  const configData = fs.readFileSync(userConfigPath, 'utf-8')
  return NextResponse.json(JSON.parse(configData))
}

export async function POST(request: Request) {
  if (!canChangeKeys) {
    return NextResponse.json({
      error: 'You are not allowed to access this resource',
    })
  }

  const userConfig = await request.json()

  let existingConfig: LLMConfig = {}
  if (fs.existsSync(userConfigPath)) {
    const configData = fs.readFileSync(userConfigPath, 'utf-8')
    existingConfig = JSON.parse(configData)
  }
  const mergedConfig: LLMConfig = {
    LLM: userConfig.LLM || existingConfig.LLM,
    LLM_PROVIDER_URL: userConfig.LLM_PROVIDER_URL || existingConfig.LLM_PROVIDER_URL,
    LLM_API_KEY: userConfig.LLM_API_KEY,
    OPENAI_API_KEY: userConfig.OPENAI_API_KEY || existingConfig.OPENAI_API_KEY,
    GOOGLE_API_KEY: userConfig.GOOGLE_API_KEY || existingConfig.GOOGLE_API_KEY,
    MODEL: userConfig.MODEL || existingConfig.MODEL,
    PEXELS_API_KEY: userConfig.PEXELS_API_KEY || existingConfig.PEXELS_API_KEY,
    USE_CUSTOM_URL: userConfig.USE_CUSTOM_URL === undefined ? existingConfig.USE_CUSTOM_URL : userConfig.USE_CUSTOM_URL,
  }
  fs.writeFileSync(userConfigPath, JSON.stringify(mergedConfig))
  return NextResponse.json(mergedConfig)
} 