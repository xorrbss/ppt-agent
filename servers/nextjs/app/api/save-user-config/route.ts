import { NextRequest, NextResponse } from "next/server";
import fs from "fs";

export async function POST(request: NextRequest) {
  try {
    const body = await request.json();
    const { provider, apiKey, userConfigPath } = body;

    if (!userConfigPath) {
      return NextResponse.json(
        { error: "User config path not found" },
        { status: 500 }
      );
    }

    // Create config object based on provider
    const config = {
      LLM: provider === "google" ? "gemini-pro" : "gpt-4",
      OPENAI_API_KEY: provider === "openai" ? apiKey : undefined,
      GOOGLE_API_KEY: provider === "google" ? apiKey : undefined,
    };

    // Read existing config if it exists
    let existingConfig = {};
    if (fs.existsSync(userConfigPath)) {
      try {
        const existingData = fs.readFileSync(userConfigPath, 'utf-8');
        existingConfig = JSON.parse(existingData);
      } catch (error) {
        console.error("Error reading existing config:", error);
      }
    }

    // Merge with existing config
    const mergedConfig = {
      ...existingConfig,
      ...config,
    };

    // Write to file
    fs.writeFileSync(userConfigPath, JSON.stringify(mergedConfig, null, 2));

    return NextResponse.json({ success: true, config: mergedConfig });
  } catch (error) {
    console.error("Error saving user config:", error);
    return NextResponse.json(
      { error: "Failed to save configuration" },
      { status: 500 }
    );
  }
}
