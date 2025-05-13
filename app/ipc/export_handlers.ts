import { ipcMain, } from "electron";
import { baseDir, downloadsDir, isDev } from "../utils/constants";
import fs from "fs";
import path from "path";
import puppeteer from "puppeteer";
import { showFileDownloadedDialog } from "../utils/dialog";

export function setupExportHandlers() {
  ipcMain.handle("file-downloaded", async (_, filePath: string): Promise<IPCStatus> => {
    const fileName = path.basename(filePath);
    const destinationPath = path.join(downloadsDir, fileName);

    await fs.promises.rename(filePath, destinationPath);
    const success = await showFileDownloadedDialog(destinationPath);
    return { success };
  });

  ipcMain.handle("export-as-pdf", async (_, id: string, title: string) => {
    const ppt_url = `${process.env.NEXT_PUBLIC_FAST_API}/presentation/${id}`;

    const browser = await puppeteer.launch({
      headless: true,
      executablePath: isDev ? undefined : path.join(baseDir, "dependencies/chrome-headless-shell/linux_build/chrome-headless-shell"),
      args: ['--no-sandbox', '--disable-setuid-sandbox']
    });

    const page = await browser.newPage();
    await page.setViewport({ width: 1280, height: 720 });

    await page.goto(ppt_url, { waitUntil: "networkidle0", timeout: 30000 });

    const pdfBuffer = await page.pdf({
      height: 720,
    });

    const destinationPath = path.join(downloadsDir, `${title}.pdf`);
    await fs.promises.writeFile(destinationPath, pdfBuffer);

    const success = await showFileDownloadedDialog(destinationPath);

    return { success };
  })

}