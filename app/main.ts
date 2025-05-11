require("dotenv").config();
import { app, BrowserWindow } from "electron";
import path from "path";
import { createUserConfig, findTwoUnusedPorts, killProcess, setupEnv } from "./utils";
import { startFastApiServer, startNextJsServer } from "./servers";
import { ChildProcessByStdio } from "child_process";
import { localhost } from "./constants";

var isDev = !app.isPackaged;
// var isDev = false;
var baseDir = app.getAppPath();
var fastapiDir = isDev ? path.join(baseDir, "servers/fastapi") : path.join(baseDir, "resources/fastapi");
var nextjsDir = isDev ? path.join(baseDir, "servers/nextjs") : path.join(baseDir, "resources/nextjs");

var libreofficePath = path.join(baseDir, "dependencies/libreoffice/linux_build/libreoffice.appimage");

var tempDir = app.getPath("temp");
var dataDir = app.getPath("userData");
var userConfigPath = path.join(dataDir, "userConfig.json");

var win: BrowserWindow | undefined;
var fastApiProcess: ChildProcessByStdio<any, any, any> | undefined;
var nextjsProcess: ChildProcessByStdio<any, any, any> | undefined;

const createWindow = () => {
  win = new BrowserWindow({
    webPreferences: {
      webSecurity: false,
      preload: path.join(__dirname, 'preload.js'),
    },
    width: 1280,
    height: 720,
  });
};

async function startServers(fastApiPort: number, nextjsPort: number) {
  try {
    fastApiProcess = await startFastApiServer(
      fastapiDir,
      fastApiPort,
      {
        DEBUG: isDev ? "True" : "False",
        LLM: process.env.LLM,
        LIBREOFFICE: libreofficePath,
        OPENAI_API_KEY: process.env.OPENAI_API_KEY,
        GOOGLE_API_KEY: process.env.GOOGLE_API_KEY,
        APP_DATA_DIRECTORY: dataDir,
        TEMP_DIRECTORY: tempDir,
        USER_CONFIG_PATH: userConfigPath,
      },
      isDev,
    );
    nextjsProcess = await startNextJsServer(
      nextjsDir,
      nextjsPort,
      {
        NEXT_PUBLIC_FAST_API: process.env.NEXT_PUBLIC_FAST_API,
        TEMP_DIRECTORY: process.env.TEMP_DIRECTORY,
        NEXT_PUBLIC_URL: process.env.NEXT_PUBLIC_URL,
        NEXT_PUBLIC_USER_CONFIG_PATH: process.env.NEXT_PUBLIC_USER_CONFIG_PATH,
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

  createUserConfig(app, {
    LLM: process.env.LLM,
    OPENAI_API_KEY: process.env.OPENAI_API_KEY,
    GOOGLE_API_KEY: process.env.GOOGLE_API_KEY,
  })

  const [fastApiPort, nextjsPort] = await findTwoUnusedPorts();
  console.log(`FastAPI port: ${fastApiPort}, NextJS port: ${nextjsPort}`);

  //? Setup environment variables to be used in the preload.ts file
  setupEnv(app, fastApiPort, nextjsPort);

  await startServers(fastApiPort, nextjsPort);
  win?.loadURL(`${localhost}:${nextjsPort}`);
});

app.on("window-all-closed", async () => {
  await stopServers();
  app.quit();
});
