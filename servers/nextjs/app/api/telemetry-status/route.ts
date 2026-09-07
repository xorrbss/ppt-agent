import { NextResponse } from "next/server";
import { readUserConfigFile } from "@/lib/user-config-store";

export const dynamic = "force-dynamic";

export async function GET() {
  const userConfigPath = process.env.USER_CONFIG_PATH;
  let fileDisabled: string | undefined;
  if (userConfigPath) {
    try {
      const parsed = readUserConfigFile<{ DISABLE_ANONYMOUS_TRACKING?: string }>(
        userConfigPath
      );
      fileDisabled = parsed?.DISABLE_ANONYMOUS_TRACKING;
    } catch {
      fileDisabled = undefined;
    }
  }
  const envValue = process.env.DISABLE_ANONYMOUS_TRACKING;
  const isTrue = (value: string | undefined) => value === "true" || value === "True";
  const isFalse = (value: string | undefined) => value === "false" || value === "False";
  // Anonymous telemetry is off by default for this fork. Either source saying
  // "true" keeps it off (an operator's env is a hard off); it is on only when
  // neither says "true" and one of them explicitly opts in with "false".
  const telemetryEnabled =
    !isTrue(envValue) &&
    !isTrue(fileDisabled) &&
    (isFalse(envValue) || isFalse(fileDisabled));
  return NextResponse.json({ telemetryEnabled });
}

