require("dotenv").config();
import {
  app,
  BrowserWindow,
  globalShortcut,
  type BrowserWindowConstructorOptions,
} from "electron";
import path from "path";
import fs from "fs";
import crypto from "crypto";
import { findUnusedPorts, setupEnv, setUserConfig } from "./utils";
import {
  startFastApiServer,
  startNextJsServer,
  startWorkspaceOperationWorker,
} from "./utils/servers";
import {
  baseDir,
  ensureDirectoriesExist,
  fastapiDir,
  getAppDataDir,
  getTempDir,
  getUserConfigPath,
  initializeAppPaths,
  isDev,
  localhost,
  nextjsDir,
} from "./utils/constants";
import { setupUserConfigRuntime } from "./ipc";
import { ipcMain } from "electron";
import { stopActiveExportProcesses } from "./ipc/export_handlers";
import { setupLibreOfficeInstallHandlers, stopActiveLibreOfficeInstallProcesses } from "./ipc/libreoffice_install_handlers";
import { setupSetupInstallHandlers, stopActiveSetupInstallProcesses } from "./ipc/setup_install_handlers";
import { checkDependenciesBeforeWindow } from "./utils/setup-dependencies";
import { getSofficePath, isLibreOfficeInstalled } from "./utils/libreoffice-check";
import { getLiteParseRunnerPath } from "./utils/liteparse-check";
import { getImageMagickBinaryPath, isImageMagickInstalled } from "./utils/imagemagick-check";
import { isExportChromiumAvailable } from "./utils/export-chromium";
import { startUpdateChecker, stopUpdateChecker } from "./utils/update-checker";
import {
  addMainBreadcrumb,
  captureMainException,
  initMainSentry,
  setMainSentryRuntimeContext,
} from "./sentry/main";
import { installSafeConsole, safeError, safeLog, safeStderrWrite, safeWarn } from "./utils/safe-console";
import { memorySnapshotMb } from "./utils/memory";
import { createWindowSecurityController } from "./utils/window-security";
import { getAppWebPreferences as buildSecureAppWebPreferences } from "./utils/authored-renderer-policy";
import { buildProviderSecureEnv } from "./utils/provider-secure-env";
import { buildProviderNonSecretStartupConfig } from "./utils/provider-startup-config";
import {
  finishChromiumCacheRecovery,
  prepareChromiumCacheRecovery,
  type ChromiumCacheRecoveryStatus,
} from "./utils/chromium-cache-recovery";

installSafeConsole();
// Enforce Chromium's OS sandbox for every renderer, including windows that a
// future code path might create without an explicit BrowserWindow preference.
app.enableSandbox();

// Chromium sandboxing is a launch requirement. Packaging/deployment must provide a
// valid Linux sandbox instead of weakening every renderer when it is unavailable.
if (process.platform === "linux") {
  // Fall back to /tmp instead of shared memory to avoid Chromium crashes
  // on systems where /dev/shm is unavailable/misconfigured.
  app.commandLine.appendSwitch("disable-dev-shm-usage");
}

var win: BrowserWindow | undefined;
type ManagedServerProcess = Awaited<ReturnType<typeof startFastApiServer>>;
var fastApiServer: ManagedServerProcess | undefined;
var nextjsServer: ManagedServerProcess | undefined;
var workspaceOperationWorker: ManagedServerProcess | undefined;
let isStopping = false;
let trustedAppOrigin: string | undefined;
const startupStatus: Record<string, string> = {
  libreoffice: "checking",
  imagemagick: "checking",
  chromium: "checking",
};

function getLiveMainWindow(): BrowserWindow | undefined {
  if (!win || win.isDestroyed()) {
    return undefined;
  }
  return win;
}

type ProcessGoneDetails = {
  reason?: string;
  type?: string;
  exitCode?: number;
  serviceName?: string;
  name?: string;
};

function profileHash(userDataDir: string): string {
  return crypto.createHash("sha256").update(userDataDir).digest("hex").slice(0, 16);
}

function updateSentryRuntimeContext(cacheRecovery: ChromiumCacheRecoveryStatus): void {
  setMainSentryRuntimeContext({
    profileHash: profileHash(electronAppPaths.userDataDir),
    cacheRecoveryStatus: cacheRecovery.status,
    cacheRecoveryMode: cacheRecovery.mode,
  });
}

function recordProcessGone(kind: "child" | "renderer", details: ProcessGoneDetails): void {
  const reason = details.reason ?? "unknown";
  const data = {
    kind,
    reason,
    type: details.type,
    exitCode: details.exitCode,
    serviceName: details.serviceName,
    name: details.name,
  };

  addMainBreadcrumb("process", `electron.${kind}_process_gone`, data);
  if (isStopping || reason === "clean-exit") {
    return;
  }

  captureMainException(new Error(`Electron ${kind} process gone: ${reason}`), data);
}

function resolveExportConverterPath(appRoot: string): string | undefined {
  const pyDir = path.join(appRoot, "resources", "export", "py");
  const candidates = [
    path.join(pyDir, `convert-${process.platform}-${process.arch}`),
    path.join(pyDir, `convert-${process.platform}-${process.arch}.exe`),
    path.join(pyDir, `convert-${process.platform}`),
    path.join(pyDir, `convert-${process.platform}.exe`),
    path.join(pyDir, "convert"),
    path.join(pyDir, "convert.exe"),
  ];
  return candidates.find((candidate) => fs.existsSync(candidate));
}

function resolveElectronDisableAuth(): string {
  const raw = (
    process.env.ELECTRON_DISABLE_AUTH ?? process.env.DISABLE_AUTH
  )?.trim().toLowerCase();
  if (!raw) {
    return "true";
  }
  if (["0", "false", "no", "off"].includes(raw)) {
    return "false";
  }
  if (["1", "true", "yes", "on"].includes(raw)) {
    return "true";
  }
  return "true";
}

app.commandLine.appendSwitch('gtk-version', '3');
// Electron authored previews are PNG-only. Keep site isolation explicit as a
// defense-in-depth boundary for every untrusted or cross-origin application frame.
app.commandLine.appendSwitch("site-per-process");

// Work around Chromium/Electron GPU compositor issues that can cause
// startup white screens on some Linux/driver combinations.
app.disableHardwareAcceleration();

const gotSingleInstanceLock = app.requestSingleInstanceLock();
if (!gotSingleInstanceLock) {
  app.quit();
}

const electronAppPaths = initializeAppPaths();
const chromiumCacheRecovery = prepareChromiumCacheRecovery(
  electronAppPaths.cacheDir,
  electronAppPaths.userDataDir,
);
safeLog("[Presenton] Electron paths initialized:", electronAppPaths);

// Allow renderer to query initial startup status as soon as it loads.
ipcMain.handle("startup:get-status", () => startupStatus);

initMainSentry();
updateSentryRuntimeContext(chromiumCacheRecovery);

app.on("child-process-gone", (_event, details) => {
  recordProcessGone("child", details);
});

addMainBreadcrumb("memory", "electron.main.startup", memorySnapshotMb());
safeLog("[Presenton] Startup memory:", {
  memory: memorySnapshotMb(),
});

function getAppWebPreferences(): Electron.WebPreferences {
  const preloadPath = path.join(__dirname, "preloads/index.js");
  try {
    if (!fs.existsSync(preloadPath)) {
      safeWarn(`[Presenton] Preload not found at ${preloadPath}`);
    }
  } catch (error) {
    safeWarn("[Presenton] Failed to stat preload path", error);
  }
  return buildSecureAppWebPreferences(preloadPath);
}

function getPresentationWindowOptions(): BrowserWindowConstructorOptions {
  return {
    width: 1280,
    height: 720,
    show: false,
    backgroundColor: "#f3f5ff",
    icon: path.join(baseDir, "resources/ui/assets/images/presenton_short_filled.png"),
    webPreferences: getAppWebPreferences(),
  };
}

const windowSecurity = createWindowSecurityController({
  getTrustedAppOrigin: () => trustedAppOrigin,
  getPresentationWindowOptions,
});

const createWindow = () => {
  const mainWindow = new BrowserWindow(getPresentationWindowOptions());
  win = mainWindow;
  windowSecurity.attach(mainWindow, { scope: "app" });

  mainWindow.on("closed", () => {
    if (win === mainWindow) {
      win = undefined;
    }
  });

  mainWindow.webContents.on("render-process-gone", (_event, details) => {
    recordProcessGone("renderer", details);
  });

  mainWindow.once("ready-to-show", () => {
    if (mainWindow.isDestroyed()) {
      return;
    }
    mainWindow.show();
    mainWindow.focus();
  });

};

function focusMainWindow(): void {
  const mainWindow = getLiveMainWindow();
  if (!mainWindow) {
    return;
  }
  if (mainWindow.isMinimized()) {
    mainWindow.restore();
  }
  mainWindow.show();
  mainWindow.focus();
}

if (gotSingleInstanceLock) {
  app.on("second-instance", () => {
    focusMainWindow();
  });
}

async function startServers(fastApiPort: number, nextjsPort: number, userConfigRuntimeEnv: FastApiEnv) {
  try {
    const appDataDir = getAppDataDir();
    const tempDir = getTempDir();
    const userConfigPath = getUserConfigPath();
    const disableAuthForElectron = resolveElectronDisableAuth();
    const sofficePath = getSofficePath();
    const exportPackageRoot = path.join(baseDir, "resources", "export");
    const exportConverterPath = resolveExportConverterPath(baseDir);
    const workspaceWorkerMode = "external";
    const fastApiEnvironment: FastApiEnv = {
      DEBUG: isDev ? "True" : "False",
      ...buildProviderNonSecretStartupConfig(process.env),
      DATABASE_URL: process.env.DATABASE_URL,
      SLIDESPEC_IMPORT_SHADOW: process.env.SLIDESPEC_IMPORT_SHADOW,
      SLIDESPEC_REPAIR_SHADOW: process.env.SLIDESPEC_REPAIR_SHADOW,
      SLIDESPEC_REPAIR_CANARY: process.env.SLIDESPEC_REPAIR_CANARY,
      SLIDESPEC_REPAIR_APPLY: process.env.SLIDESPEC_REPAIR_APPLY,
      SLIDESPEC_REPAIR_ALL_SLIDES: process.env.SLIDESPEC_REPAIR_ALL_SLIDES,
      SLIDESPEC_KILL_SWITCH: process.env.SLIDESPEC_KILL_SWITCH,
      SLIDESPEC_REPAIR_ROLLOUT_SALT: process.env.SLIDESPEC_REPAIR_ROLLOUT_SALT,
      APP_DATA_DIRECTORY: appDataDir,
      ...buildProviderSecureEnv(appDataDir),
      TEMP_DIRECTORY: tempDir,
      USER_CONFIG_PATH: userConfigPath, ...userConfigRuntimeEnv,
      MIGRATE_DATABASE_ON_STARTUP: "True",
      WORKSPACE_OPERATION_WORKER_MODE: workspaceWorkerMode,
      DISABLE_AUTH: disableAuthForElectron,
      ...(sofficePath && { SOFFICE_PATH: sofficePath }),
      IMAGEMAGICK_BINARY: getImageMagickBinaryPath(),
      LITEPARSE_RUNNER_PATH: getLiteParseRunnerPath(),
      LITEPARSE_NODE_BINARY:
        process.env.PRESENTON_DEV_NODE_BINARY || (isDev ? "node" : process.execPath),
      ELECTRON_RUN_AS_NODE: "1",
      EXPORT_PACKAGE_ROOT: exportPackageRoot,
      EXPORT_RUNTIME_DIR: exportPackageRoot,
      ...(exportConverterPath && { BUILT_PYTHON_MODULE_PATH: exportConverterPath }),
    };
    const fastApi = await startFastApiServer(
      fastapiDir,
      fastApiPort,
      fastApiEnvironment,
      isDev,
    );
    fastApiServer = fastApi;
    await fastApi.ready;

    const worker = await startWorkspaceOperationWorker(
      fastapiDir,
      fastApiEnvironment,
      isDev,
    );
    workspaceOperationWorker = worker;
    await worker.ready;
    if (worker.process.exitCode !== null) {
      throw new Error("Workspace operation worker exited during startup");
    }
    worker.process.once("exit", (code, signal) => {
      if (workspaceOperationWorker !== worker) return;
      safeError(`Workspace operation worker exited unexpectedly: code=${code}, signal=${signal}`);
      void forceQuitApp(1);
    });

    const nextjs = await startNextJsServer(
      nextjsDir,
      nextjsPort,
      {
        NEXT_PUBLIC_FAST_API: process.env.NEXT_PUBLIC_FAST_API,
        TEMP_DIRECTORY: process.env.TEMP_DIRECTORY,
        NEXT_PUBLIC_URL: process.env.NEXT_PUBLIC_URL,
        NEXT_PUBLIC_USER_CONFIG_PATH: process.env.NEXT_PUBLIC_USER_CONFIG_PATH,
        NEXT_PUBLIC_SLIDESPEC_REPAIR_CANARY_UI:
          process.env.NEXT_PUBLIC_SLIDESPEC_REPAIR_CANARY_UI,
        NEXT_PUBLIC_SLIDESPEC_REPAIR_ALL_SLIDES:
          process.env.NEXT_PUBLIC_SLIDESPEC_REPAIR_ALL_SLIDES,
        USER_CONFIG_PATH: process.env.NEXT_PUBLIC_USER_CONFIG_PATH,
        APP_DATA_DIRECTORY: appDataDir,
        DISABLE_AUTH: disableAuthForElectron,
        EXPORT_PACKAGE_ROOT: exportPackageRoot,
        PRESENTON_APP_ROOT: baseDir,
        ...(exportConverterPath && {
          BUILT_PYTHON_MODULE_PATH: exportConverterPath,
        }),
      },
      isDev,
    );
    nextjsServer = nextjs;
    await nextjs.ready;
  } catch (error) {
    safeError("Server startup error:", error);
    await stopServers();
    throw error;
  }
}

async function stopServers() {
  const fastApi = fastApiServer;
  const nextjs = nextjsServer;
  const worker = workspaceOperationWorker;
  fastApiServer = undefined;
  nextjsServer = undefined;
  workspaceOperationWorker = undefined;

  await Promise.all([
    fastApi
      ? fastApi.stop().catch((error) => safeError("Failed to stop FastAPI:", error))
      : Promise.resolve(),
    nextjs
      ? nextjs.stop().catch((error) => safeError("Failed to stop NextJS:", error))
      : Promise.resolve(),
    worker
      ? worker.stop().catch((error) => safeError("Failed to stop workspace worker:", error))
      : Promise.resolve(),
  ]);
}

async function forceQuitApp(exitCode = 0) {
  if (isStopping) return;
  isStopping = true;
  globalShortcut.unregisterAll();
  stopUpdateChecker();
  try {
    await stopActiveExportProcesses();
    await stopActiveSetupInstallProcesses();
    await stopActiveLibreOfficeInstallProcesses();
    await stopServers();
  } finally {
    app.exit(exitCode);
  }
}

if (gotSingleInstanceLock) {
app.whenReady().then(async () => {
  // Ensure all required directories exist before starting
  ensureDirectoriesExist();

  await finishChromiumCacheRecovery(
    electronAppPaths.userDataDir,
    chromiumCacheRecovery,
  );
  updateSentryRuntimeContext(chromiumCacheRecovery);

  // Register install handlers early so the unified setup window can use them
  setupLibreOfficeInstallHandlers();
  setupSetupInstallHandlers();

  // Create main window before setup so that when user skips, the main window stays open
  createWindow();
  const initialWindow = getLiveMainWindow();
  if (initialWindow && !initialWindow.webContents.isDestroyed()) {
    void initialWindow
      .loadFile(path.join(baseDir, "resources/ui/homepage/index.html"))
      .catch((error) => {
        if (!initialWindow.isDestroyed()) {
          safeWarn("[Presenton] Failed to load startup page", error);
        }
      });
  }

  // Single installer: checks LibreOffice, ImageMagick, and export Chromium; if any are
  // missing, shows one window that installs them one after another.
  const setupCompleted = await checkDependenciesBeforeWindow();
  if (!setupCompleted) {
    // Block app usage when required setup is not completed.
    getLiveMainWindow()?.destroy();
    app.quit();
    return;
  }

  // Update startup status after setup (user may have installed one or both)
  const [loResult, imageMagickOk] = await Promise.all([
    isLibreOfficeInstalled(),
    Promise.resolve(isImageMagickInstalled()),
  ]);
  startupStatus.libreoffice = loResult.installed ? "installed" : "missing";
  startupStatus.imagemagick = imageMagickOk ? "installed" : "missing";
  startupStatus.chromium = isExportChromiumAvailable() ? "installed" : "missing";

  // Ensure the launch screen stays visible and focused during the server boot.
  const launchWindow = getLiveMainWindow();
  launchWindow?.show();
  launchWindow?.focus();

  const sendStartupStatus = (name: string, status: string) => {
    startupStatus[name] = status;
    const mainWindow = getLiveMainWindow();
    if (!mainWindow || mainWindow.webContents.isDestroyed()) {
      return;
    }
    mainWindow.webContents.send("startup:status", { name, status });
  };

  const statusWindow = getLiveMainWindow();
  if (statusWindow && !statusWindow.webContents.isDestroyed()) {
    statusWindow.webContents.once("did-finish-load", () => {
      sendStartupStatus("libreoffice", startupStatus.libreoffice);
      sendStartupStatus("imagemagick", startupStatus.imagemagick);
      sendStartupStatus("chromium", startupStatus.chromium);
    });
  }

  try {
    setUserConfig(buildProviderNonSecretStartupConfig(process.env))
  } catch (error) {
    safeWarn("[Presenton] Failed to persist startup user config", error);
  }

  const [fastApiPort, nextjsPort] = await findUnusedPorts();
  safeLog(`FastAPI port: ${fastApiPort}, NextJS port: ${nextjsPort}`);
  trustedAppOrigin = new URL(`${localhost}:${nextjsPort}`).origin;

  //? Setup environment variables to be used in the preloads
  setupEnv(fastApiPort, nextjsPort);
  const userConfigRuntimeEnv = setupUserConfigRuntime(
    fastApiPort,
    () => trustedAppOrigin,
  );

  try {
    await startServers(fastApiPort, nextjsPort, userConfigRuntimeEnv);
  } catch (error) {
    safeError("[Presenton] Required application servers did not become ready", error);
    await forceQuitApp(1);
    return;
  }
  if (isStopping) {
    return;
  }
  const mainWindow = getLiveMainWindow();
  if (!mainWindow || mainWindow.webContents.isDestroyed()) {
    return;
  }

  try {
    await mainWindow.loadURL(`${localhost}:${nextjsPort}`);
  } catch (error) {
    if (mainWindow.isDestroyed()) {
      return;
    }
    safeWarn("[Presenton] Failed to load application URL", error);
    return;
  }

  // Begin polling the version server for available updates
  const updateWindow = getLiveMainWindow();
  if (updateWindow && !updateWindow.webContents.isDestroyed()) {
    safeStderrWrite("[Presenton] Starting update checker...\n");
    startUpdateChecker(updateWindow);
  }
});

app.on("window-all-closed", async () => {
  await forceQuitApp(0);
});

app.on("before-quit", async (event) => {
  if (isStopping) return;
  event.preventDefault();
  await forceQuitApp(0);
});

app.on("will-quit", async (event) => {
  if (isStopping) return;
  event.preventDefault();
  await forceQuitApp(0);
});
}
