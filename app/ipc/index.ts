import { setupExportHandlers } from "./export_handlers";
import { setupUserConfigHandlers } from "./user_config_handlers";

export function setupIpcHandlers() {
  setupExportHandlers();
  setupUserConfigHandlers();
}