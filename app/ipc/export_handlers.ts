import { ipcMain, shell, dialog } from "electron";
import { downloadsDir } from "../constants";
import fs from "fs";
import path from "path";

export function setupExportHandlers() {
  ipcMain.handle("file-downloaded", async (_, filePath: string): Promise<IPCStatus> => {
    try {
      const fileName = path.basename(filePath);
      const destinationPath = path.join(downloadsDir, fileName);

      await fs.promises.rename(filePath, destinationPath);

      const { response } = await dialog.showMessageBox({
        type: 'question',
        buttons: ['Open File', 'Open Folder', 'Cancel'],
        defaultId: 0,
        title: 'File Downloaded',
        message: 'What would you like to do?'
      });

      if (response === 0) {
        await shell.openPath(destinationPath);
      } else if (response === 1) {
        await shell.openPath(downloadsDir);
      }

      return { success: true };
    } catch (error: any) {
      console.error('Error handling downloaded file:', error);
      return { success: false };
    }
  });
}