import { ipcMain } from "electron";
import { baseDir, getAppDataDir, getDownloadsDir, getTempDir } from "../utils/constants";
import fs from "fs";
import path from "path";

import { showFileDownloadedDialog } from "../utils/dialog";
import { v4 as uuidv4 } from 'uuid';
import { spawn } from "child_process";
import { getPuppeteerExecutablePath } from "../utils/puppeteer-check";
import { safeError, safeLog } from "../utils/safe-console";
import { addMainBreadcrumb } from "../sentry/main";
import { BoundedTextBuffer, envInt, memorySnapshotMb, withNodeHeapLimit } from "../utils/memory";

type BinaryFormat = "elf" | "mach-o" | "pe" | "unknown";
type RuntimeCandidate = {
  command: string;
  label: string;
  useElectronRunAsNode?: boolean;
};

let activeExports = 0;
const pendingExports: Array<() => void> = [];

async function acquireExportSlot(): Promise<() => void> {
  const limit = envInt("PRESENTON_EXPORT_CONCURRENCY", 1, 1, 8);
  if (activeExports >= limit) {
    await new Promise<void>((resolve) => pendingExports.push(resolve));
  }
  activeExports += 1;
  return () => {
    activeExports = Math.max(0, activeExports - 1);
    pendingExports.shift()?.();
  };
}

async function enqueueExport<T>(work: () => Promise<T>): Promise<T> {
  const release = await acquireExportSlot();
  try {
    return await work();
  } finally {
    release();
  }
}

export function setupExportHandlers() {
  ipcMain.handle("file-downloaded", async (_, filePath: string): Promise<IPCStatus> => {
    const fileName = path.basename(filePath);
    const destinationPath = path.join(getDownloadsDir(), fileName);

    await fs.promises.rename(filePath, destinationPath);
    const success = await showFileDownloadedDialog(destinationPath);
    return { success };
  });

  ipcMain.handle("export-presentation", async (_, id: string, title: string, exportAs: "pptx" | "pdf") => {
    return enqueueExport(async () => {
      let exportTempDir: string | undefined;
      try {
        addMainBreadcrumb("export", "electron.ipc_export.start", {
          id,
          title,
          exportAs,
          memory: memorySnapshotMb(),
        });
        const params = new URLSearchParams({ id });
        if (process.env.NEXT_PUBLIC_FAST_API) {
          params.set("fastapiUrl", process.env.NEXT_PUBLIC_FAST_API);
        }
        const pptUrl = `${process.env.NEXT_PUBLIC_URL}/pdf-maker?${params.toString()}`;

        const exportTask = {
          type: "export",
          url: pptUrl,
          format: exportAs,
          title: title,
          fastapiUrl: process.env.NEXT_PUBLIC_FAST_API,
        };

        const randomUuid = uuidv4();
        const tempDir = getTempDir();
        const appDataDir = getAppDataDir();
        exportTempDir = path.join(tempDir, randomUuid);
        await fs.promises.mkdir(exportTempDir, { recursive: true });

        const exportTaskPath = path.join(exportTempDir, "export_task.json");
        await fs.promises.writeFile(exportTaskPath, JSON.stringify(exportTask));

        const exportScriptPath = path.join(baseDir, "resources", "export", "index.js");
        const pythonModulePath = await resolveConverterPath(baseDir);
        const puppeteerExecutablePath = await getPuppeteerExecutablePath();
        safeLog("[Export] Spawning export task with config:", {
          exportAs,
          id,
          title,
          pptUrl,
          exportTaskPath,
          exportScriptPath,
          pythonModulePath,
          puppeteerExecutablePath,
          NEXT_PUBLIC_URL: process.env.NEXT_PUBLIC_URL,
          NEXT_PUBLIC_FAST_API: process.env.NEXT_PUBLIC_FAST_API,
        });
        const baseExportEnv = withNodeHeapLimit({
          ...process.env,
          TEMP_DIRECTORY: tempDir,
          APP_DATA_DIRECTORY: appDataDir,
          NODE_ENV: "development",
          BUILT_PYTHON_MODULE_PATH: pythonModulePath,
          ...(puppeteerExecutablePath && {
            PUPPETEER_EXECUTABLE_PATH: puppeteerExecutablePath,
          }),
        }, "PRESENTON_EXPORT_NODE_MAX_OLD_SPACE_MB", 1536);
        await runExportTaskWithRuntimeFallback(
          exportScriptPath,
          exportTaskPath,
          baseExportEnv
        );

        const responsePath = exportTaskPath.replace(".json", ".response.json");
        const responseRaw = await fs.promises.readFile(responsePath, "utf8");
        const responseData = JSON.parse(responseRaw);
        const exportFilePath = resolveExportedFilePath(responseData);

        if (!exportFilePath) {
          return { success: false, message: "Export finished but output file was not found." };
        }

        const destinationPath = path.join(getDownloadsDir(), path.basename(exportFilePath));
        await moveFile(exportFilePath, destinationPath);
        const success = await showFileDownloadedDialog(destinationPath);
        addMainBreadcrumb("export", "electron.ipc_export.finish", {
          id,
          exportAs,
          success,
          memory: memorySnapshotMb(),
        });
        return { success, message: success ? "Export completed." : "Export completed but dialog failed." };
      } catch (error: any) {
        safeError("[Export] Error exporting presentation:", error);
        addMainBreadcrumb("export", "electron.ipc_export.error", {
          id,
          exportAs,
          message: error?.message,
          memory: memorySnapshotMb(),
        });
        return { success: false, message: error?.message ?? "Export failed." };
      } finally {
        if (exportTempDir) {
          await fs.promises.rm(exportTempDir, { recursive: true, force: true }).catch(() => {});
        }
      }
    });
  });
}

function getExportRuntimeCandidates(): RuntimeCandidate[] {
  const candidates: RuntimeCandidate[] = [];
  const push = (command: string | undefined, label: string, useElectronRunAsNode?: boolean) => {
    if (!command) return;
    const trimmed = command.trim();
    if (!trimmed) return;
    candidates.push({ command: trimmed, label, useElectronRunAsNode });
  };

  // Explicit overrides first.
  push(process.env.EXPORT_NODE_BINARY, "EXPORT_NODE_BINARY");
  push(process.env.NODE_BINARY, "NODE_BINARY");

  // Match the older stable export approach: `spawn("node", ...)` first.
  push("node", "node");

  // Additional system node entries.
  if (process.platform === "win32") {
    push("node.exe", "node.exe");
    push("node.cmd", "node.cmd");
  } else {
    push("/usr/bin/node", "/usr/bin/node");
    push("/usr/local/bin/node", "/usr/local/bin/node");
  }

  // Fallback: Electron runtime in Node mode.
  push(process.execPath, "process.execPath (electron-as-node)", true);

  const deduped: RuntimeCandidate[] = [];
  const seen = new Set<string>();
  for (const candidate of candidates) {
    const key = `${candidate.command}|${candidate.useElectronRunAsNode ? "electron-node" : "plain"}`;
    if (seen.has(key)) continue;
    seen.add(key);
    deduped.push(candidate);
  }
  return deduped;
}

function isRetryableRuntimeError(error: any): boolean {
  const code = String(error?.code || "");
  // Try another runtime when binary is missing or cannot be spawned.
  return ["ENOENT", "EACCES", "EPERM", "EAGAIN"].includes(code);
}

async function runExportTaskWithRuntimeFallback(
  exportScriptPath: string,
  exportTaskPath: string,
  baseEnv: NodeJS.ProcessEnv
): Promise<void> {
  const runtimeCandidates = getExportRuntimeCandidates();
  const failures: string[] = [];

  for (const runtime of runtimeCandidates) {
    try {
      safeLog(`[Export] Trying runtime: ${runtime.label} -> ${runtime.command}`);
      await runExportTaskOnce(
        runtime,
        exportScriptPath,
        exportTaskPath,
        baseEnv
      );
      return;
    } catch (error: any) {
      const details = [
        `${runtime.label}: ${error?.message || "Unknown error"}`,
        error?.code ? `code=${error.code}` : "",
      ]
        .filter(Boolean)
        .join(" ");
      failures.push(details);
      safeError(`[Export] Runtime failed (${runtime.label})`, error);

      if (!isRetryableRuntimeError(error)) {
        throw error;
      }
    }
  }

  throw new Error(
    `Export failed to start with all runtimes.\n${failures.map((f) => `- ${f}`).join("\n")}`
  );
}

async function runExportTaskOnce(
  runtime: RuntimeCandidate,
  exportScriptPath: string,
  exportTaskPath: string,
  baseEnv: NodeJS.ProcessEnv
): Promise<void> {
  const runtimeEnv = {
    ...baseEnv,
    ...(runtime.useElectronRunAsNode ? { ELECTRON_RUN_AS_NODE: "1" } : {}),
  };

  const exportTaskProcess = spawn(runtime.command, [exportScriptPath, exportTaskPath], {
    stdio: ["ignore", "pipe", "pipe"],
    cwd: baseDir,
    windowsHide: process.platform === "win32",
    env: runtimeEnv,
  });

  safeLog("[Export] Child process started:", {
    runtime: runtime.label,
    pid: exportTaskProcess.pid,
    memory: memorySnapshotMb(),
  });
  addMainBreadcrumb("export", "electron.export_child.start", {
    runtime: runtime.label,
    pid: exportTaskProcess.pid,
    memory: memorySnapshotMb(),
  });

  const stdoutTail = new BoundedTextBuffer();
  const stderrTail = new BoundedTextBuffer();

  exportTaskProcess.stdout.on("data", (data: Buffer) => {
    const text = data.toString();
    stdoutTail.append(text);
    safeLog(`[Export] ${text}`);
  });
  exportTaskProcess.stderr.on("data", (data: Buffer) => {
    const text = data.toString();
    stderrTail.append(text);
    safeError(`[Export] ${text}`);
  });

  await new Promise<void>((resolve, reject) => {
    exportTaskProcess.on("error", reject);
    exportTaskProcess.on("exit", (code) => {
      safeLog("[Export] Child process exited:", {
        runtime: runtime.label,
        pid: exportTaskProcess.pid,
        code,
        memory: memorySnapshotMb(),
      });
      addMainBreadcrumb("export", "electron.export_child.exit", {
        runtime: runtime.label,
        pid: exportTaskProcess.pid,
        code,
        memory: memorySnapshotMb(),
      });
      if (code === 0) {
        resolve();
      } else {
        const stderrText = stderrTail.toString() || "(no stderr)";
        const stdoutText = stdoutTail.toString();
        const detail =
          stderrText !== "(no stderr)"
            ? stderrText
            : stdoutText
              ? `stdout: ${stdoutText}`
              : "";
        const error: NodeJS.ErrnoException = new Error(
          `Export process exited with code ${code}${detail ? `. ${detail}` : ""}`
        );
        error.code = `EXIT_${code ?? "UNKNOWN"}`;
        reject(error);
      }
    });
  });
}

async function resolveConverterPath(currentBaseDir: string): Promise<string> {
  const pyDir = path.join(currentBaseDir, "resources", "export", "py");
  const extension = process.platform === "win32" ? ".exe" : "";
  const converterCandidates = [
    path.join(pyDir, `convert-${process.platform}-${process.arch}${extension}`),
    path.join(pyDir, `convert-${process.platform}${extension}`),
    ...(process.platform === "win32"
      ? [path.join(pyDir, "convert.exe"), path.join(pyDir, "convert")]
      : [path.join(pyDir, "convert")]),
  ];

  const converterPath = await findFirstExistingPath(converterCandidates);
  if (!converterPath) {
    throw new Error(
      [
        "No converter binary found for export.",
        "Expected one of:",
        ...converterCandidates.map((candidate) => `  - ${candidate}`),
      ].join("\n")
    );
  }

  const format = await detectBinaryFormat(converterPath);
  if (!isBinaryFormatCompatible(format)) {
    throw new Error(
      [
        `Converter binary is not valid for ${process.platform}/${process.arch}.`,
        `Selected converter: ${converterPath}`,
        `Detected format: ${format}`,
        "Please bundle a platform-correct converter binary (for example convert-darwin-arm64 or convert-darwin-x64).",
      ].join("\n")
    );
  }

  return converterPath;
}

async function findFirstExistingPath(paths: string[]): Promise<string | null> {
  for (const candidate of paths) {
    try {
      await fs.promises.access(candidate, fs.constants.F_OK);
      return candidate;
    } catch {
      continue;
    }
  }
  return null;
}

async function detectBinaryFormat(binaryPath: string): Promise<BinaryFormat> {
  const fd = await fs.promises.open(binaryPath, "r");
  try {
    const header = Buffer.alloc(4);
    await fd.read(header, 0, 4, 0);

    if (header[0] === 0x7f && header[1] === 0x45 && header[2] === 0x4c && header[3] === 0x46) {
      return "elf";
    }

    if (header[0] === 0x4d && header[1] === 0x5a) {
      return "pe";
    }

    const magic = header.readUInt32BE(0);
    if (
      magic === 0xfeedface ||
      magic === 0xcefaedfe ||
      magic === 0xfeedfacf ||
      magic === 0xcffaedfe ||
      magic === 0xcafebabe ||
      magic === 0xbebafeca
    ) {
      return "mach-o";
    }

    return "unknown";
  } finally {
    await fd.close();
  }
}

function isBinaryFormatCompatible(format: BinaryFormat): boolean {
  if (process.platform === "darwin") return format === "mach-o";
  if (process.platform === "linux") return format === "elf";
  if (process.platform === "win32") return format === "pe";
  return true;
}

function resolveExportedFilePath(responseData: any): string | null {
  if (responseData?.path && typeof responseData.path === "string") {
    return path.isAbsolute(responseData.path)
      ? responseData.path
      : path.join(getAppDataDir(), responseData.path);
  }

  if (responseData?.url && typeof responseData.url === "string") {
    try {
      const parsed = new URL(responseData.url);
      if (parsed.protocol === "file:") {
        const filePath = decodeURIComponent(parsed.pathname);
        if (process.platform === "win32" && filePath.startsWith("/")) {
          return filePath.slice(1);
        }
        return filePath;
      }
    } catch {
      return null;
    }
  }

  return null;
}

async function moveFile(sourcePath: string, destinationPath: string) {
  try {
    await fs.promises.rename(sourcePath, destinationPath);
  } catch (error: any) {
    if (error?.code !== "EXDEV") {
      throw error;
    }
    await fs.promises.copyFile(sourcePath, destinationPath);
    await fs.promises.unlink(sourcePath);
  }
}
