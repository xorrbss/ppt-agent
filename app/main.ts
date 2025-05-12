require("dotenv").config();
import { app, BrowserWindow } from "electron";
import path from "path";
import { findUnusedPorts, killProcess, setupEnv, setUserConfig } from "./utils";
import { startFastApiServer } from "./utils/servers";
import { ChildProcessByStdio } from "child_process";
import { baseDir, fastapiDir, isDev, tempDir, userConfigPath, userDataDir } from "./utils/constants";
import { setupIpcHandlers } from "./ipc";

var win: BrowserWindow | undefined;
var fastApiProcess: ChildProcessByStdio<any, any, any> | undefined;
var nextjsProcess: ChildProcessByStdio<any, any, any> | undefined;

const createWindow = () => {
  win = new BrowserWindow({
    width: 1280,
    height: 720,
    icon: path.join(baseDir, "resources/ui/assets/images/presenton_short_filled.png"),
    webPreferences: {
      webSecurity: false,
      preload: path.join(__dirname, 'preloads/index.js'),
    },
  });
};

async function startServers(fastApiPort: number) {
  try {
    fastApiProcess = await startFastApiServer(
      fastapiDir,
      fastApiPort,
      {
        DEBUG: isDev ? "True" : "False",
        LLM: process.env.LLM,
        LIBREOFFICE: process.env.LIBREOFFICE,
        OPENAI_API_KEY: process.env.OPENAI_API_KEY,
        GOOGLE_API_KEY: process.env.GOOGLE_API_KEY,
        APP_DATA_DIRECTORY: userDataDir,
        TEMP_DIRECTORY: tempDir,
        USER_CONFIG_PATH: userConfigPath,
      },
      isDev,
    );
  } catch (error) {
    console.error("Server startup error:", error);
  }
}

async function stopServers() {
  if (fastApiProcess?.pid) {
    await killProcess(fastApiProcess.pid);
  }
  if (nextjsProcess?.pid) {
    await killProcess(nextjsProcess.pid);
  }
}

app.whenReady().then(async () => {
  createWindow();
  win?.loadFile(path.join(baseDir, "resources/ui/homepage/index.html"));
  win?.webContents.openDevTools();

  setUserConfig({
    LLM: process.env.LLM,
    OPENAI_API_KEY: process.env.OPENAI_API_KEY,
    GOOGLE_API_KEY: process.env.GOOGLE_API_KEY,
  })

  const [fastApiPort] = await findUnusedPorts();
  console.log(`FastAPI port: ${fastApiPort}`);

  //? Setup environment variables to be used in the preload.ts file
  setupEnv(fastApiPort);
  setupIpcHandlers();

  await startServers(fastApiPort);

  //? Load the NextJS UI
  win?.loadFile(path.join(baseDir, "resources/nextjs/index.html"));
});

app.on("window-all-closed", async () => {
  await stopServers();
  app.quit();
});
