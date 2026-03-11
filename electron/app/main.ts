require("dotenv").config();
import { app, BrowserWindow, shell } from "electron";
import path from "path";
import fs from "fs";
import { findUnusedPorts, killProcess, setupEnv, setUserConfig } from "./utils";
import { startFastApiServer, startNextJsServer } from "./utils/servers";
import { ChildProcessByStdio } from "child_process";
import { appDataDir, baseDir, ensureDirectoriesExist, fastapiDir, isDev, localhost, nextjsDir, tempDir, userConfigPath, userDataDir } from "./utils/constants";
import { setupIpcHandlers } from "./ipc";
import { ipcMain } from "electron";
import { setupLibreOfficeInstallHandlers } from "./ipc/libreoffice_install_handlers";
import { checkLibreOfficeBeforeWindow, getSofficePath } from "./utils/libreoffice-check";
import { checkPuppeteerChromiumBeforeWindow } from "./utils/puppeteer-check";
import { startUpdateChecker, stopUpdateChecker } from "./utils/update-checker";


var win: BrowserWindow | undefined;
var fastApiProcess: ChildProcessByStdio<any, any, any> | undefined;
var nextjsProcess: any;
let isStopping = false;
const startupStatus: Record<string, string> = {
  libreoffice: "checking",
  puppeteer: "checking",
};

app.commandLine.appendSwitch('gtk-version', '3');

// Mitigate "Unable to move the cache: Access is denied" on Windows (Chromium disk cache).
// Use explicit cache paths and remove stale old_* dirs that cause move failures.
if (process.platform === "win32") {
  const ud = app.getPath("userData");
  const cacheBase = path.join(ud, "Cache");
  const gpuCacheBase = path.join(ud, "GPUCache");
  app.setPath("cache", cacheBase);
  app.commandLine.appendSwitch("disk-cache-dir", cacheBase);
  try {
    [cacheBase, gpuCacheBase].forEach((dir) => {
      if (fs.existsSync(dir)) {
        const entries = fs.readdirSync(dir, { withFileTypes: true });
        for (const e of entries) {
          if (e.isDirectory() && e.name.startsWith("old_")) {
            fs.rmSync(path.join(dir, e.name), { recursive: true, force: true });
          }
        }
      }
    });
  } catch {
    /* ignore cleanup errors */
  }
}

const createWindow = () => {
  win = new BrowserWindow({
    width: 1280,
    height: 720,
    show: false, // Shown after LibreOffice check so "Skip" doesn't quit the app
    icon: path.join(baseDir, "resources/ui/assets/images/presenton_short_filled.png"),
    webPreferences: {
      webSecurity: false,
      preload: path.join(__dirname, 'preloads/index.js'),
    },
  });

  // Open external links (e.g. "Download update") in the system browser so the user
  // sees download progress and can manage downloads normally.
  win.webContents.setWindowOpenHandler(({ url }) => {
    if (url.startsWith("http://") || url.startsWith("https://")) {
      shell.openExternal(url);
      return { action: "deny" };
    }
    return { action: "allow" };
  });
};

async function startServers(fastApiPort: number, nextjsPort: number) {
  try {
    const fastApi = await startFastApiServer(
      fastapiDir,
      fastApiPort,
      {
        DEBUG: isDev ? "True" : "False",
        CAN_CHANGE_KEYS: process.env.CAN_CHANGE_KEYS,
        LLM: process.env.LLM,
        OPENAI_API_KEY: process.env.OPENAI_API_KEY,
        OPENAI_MODEL: process.env.OPENAI_MODEL,
        GOOGLE_API_KEY: process.env.GOOGLE_API_KEY,
        GOOGLE_MODEL: process.env.GOOGLE_MODEL,
        ANTHROPIC_API_KEY: process.env.ANTHROPIC_API_KEY,
        ANTHROPIC_MODEL: process.env.ANTHROPIC_MODEL,
        OLLAMA_URL: process.env.OLLAMA_URL,
        OLLAMA_MODEL: process.env.OLLAMA_MODEL,
        CUSTOM_LLM_URL: process.env.CUSTOM_LLM_URL,
        CUSTOM_LLM_API_KEY: process.env.CUSTOM_LLM_API_KEY,
        CUSTOM_MODEL: process.env.CUSTOM_MODEL,
        PEXELS_API_KEY: process.env.PEXELS_API_KEY,
        PIXABAY_API_KEY: process.env.PIXABAY_API_KEY,
        IMAGE_PROVIDER: process.env.IMAGE_PROVIDER,
        DISABLE_IMAGE_GENERATION: process.env.DISABLE_IMAGE_GENERATION,
        EXTENDED_REASONING: process.env.EXTENDED_REASONING,
        TOOL_CALLS: process.env.TOOL_CALLS,
        DISABLE_THINKING: process.env.DISABLE_THINKING,
        WEB_GROUNDING: process.env.WEB_GROUNDING,
        DATABASE_URL: process.env.DATABASE_URL,
        DISABLE_ANONYMOUS_TRACKING: process.env.DISABLE_ANONYMOUS_TRACKING,
        COMFYUI_URL: process.env.COMFYUI_URL,
        COMFYUI_WORKFLOW: process.env.COMFYUI_WORKFLOW,
        DALL_E_3_QUALITY: process.env.DALL_E_3_QUALITY,
        GPT_IMAGE_1_5_QUALITY: process.env.GPT_IMAGE_1_5_QUALITY,
        APP_DATA_DIRECTORY: appDataDir,
        TEMP_DIRECTORY: tempDir,
        USER_CONFIG_PATH: userConfigPath,
        MIGRATE_DATABASE_ON_STARTUP: "True",
        // Resolved by libreoffice-check.ts at startup; lets Python invoke the
        // exact binary path instead of relying on the system PATH.
        SOFFICE_PATH: getSofficePath(),
      },
      isDev,
    );
    fastApiProcess = fastApi.process;
    await fastApi.ready;

    const nextjs = await startNextJsServer(
      nextjsDir,
      nextjsPort,
      {
        NEXT_PUBLIC_FAST_API: process.env.NEXT_PUBLIC_FAST_API,
        TEMP_DIRECTORY: process.env.TEMP_DIRECTORY,
        NEXT_PUBLIC_URL: process.env.NEXT_PUBLIC_URL,
        NEXT_PUBLIC_USER_CONFIG_PATH: process.env.NEXT_PUBLIC_USER_CONFIG_PATH,
        USER_CONFIG_PATH: process.env.NEXT_PUBLIC_USER_CONFIG_PATH,
        APP_DATA_DIRECTORY: appDataDir,
      },
      isDev,
    )
    nextjsProcess = nextjs.process;
    await nextjs.ready;
  } catch (error) {
    console.error("Server startup error:", error);
  }
}

async function stopServers() {
  if (fastApiProcess?.pid) {
    console.log("Closing FastAPI...");
    try {
      await killProcess(fastApiProcess.pid);
    } catch {
      await killProcess(fastApiProcess.pid, "SIGKILL");
    }
  }
  if (nextjsProcess) {
    if (isDev) {
      console.log("Closing NextJS...");
      try {
        await killProcess(nextjsProcess.pid);
      } catch {
        await killProcess(nextjsProcess.pid, "SIGKILL");
      }
    } else {
      console.log("Closing NextJS...");
      nextjsProcess.close();
    }
  }
}

app.whenReady().then(async () => {
  // Ensure all required directories exist before starting
  ensureDirectoriesExist();

  // Register LibreOffice install handlers early so the installer window can use them
  setupLibreOfficeInstallHandlers();

  // Create main window BEFORE LibreOffice check so that when user clicks "Skip for now",
  // the installer closes but the main window stays open (avoids app quit on window-all-closed).
  createWindow();
  win?.loadFile(path.join(baseDir, "resources/ui/homepage/index.html"));

  // Check for LibreOffice (required for custom template from PPTX). Shows installer
  // window if missing. Never blocks; always proceeds.
  await checkLibreOfficeBeforeWindow();

  // Show and focus main window (was hidden to avoid app quit when user clicks "Skip for now")
  win?.show();
  win?.focus();

  const sendStartupStatus = (name: string, status: string) => {
    startupStatus[name] = status;
    win?.webContents.send("startup:status", { name, status });
  };

  win?.webContents.once("did-finish-load", async () => {
    // Emit initial status so the UI doesn't remain in "Checking..." if it
    // registers late.
    sendStartupStatus("libreoffice", startupStatus.libreoffice);
    sendStartupStatus("puppeteer", startupStatus.puppeteer);
    // Check for LibreOffice (required for custom template from PPTX). Shows installer
    // window if missing. Never blocks; always proceeds.
    await checkLibreOfficeBeforeWindow((status) =>
      sendStartupStatus("libreoffice", status)
    );
    // Check Puppeteer Chromium (used for export & template rendering).
    await checkPuppeteerChromiumBeforeWindow((status) =>
      sendStartupStatus("puppeteer", status)
    );
  });

  setUserConfig({
    CAN_CHANGE_KEYS: process.env.CAN_CHANGE_KEYS,
    LLM: process.env.LLM,
    OPENAI_API_KEY: process.env.OPENAI_API_KEY,
    OPENAI_MODEL: process.env.OPENAI_MODEL,
    GOOGLE_API_KEY: process.env.GOOGLE_API_KEY,
    GOOGLE_MODEL: process.env.GOOGLE_MODEL,
    ANTHROPIC_API_KEY: process.env.ANTHROPIC_API_KEY,
    ANTHROPIC_MODEL: process.env.ANTHROPIC_MODEL,
    OLLAMA_URL: process.env.OLLAMA_URL,
    OLLAMA_MODEL: process.env.OLLAMA_MODEL,
    CUSTOM_LLM_URL: process.env.CUSTOM_LLM_URL,
    CUSTOM_LLM_API_KEY: process.env.CUSTOM_LLM_API_KEY,
    CUSTOM_MODEL: process.env.CUSTOM_MODEL,
    PEXELS_API_KEY: process.env.PEXELS_API_KEY,
    PIXABAY_API_KEY: process.env.PIXABAY_API_KEY,
    IMAGE_PROVIDER: process.env.IMAGE_PROVIDER,
    DISABLE_IMAGE_GENERATION: process.env.DISABLE_IMAGE_GENERATION,
    EXTENDED_REASONING: process.env.EXTENDED_REASONING,
    TOOL_CALLS: process.env.TOOL_CALLS,
    DISABLE_THINKING: process.env.DISABLE_THINKING,
    WEB_GROUNDING: process.env.WEB_GROUNDING,
    DATABASE_URL: process.env.DATABASE_URL,
    DISABLE_ANONYMOUS_TRACKING: process.env.DISABLE_ANONYMOUS_TRACKING,
    COMFYUI_URL: process.env.COMFYUI_URL,
    COMFYUI_WORKFLOW: process.env.COMFYUI_WORKFLOW,
    DALL_E_3_QUALITY: process.env.DALL_E_3_QUALITY,
    GPT_IMAGE_1_5_QUALITY: process.env.GPT_IMAGE_1_5_QUALITY,
  })

  const [fastApiPort, nextjsPort] = await findUnusedPorts();
  console.log(`FastAPI port: ${fastApiPort}, NextJS port: ${nextjsPort}`);

  //? Setup environment variables to be used in the preloads
  setupEnv(fastApiPort, nextjsPort);
  setupIpcHandlers();
  ipcMain.handle("startup:get-status", () => startupStatus);

  await startServers(fastApiPort, nextjsPort);
  win?.loadURL(`${localhost}:${nextjsPort}`);

  // Begin polling the version server for available updates
  if (win) {
    process.stderr.write("[Presenton] Starting update checker...\n");
    startUpdateChecker(win);
  }
});

app.on("window-all-closed", async () => {
  stopUpdateChecker();
  await stopServers();
  app.quit();
});

app.on("before-quit", async (event) => {
  if (isStopping) return;
  isStopping = true;
  event.preventDefault();
  try {
    await stopServers();
  } finally {
    app.quit();
  }
});

app.on("will-quit", async (event) => {
  if (isStopping) return;
  isStopping = true;
  event.preventDefault();
  try {
    await stopServers();
  } finally {
    app.quit();
  }
});
