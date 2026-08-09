import crypto from "crypto";
import { requestRuntimeSettingsRefresh } from "../utils/runtime-settings-refresh";

type UserConfigRuntimeDependencies = {
  persistUserConfig: (userConfig: UserConfig) => void;
  refreshRuntimeSettings: () => Promise<void>;
};

type UserConfigHandlerSetup = (
  refreshRuntimeSettings: () => Promise<void>,
) => void;

export function initializeUserConfigRuntime(
  fastApiPort: number,
  setupHandlers: UserConfigHandlerSetup,
): FastApiEnv {
  const refreshToken = crypto.randomBytes(32).toString("base64url");
  setupHandlers(() => requestRuntimeSettingsRefresh(fastApiPort, refreshToken));
  return { PRESENTON_RUNTIME_SETTINGS_REFRESH_TOKEN: refreshToken };
}

export async function saveUserConfigAndRefresh(
  userConfig: UserConfig,
  dependencies: UserConfigRuntimeDependencies,
): Promise<void> {
  dependencies.persistUserConfig(userConfig);
  await dependencies.refreshRuntimeSettings();
}
