import { ipcMain, dialog } from "electron";
import puppeteer from "puppeteer";

export function setupTemplateHandlers() {
  // Handler for template API (with puppeteer)
  ipcMain.handle("api:template", async (event, { group }: { group: string }) => {
    if (!group) {
      throw new Error("Missing group name");
    }

    const schemaPageUrl = `http://localhost/schema?group=${encodeURIComponent(group)}`;

    let browser;
    try {
      browser = await puppeteer.launch({
        executablePath: process.env.PUPPETEER_EXECUTABLE_PATH,
        headless: true,
        args: [
          "--no-sandbox",
          "--disable-setuid-sandbox",
          "--disable-dev-shm-usage",
          "--disable-gpu",
          "--disable-web-security",
          "--disable-background-timer-throttling",
          "--disable-backgrounding-occluded-windows",
          "--disable-renderer-backgrounding",
          "--disable-features=TranslateUI",
          "--disable-ipc-flooding-protection",
        ],
      });
      
      const page = await browser.newPage();
      await page.setViewport({ width: 1280, height: 720 });
      page.setDefaultNavigationTimeout(300000);
      page.setDefaultTimeout(300000);
      
      await page.goto(schemaPageUrl, {
        waitUntil: "networkidle0",
        timeout: 300000,
      });

      await page.waitForSelector("[data-layouts]", { timeout: 300000 });
      await page.waitForSelector("[data-settings]", { timeout: 300000 });

      const { dataLayouts, dataGroupSettings } = await page.$eval(
        "[data-layouts]",
        (el) => ({
          dataLayouts: el.getAttribute("data-layouts"),
          dataGroupSettings: el.getAttribute("data-settings"),
        })
      );

      if (!dataLayouts || !dataGroupSettings) {
        throw new Error("Could not find layouts or settings data");
      }

      const layouts = JSON.parse(dataLayouts);
      const groupSettings = JSON.parse(dataGroupSettings);

      return {
        layouts,
        groupSettings,
      };
    } catch (error) {
      console.error("Error getting template:", error);
      throw new Error(`Failed to get template: ${error instanceof Error ? error.message : String(error)}`);
    } finally {
      if (browser) {
        await browser.close();
      }
    }
  });

  // Handler for presentation_to_pptx_model API (simplified version)
  ipcMain.handle("api:presentation-to-pptx-model", async (event, { id }: { id?: string }) => {
    // Note: This is a simplified version since the full implementation is quite complex
    // and involves puppeteer operations that might be better handled differently in Electron
    try {
      if (!id) {
        throw new Error("Missing presentation ID");
      }

      // For now, return a placeholder response or implement a simplified version
      // The full implementation would require significant adaptation for Electron context
      return {
        error: "This endpoint requires server-side implementation",
        message: "Use the FastAPI backend for presentation to PPTX conversion"
      };
    } catch (error) {
      console.error("Error in presentation-to-pptx-model:", error);
      throw new Error(`Failed to convert presentation: ${error instanceof Error ? error.message : String(error)}`);
    }
  });
}