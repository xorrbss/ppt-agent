import crypto from "crypto";
import { requestRuntimeSettingsRefresh } from "../utils/runtime-settings-refresh";

type UserConfigRuntimeDependencies = {
  persistUserConfig?: (userConfig: UserConfig) => void;
  refreshRuntimeSettings: (userConfig?: UserConfig) => Promise<void>;
};

type UserConfigHandlerSetup = (
  refreshRuntimeSettings: (userConfig?: UserConfig) => Promise<void>,
) => void;

export function initializeUserConfigRuntime(
  fastApiPort: number,
  setupHandlers: UserConfigHandlerSetup,
): FastApiEnv {
  const refreshToken = crypto.randomBytes(32).toString("base64url");
  setupHandlers((userConfig) =>
    requestRuntimeSettingsRefresh(fastApiPort, refreshToken, userConfig),
  );
  return { PRESENTON_RUNTIME_SETTINGS_REFRESH_TOKEN: refreshToken };
}

export async function saveUserConfigAndRefresh(
  userConfig: UserConfig,
  dependencies: UserConfigRuntimeDependencies,
): Promise<void> {
  if (dependencies.persistUserConfig) {
    dependencies.persistUserConfig(userConfig);
    await dependencies.refreshRuntimeSettings();
    return;
  }
  await dependencies.refreshRuntimeSettings(userConfig);
}
