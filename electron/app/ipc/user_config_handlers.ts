import { ipcMain } from "electron";
import { getUserConfig, setUserConfigAndMarkRuntimeRefresh } from "../utils";
import { saveUserConfigAndRefresh } from "./user_config_runtime";

export function setupUserConfigHandlers(
  refreshRuntimeSettings: () => Promise<void>,
) {
  ipcMain.handle("get-user-config", async (_, __) => {
    return getUserConfig();
  });

  ipcMain.handle("set-user-config", async (_, userConfig: UserConfig) => {
    await saveUserConfigAndRefresh(userConfig, {
      persistUserConfig: setUserConfigAndMarkRuntimeRefresh,
      refreshRuntimeSettings,
    });
  });

  ipcMain.handle("get-can-change-keys", async (_, __) => {
    return process.env.CAN_CHANGE_KEYS !== "false";
  });
}
