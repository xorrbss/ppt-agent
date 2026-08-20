import { ipcMain } from "electron";
import fs from "fs";
import path from "path";
import { getUserConfig } from "../utils";
import { nextjsDir } from "../utils/constants";

export function setupApiHandlers() {
  // Handler for can-change-keys API
  ipcMain.handle("api:can-change-keys", async () => {
    const canChangeKeys = process.env.CAN_CHANGE_KEYS !== "false";
    return { canChange: canChangeKeys };
  });

  // Handler for has-required-key API
  ipcMain.handle("api:has-required-key", async () => {
    let keyFromFile = "";
    try {
      const cfg = getUserConfig();
      keyFromFile = cfg?.OPENAI_API_KEY || "";
    } catch {
      // Silent error handling
    }

    const keyFromEnv = process.env.OPENAI_API_KEY || "";
    const hasKey = Boolean((keyFromFile || keyFromEnv).trim());

    return { hasKey };
  });

  // Reads persisted user config so runtime toggles from the settings page
  // are picked up immediately without requiring an app restart.
  ipcMain.handle("api:telemetry-status", async () => {
    const cfg = getUserConfig();
    const fromConfig = cfg.DISABLE_ANONYMOUS_TRACKING;
    const fromEnv = process.env.DISABLE_ANONYMOUS_TRACKING;
    const raw = fromConfig ?? fromEnv ?? "";
    const isDisabled = raw === "true" || raw === "True";
    return { telemetryEnabled: !isDisabled };
  });

  // Handler for templates API (static list)
  ipcMain.handle("api:templates", async () => {
    try {
      const templatesPath = path.join(nextjsDir, "presentation-templates");

      if (!fs.existsSync(templatesPath)) {
        return [];
      }

      const items = fs.readdirSync(templatesPath, { withFileTypes: true });
      const templateDirectories = items
        .filter(item => item.isDirectory())
        .map(dir => dir.name);

      const allLayouts: Array<{ templateName: string; templateID: string; files: string[]; settings: any }> = [];

      // Scan each template directory for layout files and settings
      for (const templateName of templateDirectories) {
        try {
          const templatePath = path.join(templatesPath, templateName);
          const templateFiles = fs.readdirSync(templatePath);

          // Filter for .tsx files and exclude any non-layout files
          const layoutFiles = templateFiles.filter(file =>
            file.endsWith('.tsx') &&
            !file.startsWith('.') &&
            !file.includes('.test.') &&
            !file.includes('.spec.') &&
            file !== 'settings.json'
          );

          // Read settings.json if it exists
          let settings: any = null;
          const settingsPath = path.join(templatePath, 'settings.json');
          try {
            const settingsContent = fs.readFileSync(settingsPath, 'utf-8');
            settings = JSON.parse(settingsContent);
          } catch (settingsError) {
            console.warn(`No settings.json found for template ${templateName} or invalid JSON`);
            // Provide default settings if settings.json is missing or invalid
            settings = {
              description: `${templateName} presentation layouts`,
              ordered: false,
              default: false
            };
          }

          if (layoutFiles.length > 0) {
            allLayouts.push({
              templateName: templateName,
              templateID: templateName,
              files: layoutFiles,
              settings: settings
            });
          }
        } catch (error) {
          console.error(`Error reading template directory ${templateName}:`, error);
          // Continue with other templates even if one fails
        }
      }

      return allLayouts;
    } catch (error) {
      console.error("Error reading templates:", error);
      return [];
    }
  });
}
