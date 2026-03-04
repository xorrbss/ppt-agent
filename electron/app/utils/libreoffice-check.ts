/**
 * libreoffice-check.ts
 *
 * Checks whether LibreOffice is available on the host machine before the
 * main BrowserWindow is created.  If it is not found, an Electron dialog is
 * shown that lets the user download LibreOffice, skip the check, or quit.
 */

import { app, dialog, shell } from "electron";
import { exec } from "child_process";
import * as util from "util";
import * as fs from "fs";

const execAsync = util.promisify(exec);

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

/** Result returned by {@link isLibreOfficeInstalled}. */
interface LibreOfficeCheckResult {
  installed: boolean;
  /** The raw version string from `soffice --version`, when available. */
  version?: string;
  /** The resolved absolute path (or bare command name) of the soffice binary. */
  path?: string;
}

// ---------------------------------------------------------------------------
// Platform helpers
// ---------------------------------------------------------------------------

/**
 * Reads a directory and returns the names of all entries whose names match
 * `pattern`.  Returns an empty array if the directory cannot be read.
 */
function scanDir(dir: string, pattern: RegExp): string[] {
  try {
    return fs.readdirSync(dir).filter((entry) => pattern.test(entry));
  } catch {
    return [];
  }
}

/**
 * Returns an ordered list of absolute paths to try for the `soffice` binary
 * on the current platform.
 *
 * Instead of hard-coding version numbers, parent directories are scanned with
 * a regex so any past or future LibreOffice version is automatically found.
 * Fixed (non-versioned) paths are still included first so the common case
 * resolves instantly.
 *
 * Detection strategy per platform:
 *  Windows  – scan Program Files (64-bit & 32-bit) for /^LibreOffice(\s[\d.]+)?$/i,
 *             plus per-user LOCALAPPDATA / APPDATA locations.
 *  macOS    – scan /Applications and ~/Applications for /^LibreOffice[\s\d.]*\.app$/i,
 *             plus Homebrew (Intel & Apple Silicon) and MacPorts fixed paths.
 *  Linux    – fixed distro/local/snap/flatpak paths, then scan /opt for
 *             /^libreoffice[\d.]*$/i, and ~/.local for user installs.
 */
function getCandidatePaths(): string[] {
  const platform = process.platform;

  // -------------------------------------------------------------------------
  // Windows
  // -------------------------------------------------------------------------
  if (platform === "win32") {
    const pf      = process.env["ProgramFiles"]      ?? "C:\\Program Files";
    const pf86    = process.env["ProgramFiles(x86)"] ?? "C:\\Program Files (x86)";
    const local   = process.env["LOCALAPPDATA"]       ?? "";
    const appData = process.env["APPDATA"]            ?? "";

    // Matches "LibreOffice", "LibreOffice 7", "LibreOffice 24.8", etc.
    const loPattern = /^LibreOffice(\s[\d.]+)?$/i;

    const paths: string[] = [];

    // Scan both Program Files roots for any LibreOffice installation folder.
    for (const root of [pf, pf86]) {
      for (const entry of scanDir(root, loPattern)) {
        paths.push(`${root}\\${entry}\\program\\soffice.exe`);
      }
    }

    // Per-user installs
    if (local) {
      paths.push(
        `${local}\\Programs\\LibreOffice\\program\\soffice.exe`,
        `${local}\\LibreOffice\\program\\soffice.exe`,
      );
    }
    if (appData) {
      paths.push(`${appData}\\LibreOffice\\program\\soffice.exe`);
    }

    return paths;
  }

  // -------------------------------------------------------------------------
  // macOS
  // -------------------------------------------------------------------------
  if (platform === "darwin") {
    const home = process.env["HOME"] ?? "";

    // Matches "LibreOffice.app", "LibreOffice 7.app", "LibreOffice 24.8.app", etc.
    const bundlePattern = /^LibreOffice[\s\d.]*\.app$/i;
    const macosRelative = "Contents/MacOS/soffice";

    const paths: string[] = [];

    // Scan /Applications and ~/Applications for any LibreOffice bundle.
    const appDirs = ["/Applications"];
    if (home) appDirs.push(`${home}/Applications`);

    for (const appDir of appDirs) {
      for (const bundle of scanDir(appDir, bundlePattern)) {
        paths.push(`${appDir}/${bundle}/${macosRelative}`);
      }
    }

    // Homebrew – Intel Macs
    paths.push(
      "/usr/local/bin/soffice",
      "/usr/local/lib/libreoffice/program/soffice",
    );

    // Homebrew – Apple Silicon (M-series)
    paths.push(
      "/opt/homebrew/bin/soffice",
      "/opt/homebrew/lib/libreoffice/program/soffice",
    );

    // MacPorts
    paths.push("/opt/local/bin/soffice");

    return paths;
  }

  // -------------------------------------------------------------------------
  // Linux
  // -------------------------------------------------------------------------
  const home = process.env["HOME"] ?? "";

  const paths: string[] = [
    // Distro packages (Debian/Ubuntu, Fedora, Arch, openSUSE, …)
    "/usr/bin/soffice",
    "/usr/bin/libreoffice",
    "/usr/lib/libreoffice/program/soffice",
    "/usr/lib64/libreoffice/program/soffice",

    // Manual / local installs
    "/usr/local/bin/soffice",
    "/usr/local/lib/libreoffice/program/soffice",

    // Snap – classic and strict confinement
    "/snap/bin/soffice",
    "/snap/bin/libreoffice",
    "/var/lib/snapd/snap/bin/soffice",
    "/var/lib/snapd/snap/bin/libreoffice",

    // Flatpak – system-wide
    "/var/lib/flatpak/exports/bin/org.libreoffice.LibreOffice",
    "/var/lib/flatpak/app/org.libreoffice.LibreOffice/current/active/export/bin/libreoffice",
  ];

  // Scan /opt for any versioned tarball directory, e.g. libreoffice7.6,
  // libreoffice24.8, libreoffice (plain symlink), etc.
  // Matches "libreoffice", "libreoffice7", "libreoffice7.6", "libreoffice24.2", …
  const optPattern = /^libreoffice[\d.]*$/i;
  for (const entry of scanDir("/opt", optPattern)) {
    paths.push(`/opt/${entry}/program/soffice`);
  }

  // Flatpak – per-user and ~/.local installs
  if (home) {
    paths.push(
      `${home}/.local/share/flatpak/exports/bin/org.libreoffice.LibreOffice`,
      `${home}/.local/share/flatpak/app/org.libreoffice.LibreOffice/current/active/export/bin/libreoffice`,
      `${home}/.local/bin/soffice`,
      `${home}/.local/lib/libreoffice/program/soffice`,
    );
  }

  return paths;
}

/**
 * Returns a human-readable, OS-specific install instruction string.
 */
function getInstallInstructions(): string {
  const platform = process.platform;

  if (platform === "win32") {
    return (
      "Download the Windows installer from https://www.libreoffice.org/download/ " +
      "and run it.  Both the 64-bit and 32-bit editions are supported."
    );
  }

  if (platform === "darwin") {
    return (
      "Download the macOS disk image from https://www.libreoffice.org/download/ " +
      "and drag LibreOffice into your Applications folder."
    );
  }

  // Linux
  return (
    "Install LibreOffice with your package manager, for example:\n\n" +
    "  Ubuntu / Debian:  sudo apt install libreoffice\n" +
    "  Fedora:           sudo dnf install libreoffice\n" +
    "  Arch:             sudo pacman -S libreoffice-still\n\n" +
    "Or download it from https://www.libreoffice.org/download/"
  );
}

// ---------------------------------------------------------------------------
// Resolved path – set once by checkLibreOfficeBeforeWindow()
// ---------------------------------------------------------------------------

/**
 * The resolved soffice binary path discovered at startup.
 * Defaults to the bare command name so callers always get a usable string
 * even if the check has not run yet (e.g. in non-Electron environments).
 */
let resolvedSofficePath: string = "soffice";

/**
 * Returns the resolved soffice binary path found during startup detection.
 *
 * Pass as the `SOFFICE_PATH` env var to the FastAPI subprocess so Python
 * code can invoke the exact binary rather than relying on `PATH`.
 */
export function getSofficePath(): string {
  return resolvedSofficePath;
}

// ---------------------------------------------------------------------------
// Core detection logic
// ---------------------------------------------------------------------------

/**
 * Attempts to detect LibreOffice by:
 *  1. Checking well-known installation paths for the binary (fast, no shell).
 *  2. Falling back to `soffice --version` via the shell (catches PATH installs).
 *
 * Returns an object indicating whether LibreOffice was found and, when it
 * was, the version string reported by the binary.
 */
async function isLibreOfficeInstalled(): Promise<LibreOfficeCheckResult> {
  // --- Step 1: check well-known paths synchronously (no exec overhead) ---
  for (const candidate of getCandidatePaths()) {
    if (fs.existsSync(candidate)) {
      // Binary found at a known location – try to get the version string.
      try {
        const quoted = `"${candidate}"`;
        const { stdout } = await execAsync(`${quoted} --version`, {
          timeout: 8_000,
        });
        return { installed: true, version: stdout.trim(), path: candidate };
      } catch {
        // Binary exists but failed to execute – still treat as installed.
        return { installed: true, path: candidate };
      }
    }
  }

  // --- Step 2: try the PATH-based command ---
  try {
    const { stdout } = await execAsync("soffice --version", {
      timeout: 8_000,
    });
    // Found via PATH – record the bare command name as the path so callers
    // can pass it directly to subprocess invocations.
    return { installed: true, version: stdout.trim(), path: "soffice" };
  } catch {
    // Command not found or timed out – LibreOffice is not available.
    return { installed: false };
  }
}

// ---------------------------------------------------------------------------
// Dialog
// ---------------------------------------------------------------------------

/**
 * Shows a modal dialog informing the user that LibreOffice is required.
 *
 * Button indices:
 *  0 – "Download LibreOffice" → opens download page, shows a re-launch notice,
 *                               then quits the application
 *  1 – "Install Later"        → continues launching without LibreOffice
 *  2 – "Exit"                 → quits the application immediately
 *
 * @returns `true` if the application should proceed to create its window,
 *          `false` if `app.quit()` has been called.
 */
async function showLibreOfficeMissingDialog(): Promise<boolean> {
  const instructions = getInstallInstructions();

  const { response } = await dialog.showMessageBox({
    type: "warning",
    title: "LibreOffice Required",
    message: "LibreOffice is not installed",
    detail:
      "Presenton uses LibreOffice to export presentations to PPTX and PDF " +
      "formats.  Without it, export functionality will not work.\n\n" +
      `How to install LibreOffice on your system:\n\n${instructions}`,
    buttons: ["Download LibreOffice", "Install Later", "Exit"],
    defaultId: 0,
    cancelId: 1,
    noLink: true,
  });

  if (response === 0) {
    // Open the LibreOffice download page in the default browser.
    await shell.openExternal("https://www.libreoffice.org/download/");

    // Let the user know they need to restart Presenton after installation,
    // then close the app so they start fresh with LibreOffice on the PATH.
    await dialog.showMessageBox({
      type: "info",
      title: "Restart Required",
      message: "Please re-launch Presenton after installation",
      detail:
        "The LibreOffice download page has been opened in your browser.\n\n" +
        "Once LibreOffice is installed, re-run Presenton and it will be " +
        "detected automatically.",
      buttons: ["OK"],
      defaultId: 0,
    });

    app.quit();
    return false;
  }

  if (response === 2) {
    // User chose to exit immediately.
    app.quit();
    return false;
  }

  // response === 1 → "Install Later" – continue launching without LibreOffice.
  return true;
}

// ---------------------------------------------------------------------------
// Public API
// ---------------------------------------------------------------------------

/**
 * Checks for LibreOffice and, when it is absent, presents the user with the
 * "LibreOffice Required" dialog.
 *
 * Call this function **before** creating the main `BrowserWindow`.
 *
 * @returns `true` if the application should proceed to create its window,
 *          `false` if the user chose to exit and `app.quit()` has been called.
 */
export async function checkLibreOfficeBeforeWindow(): Promise<boolean> {
  const result = await isLibreOfficeInstalled();

  if (result.installed) {
    // Persist the resolved path so getSofficePath() returns it for the
    // lifetime of this Electron process.
    if (result.path) {
      resolvedSofficePath = result.path;
    }
    console.log(
      `[LibreOffice] Detected: ${result.version ?? "(version unknown)"} at ${resolvedSofficePath}`
    );
    return true;
  }

  console.warn(
    "[LibreOffice] Not found on this system – showing installation dialog."
  );
  return showLibreOfficeMissingDialog();
}
