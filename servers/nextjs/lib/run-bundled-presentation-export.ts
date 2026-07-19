import path from "path";
import os from "os";
import fs from "fs/promises";
import { fileURLToPath } from "url";
import { spawn } from "child_process";
import { sanitizeFilename } from "@/app/(presentation-generator)/utils/others";
import {
  BoundedTextBuffer,
  memorySnapshotMb,
} from "@/lib/runtime-limits";

/** Repo `presentation-export/` at app root (`/app/presentation-export` in Docker). */
export function getExportPackageRoot(): string {
  return (
    process.env.EXPORT_PACKAGE_ROOT?.trim() ||
    path.join(process.cwd(), "..", "..", "presentation-export")
  );
}

export function getPresentonAppRoot(): string {
  return (
    process.env.PRESENTON_APP_ROOT?.trim() ||
    path.join(process.cwd(), "..", "..")
  );
}

function extractSessionTokenFromCookieHeader(cookieHeader?: string): string | undefined {
  if (!cookieHeader) {
    return undefined;
  }

  const match = cookieHeader.match(/(?:^|;\s*)presenton_session=([^;]+)/);
  if (!match?.[1]) {
    return undefined;
  }

  return decodeURIComponent(match[1]);
}

async function resolveExportEntrypoint(exportRoot: string): Promise<string> {
  const indexCjs = path.join(exportRoot, "index.cjs");
  const indexJs = path.join(exportRoot, "index.js");

  try {
    await fs.access(indexCjs);
    return indexCjs;
  } catch {
    await fs.access(indexJs);
    await fs.copyFile(indexJs, indexCjs);
    return indexCjs;
  }
}

function bundledConverterPath(exportRoot: string): string {
  const fromEnv = process.env.BUILT_PYTHON_MODULE_PATH?.trim();
  if (fromEnv) {
    return fromEnv;
  }
  // The sync script fetches a per-OS/arch PyInstaller binary named
  // convert-<platform>-<arch>[.exe] (e.g. convert-linux-x64,
  // convert-win32-x64.exe). Resolve by that convention for every platform;
  // callers gate on fs.access, so a missing binary surfaces as "not available".
  const ext = process.platform === "win32" ? ".exe" : "";
  return path.join(
    exportRoot,
    "py",
    `convert-${process.platform}-${process.arch}${ext}`
  );
}

/**
 * Path to a Chrome/Chromium the bundled export (puppeteer) can launch. Honors
 * PUPPETEER_EXECUTABLE_PATH / CHROME_PATH, then probes common system locations —
 * so a native (non-Docker) run doesn't fall back to puppeteer's own download
 * (which is absent/broken on most desktops). Returns undefined if none found
 * (puppeteer then uses its default resolution).
 */
async function resolveChromeExecutable(): Promise<string | undefined> {
  const fromEnv =
    process.env.PUPPETEER_EXECUTABLE_PATH?.trim() ||
    process.env.CHROME_PATH?.trim();
  if (fromEnv) {
    return fromEnv;
  }

  const candidates: string[] = [];
  if (process.platform === "win32") {
    const pf = process.env["ProgramFiles"] || "C:\\Program Files";
    const pfx86 =
      process.env["ProgramFiles(x86)"] || "C:\\Program Files (x86)";
    const local = process.env["LOCALAPPDATA"];
    candidates.push(
      path.join(pf, "Google/Chrome/Application/chrome.exe"),
      path.join(pfx86, "Google/Chrome/Application/chrome.exe")
    );
    if (local) {
      candidates.push(path.join(local, "Google/Chrome/Application/chrome.exe"));
    }
    candidates.push(
      path.join(pfx86, "Microsoft/Edge/Application/msedge.exe"),
      path.join(pf, "Microsoft/Edge/Application/msedge.exe")
    );
  } else if (process.platform === "darwin") {
    candidates.push(
      "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
      "/Applications/Chromium.app/Contents/MacOS/Chromium",
      "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge"
    );
  } else {
    candidates.push(
      "/usr/bin/google-chrome",
      "/usr/bin/google-chrome-stable",
      "/usr/bin/chromium",
      "/usr/bin/chromium-browser"
    );
  }

  for (const candidate of candidates) {
    try {
      await fs.access(candidate);
      return candidate;
    } catch {
      // keep probing
    }
  }
  return undefined;
}

export async function bundledExportPackageAvailable(): Promise<boolean> {
  try {
    const root = getExportPackageRoot();
    await resolveExportEntrypoint(root);
    await fs.access(bundledConverterPath(root));
    return true;
  } catch {
    return false;
  }
}

export type BundledPresentationExportFormat = "pdf" | "pptx";

export type BundledPresentationExportResult = { path: string };

const EXPORT_DIRECTORY_MODE = 0o755;
const EXPORT_FILE_MODE = 0o644;

function normalizeExportOutputPath(params: {
  pathValue?: string;
  urlValue?: string;
}): string {
  const { pathValue, urlValue } = params;
  const appData = process.env.APP_DATA_DIRECTORY?.trim();

  const resolveAppDataRelative = (value: string): string => {
    if (!appData) {
      throw new Error("APP_DATA_DIRECTORY is required for relative export paths.");
    }

    const normalized = value.startsWith("/") ? value.slice(1) : value;
    if (!normalized.startsWith("app_data/")) {
      return path.join(appData, normalized);
    }
    return path.join(appData, normalized.slice("app_data/".length));
  };

  if (pathValue && typeof pathValue === "string") {
    if (path.isAbsolute(pathValue)) {
      return pathValue;
    }
    return resolveAppDataRelative(pathValue);
  }

  if (urlValue && typeof urlValue === "string") {
    if (urlValue.startsWith("file://")) {
      // fileURLToPath handles Windows drive letters ("file:///C:/x" -> "C:\x").
      // Parsing .pathname manually yields "/C:/x", which fs then resolves
      // against the current drive as the bogus "C:\C:\x".
      const fsPath = fileURLToPath(urlValue);
      // Docker serves exports from /app_data; remap onto APP_DATA_DIRECTORY.
      const posix = fsPath.replace(/\\/g, "/");
      if (posix.startsWith("/app_data/")) {
        return resolveAppDataRelative(posix);
      }
      return fsPath;
    }

    if (urlValue.startsWith("/app_data/")) {
      return resolveAppDataRelative(urlValue);
    }
  }

  throw new Error("Export finished but response did not include a valid output path.");
}

async function ensureExportFileReadable(filePath: string): Promise<void> {
  const stats = await fs.stat(filePath);
  if (!stats.isFile()) {
    throw new Error("Export finished but output path is not a file.");
  }

  await fs.chmod(path.dirname(filePath), EXPORT_DIRECTORY_MODE);
  await fs.chmod(filePath, EXPORT_FILE_MODE);
}

/**
 * Runs the bundled export entrypoint (`presentation-export/index.js`) with
 * `BUILT_PYTHON_MODULE_PATH` pointing at the PyInstaller converter binary.
 */
export async function runBundledPresentationExport(params: {
  presentationId: string;
  title: string | undefined;
  format: BundledPresentationExportFormat;
  cookieHeader?: string;
}): Promise<BundledPresentationExportResult> {
  return runBundledPresentationExportLocked(params);
}

async function runBundledPresentationExportLocked(params: {
  presentationId: string;
  title: string | undefined;
  format: BundledPresentationExportFormat;
  cookieHeader?: string;
}): Promise<BundledPresentationExportResult> {
  const { presentationId, title, format, cookieHeader } = params;
  const exportRoot = getExportPackageRoot();
  const entrypoint = await resolveExportEntrypoint(exportRoot);
  const converter = bundledConverterPath(exportRoot);
  const appRoot = getPresentonAppRoot();

  await fs.access(converter);

  const nextjsUrl =
    process.env.NEXT_PUBLIC_URL?.trim() || "http://127.0.0.1";
  const q = new URLSearchParams({ id: presentationId });
  const sessionToken = extractSessionTokenFromCookieHeader(cookieHeader);
  if (sessionToken) {
    q.set("exportSession", sessionToken);
  }
  const fastapiUrl = process.env.NEXT_PUBLIC_FAST_API?.trim();
  if (fastapiUrl) {
    q.set("fastapiUrl", fastapiUrl);
  }
  const basePptUrl = `${nextjsUrl}/pdf-maker?${q.toString()}`;
  const pptUrl = cookieHeader?.trim()
    ? `${basePptUrl}#exportCookie=${encodeURIComponent(cookieHeader)}`
    : basePptUrl;

  const tempBase =
    process.env.TEMP_DIRECTORY?.trim() || path.join(os.tmpdir(), "presenton");
  await fs.mkdir(tempBase, { recursive: true });
  const workDir = await fs.mkdtemp(path.join(tempBase, "export-"));
  const exportTaskPath = path.join(workDir, "export_task.json");

  const exportTask = {
    type: "export",
    url: pptUrl,
    format,
    title: sanitizeFilename(title ?? "presentation"),
    fastapiUrl: fastapiUrl || undefined,
    cookieHeader: cookieHeader || undefined,
  };

  try {
    await fs.writeFile(exportTaskPath, JSON.stringify(exportTask), "utf8");

    const responsePath = exportTaskPath.replace(/\.json$/i, ".response.json");

    console.info("[bundled-export] start", {
      presentationId,
      format,
      memory: memorySnapshotMb(),
    });
    const chromeExecutable = await resolveChromeExecutable();
    await new Promise<void>((resolve, reject) => {
      const child = spawn(process.execPath, [entrypoint, exportTaskPath], {
        cwd: appRoot,
        stdio: ["ignore", "pipe", "pipe"],
        env: {
          ...process.env,
          BUILT_PYTHON_MODULE_PATH: converter,
          // The entrypoint requires TEMP_DIRECTORY; forward the base we already
          // resolved so it doesn't error with "TEMP_DIRECTORY must be set".
          TEMP_DIRECTORY: tempBase,
          // Point puppeteer at a real Chrome so it doesn't try to download one.
          ...(chromeExecutable
            ? { PUPPETEER_EXECUTABLE_PATH: chromeExecutable }
            : {}),
        },
      });
      const stderr = new BoundedTextBuffer();
      const stdout = new BoundedTextBuffer();
      const onStderrData = (d: Buffer) => stderr.append(d);
      const onStdoutData = (d: Buffer) => stdout.append(d);
      let settled = false;
      const cleanup = () => {
        child.stderr?.removeListener("data", onStderrData);
        child.stdout?.removeListener("data", onStdoutData);
        child.removeListener("error", onError);
        child.removeListener("close", onClose);
      };
      const finish = (callback: () => void) => {
        if (settled) return;
        settled = true;
        cleanup();
        callback();
      };
      const onError = (error: Error) => finish(() => reject(error));
      const onClose = (code: number | null, signal: NodeJS.Signals | null) => {
        console.info("[bundled-export] child exit", {
          presentationId,
          format,
          pid: child.pid,
          code,
          signal,
          memory: memorySnapshotMb(),
        });
        if (code === 0) {
          finish(resolve);
        } else {
          const errText = stderr.toString();
          const outText = stdout.toString();
          finish(() => {
            reject(
              new Error(
                `Export process exited with code ${code ?? "unknown"}${signal ? ` signal ${signal}` : ""}${errText ? `. ${errText}` : ""}${outText ? ` stdout: ${outText}` : ""}`
              )
            );
          });
        }
      };
      child.stderr?.on("data", onStderrData);
      child.stdout?.on("data", onStdoutData);
      child.once("error", onError);
      child.once("close", onClose);
    });

    const responseRaw = await fs.readFile(responsePath, "utf8");
    const responseData = JSON.parse(responseRaw) as { path?: string; url?: string };

    const outPath = normalizeExportOutputPath({
      pathValue: responseData?.path,
      urlValue: responseData?.url,
    });

    await ensureExportFileReadable(outPath);
    console.info("[bundled-export] finish", {
      presentationId,
      format,
      memory: memorySnapshotMb(),
    });

    return { path: outPath };
  } finally {
    await fs.rm(workDir, { recursive: true, force: true }).catch(() => {});
  }
}
