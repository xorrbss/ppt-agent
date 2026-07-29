import { ipcMain } from "electron";
import fs from "fs";
import path from "path";

// Confine reads to the app's own data/temp roots — mirrors the web guard in
// servers/nextjs/app/api/read-file/route.ts. Without this, ANY renderer JS (a
// malicious custom-template compiled+run in the renderer, or any DOM-XSS) could
// call window.electron.readFile('C:\\...') to read arbitrary local files and
// exfiltrate them (this app relaxes webSecurity). realpath defeats symlink/`..`
// escapes; legit callers only ever read upload/decompose temp paths.
function allowedBaseDirs(): string[] {
  return [
    process.env.APP_DATA_DIRECTORY || "/app/user_data",
    process.env.TEMP_DIRECTORY || "/tmp",
    "/app/user_data",
  ];
}

export function setupReadFile() {
  ipcMain.handle("read-file", async (_, filePath: string) => {
    try {
      if (!filePath || typeof filePath !== "string") {
        throw new Error("read-file: filePath is required");
      }
      const normalizedPath = path.normalize(filePath.replace(/\0/g, ""));
      const resolvedPath = fs.realpathSync(path.resolve(normalizedPath));
      const isAllowed = allowedBaseDirs().some((baseDir) => {
        let resolvedBase: string;
        try {
          resolvedBase = fs.realpathSync(path.resolve(baseDir));
        } catch {
          return false;
        }
        return (
          resolvedPath === resolvedBase ||
          resolvedPath.startsWith(resolvedBase + path.sep)
        );
      });
      if (!isAllowed) {
        throw new Error("read-file: path is outside the allowed directories");
      }
      const content = fs.readFileSync(resolvedPath, "utf-8");
      return { content };
    } catch (error) {
      console.error("Error reading file:", error);
      throw error;
    }
  });
}
