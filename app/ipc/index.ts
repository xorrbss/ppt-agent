import { setupExportHandlers } from "./export_handlers";
import { setupUserConfigHandlers } from "./user_config_handlers";
import { setupReadFile } from "./read_file";
export function setupIpcHandlers() {
  setupExportHandlers();
  setupUserConfigHandlers();
  setupReadFile();
}