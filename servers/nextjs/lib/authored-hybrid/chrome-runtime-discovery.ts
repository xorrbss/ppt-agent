import type { Dirent } from "node:fs";
import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";

interface PlaywrightHeadlessShell {
  executable: string;
  revision: number;
}

async function accessible(filePath: string): Promise<boolean> {
  try {
    await fs.access(/* turbopackIgnore: true */ filePath);
    return true;
  } catch {
    return false;
  }
}

function expandHome(filePath: string): string {
  if (filePath === "~") return os.homedir();
  if (filePath.startsWith("~/") || filePath.startsWith("~\\")) {
    return path.join(os.homedir(), filePath.slice(2));
  }
  return filePath;
}

/**
 * Probe browser installations only when an authored export runs. Keeping this
 * filesystem discovery separate from the Chrome process runner prevents Next's
 * file tracer from treating Playwright browser caches as application assets.
 */
async function playwrightHeadlessShellCandidates(): Promise<string[]> {
  const roots: string[] = [];
  const configuredRoot = process.env.PLAYWRIGHT_BROWSERS_PATH?.trim();
  if (configuredRoot && configuredRoot !== "0") {
    roots.push(expandHome(configuredRoot));
  }

  const localAppData = process.env.LOCALAPPDATA?.trim();
  if (localAppData) roots.push(path.join(localAppData, "ms-playwright"));
  roots.push(
    path.join(os.homedir(), ".cache", "ms-playwright"),
    path.join(os.homedir(), "Library", "Caches", "ms-playwright")
  );

  const seenRoots = new Set<string>();
  const found: PlaywrightHeadlessShell[] = [];
  for (const root of roots) {
    const resolvedRoot = path.resolve(/*turbopackIgnore: true*/ root);
    const rootKey =
      process.platform === "win32" ? resolvedRoot.toLowerCase() : resolvedRoot;
    if (seenRoots.has(rootKey)) continue;
    seenRoots.add(rootKey);

    let revisions: Dirent<string>[];
    try {
      revisions = await fs.readdir(
        /* turbopackIgnore: true */ resolvedRoot,
        { withFileTypes: true }
      );
    } catch {
      continue;
    }

    for (const revisionDirectory of revisions) {
      const match = /^chromium_headless_shell-(\d+)$/.exec(
        revisionDirectory.name
      );
      if (!revisionDirectory.isDirectory() || !match) continue;

      const revisionPath = path.join(
        /*turbopackIgnore: true*/ resolvedRoot,
        revisionDirectory.name
      );
      let platformDirectories: Dirent<string>[];
      try {
        platformDirectories = await fs.readdir(
          /* turbopackIgnore: true */ revisionPath,
          { withFileTypes: true }
        );
      } catch {
        continue;
      }

      for (const platformDirectory of platformDirectories) {
        if (!platformDirectory.isDirectory()) continue;
        for (const executableName of [
          "chrome-headless-shell.exe",
          "chrome-headless-shell",
        ]) {
          const executable = path.join(
            /*turbopackIgnore: true*/ revisionPath,
            platformDirectory.name,
            executableName
          );
          if (await accessible(executable)) {
            found.push({ executable, revision: Number(match[1]) });
          }
        }
      }
    }
  }

  return found
    .sort(
      (left, right) =>
        right.revision - left.revision ||
        left.executable.localeCompare(right.executable)
    )
    .map(({ executable }) => executable);
}

/**
 * First-party Chrome resolution, intentionally independent of generated export
 * code and limited to runtime environment/filesystem probes.
 */
export async function resolveAuthoredHybridChromeExecutable(): Promise<
  string | undefined
> {
  const fromEnv =
    process.env.AUTHORED_HYBRID_CHROME_PATH?.trim() ||
    process.env.PUPPETEER_EXECUTABLE_PATH?.trim() ||
    process.env.CHROME_PATH?.trim();
  if (fromEnv) return fromEnv;

  const headlessShells = await playwrightHeadlessShellCandidates();
  if (headlessShells.length) return headlessShells[0];

  const candidates: string[] = [];
  if (process.platform === "win32") {
    const programFiles = process.env.ProgramFiles || "C:\\Program Files";
    const programFilesX86 =
      process.env["ProgramFiles(x86)"] || "C:\\Program Files (x86)";
    const localAppData = process.env.LOCALAPPDATA;
    candidates.push(
      path.join(programFiles, "Google/Chrome/Application/chrome.exe"),
      path.join(programFilesX86, "Google/Chrome/Application/chrome.exe")
    );
    if (localAppData) {
      candidates.push(
        path.join(localAppData, "Google/Chrome/Application/chrome.exe")
      );
    }
    candidates.push(
      path.join(programFilesX86, "Microsoft/Edge/Application/msedge.exe"),
      path.join(programFiles, "Microsoft/Edge/Application/msedge.exe")
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
    if (await accessible(candidate)) return candidate;
  }
  return undefined;
}
