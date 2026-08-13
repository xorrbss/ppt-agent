import { ipcMain } from "electron";
import { getUserConfig } from "../utils";
import { saveUserConfigAndRefresh } from "./user_config_runtime";

export function setupUserConfigHandlers(
  refreshRuntimeSettings: (userConfig?: UserConfig) => Promise<void>,
) {
  ipcMain.handle("get-user-config", async (_, __) => {
    return getUserConfig();
  });

  ipcMain.handle("set-user-config", async (_, userConfig: UserConfig) => {
    await saveUserConfigAndRefresh(userConfig, {
      refreshRuntimeSettings,
    });
  });

  ipcMain.handle("get-can-change-keys", async (_, __) => {
    return process.env.CAN_CHANGE_KEYS !== "false";
  });
}
