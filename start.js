/* This script starts the FastAPI and Next.js servers, setting up user configuration if necessary. It reads environment variables to configure API keys and other settings, ensuring that the user configuration file is created if it doesn't exist. The script also handles the starting of both servers and keeps the Node.js process alive until one of the servers exits. */

import { join, dirname } from "path";
import { fileURLToPath } from "url";
import { spawn } from "child_process";
import { randomUUID } from "crypto";
import {
  chmodSync,
  closeSync,
  copyFileSync,
  existsSync,
  fsyncSync,
  mkdirSync,
  openSync,
  readdirSync,
  readFileSync,
  renameSync,
  unlinkSync,
  writeFileSync,
} from "fs";
import { printPresentonStartupBanner } from "./scripts/presenton-terminal-banner.mjs";
import {
  stopManagedProcesses,
  waitForProcessReady,
} from "./scripts/managed-processes.mjs";

process.umask(0o022);

const __filename = fileURLToPath(import.meta.url);
const __dirname = dirname(__filename);

const fastapiDir = join(__dirname, "servers/fastapi");
const nextjsDir = join(__dirname, "servers/nextjs");
const nextjsStandaloneServer = join(nextjsDir, "server.js");
const exportSyncScript = join(__dirname, "scripts/sync-presentation-export.cjs");
const settingsRegistryPath = join(
  fastapiDir,
  "constants/settings_registry.json"
);
const settingsRegistry = JSON.parse(readFileSync(settingsRegistryPath, "utf8"));
const userConfigSettings = Object.freeze(
  settingsRegistry.settings.filter((setting) =>
    setting.targets.includes("start.user_config")
  )
);

const args = process.argv.slice(2);
const hasDevArg = args.includes("--dev") || args.includes("-d");
const isDev = hasDevArg;
const canChangeKeys = process.env.CAN_CHANGE_KEYS !== "false";
const shouldStartMcpServer = process.env.ENABLE_MCP_SERVER === "true";

const fastapiPort = 8000;
const nextjsPort = 3000;
const appmcpPort = 8001;
/** Must match `listen` in nginx.conf (public HTTP inside the container). */
const nginxListenPort = 80;

const appDataDirectory = process.env.APP_DATA_DIRECTORY;
if (!appDataDirectory) {
  throw new Error("APP_DATA_DIRECTORY is required");
}

const appDataDirectoryMode = 0o755;
const userConfigPath = join(appDataDirectory, "userConfig.json");
const userConfigBackupPath = `${userConfigPath}.bak`;
const userDataDir = dirname(userConfigPath);
const appDataStaticDirectories = [
  "exports",
  "images",
  "uploads",
  "fonts",
  "pptx-to-html",
].map((name) => join(appDataDirectory, name));

const ensureReadableDirectory = (dirPath) => {
  mkdirSync(dirPath, { recursive: true, mode: appDataDirectoryMode });
  chmodSync(dirPath, appDataDirectoryMode);
};

const ensureReadableExportFiles = (dirPath) => {
  for (const entry of readdirSync(dirPath, { withFileTypes: true })) {
    const entryPath = join(dirPath, entry.name);

    if (entry.isDirectory()) {
      chmodSync(entryPath, appDataDirectoryMode);
      ensureReadableExportFiles(entryPath);
    } else if (entry.isFile()) {
      chmodSync(entryPath, 0o644);
    }
  }
};

const ensureAppDataDirectories = () => {
  ensureReadableDirectory(userDataDir);
  for (const dirPath of appDataStaticDirectories) {
    ensureReadableDirectory(dirPath);
  }
  ensureReadableExportFiles(join(appDataDirectory, "exports"));
};

ensureAppDataDirectories();

const readJsonConfig = (filePath) => {
  try {
    if (!existsSync(filePath)) {
      return undefined;
    }
    const raw = readFileSync(filePath, "utf8").trim();
    if (!raw) {
      return {};
    }
    const parsed = JSON.parse(raw);
    return parsed && typeof parsed === "object" && !Array.isArray(parsed)
      ? parsed
      : undefined;
  } catch {
    return undefined;
  }
};

const readUserConfig = () =>
  readJsonConfig(userConfigPath) || readJsonConfig(userConfigBackupPath) || {};

const copyUserConfigBackup = () => {
  try {
    if (readJsonConfig(userConfigPath)) {
      copyFileSync(userConfigPath, userConfigBackupPath);
      chmodSync(userConfigBackupPath, 0o600);
    }
  } catch (error) {
    console.warn("Failed to update user config backup:", error);
  }
};

const writeUserConfig = (config) => {
  ensureReadableDirectory(userDataDir);
  copyUserConfigBackup();

  const tempPath = `${userConfigPath}.${process.pid}.${Date.now()}.${randomUUID()}.tmp`;
  const fd = openSync(tempPath, "w");
  try {
    writeFileSync(fd, JSON.stringify(config), "utf8");
    fsyncSync(fd);
  } finally {
    closeSync(fd);
  }

  try {
    renameSync(tempPath, userConfigPath);
    chmodSync(userConfigPath, 0o600);
    if (!existsSync(userConfigBackupPath)) {
      copyUserConfigBackup();
    }
  } catch (error) {
    try {
      unlinkSync(tempPath);
    } catch {
      // Best-effort cleanup.
    }
    throw error;
  }
};

// Setup node_modules for development
const setupNodeModules = () => {
  return new Promise((resolve, reject) => {
    console.log("Setting up node_modules for Next.js...");
    const npmProcess = spawn("npm", ["install"], {
      cwd: nextjsDir,
      stdio: "inherit",
      env: process.env,
    });

    npmProcess.on("error", (err) => {
      console.error("npm install failed:", err);
      reject(err);
    });

    npmProcess.on("exit", (code) => {
      if (code === 0) {
        console.log("npm install completed successfully");
        resolve();
      } else {
        console.error(`npm install failed with exit code: ${code}`);
        reject(new Error(`npm install failed with exit code: ${code}`));
      }
    });
  });
};

const runCommand = (command, commandArgs, options = {}) => {
  return new Promise((resolve, reject) => {
    const child = spawn(command, commandArgs, {
      cwd: options.cwd || __dirname,
      stdio: options.stdio || "inherit",
      env: options.env || process.env,
    });

    child.on("error", (err) => {
      reject(err);
    });

    child.on("exit", (code) => {
      if (code === 0) {
        resolve();
      } else {
        reject(new Error(`${command} exited with code: ${code}`));
      }
    });
  });
};

const runNodeScript = (scriptPath, scriptArgs) => {
  return runCommand(process.execPath, [scriptPath, ...scriptArgs], {
    cwd: __dirname,
  });
};

const isTruthyEnv = (value) => {
  if (value == null) {
    return false;
  }

  return !["", "0", "false", "no", "off"].includes(
    String(value).trim().toLowerCase()
  );
};

const isOllamaInstalled = () =>
  existsSync("/usr/bin/ollama") || existsSync("/usr/local/bin/ollama");

const shouldStartOllama = () => isTruthyEnv(process.env.START_OLLAMA);

const ensureOllamaRuntime = async () => {
  if (!shouldStartOllama() || isOllamaInstalled()) {
    return;
  }

  console.log("START_OLLAMA=true; installing Ollama runtime...");
  await runCommand("sh", ["-c", "curl -fsSL https://ollama.com/install.sh | sh"], {
    cwd: "/",
  });
};

const ensurePresentationExportRuntime = async () => {
  if (process.env.ENSURE_PRESENTATION_EXPORT_RUNTIME === "false") {
    return;
  }

  if (!existsSync(exportSyncScript)) {
    console.warn("presentation-export sync script not found; skipping runtime check");
    return;
  }

  try {
    await runNodeScript(exportSyncScript, ["--check-only"]);
  } catch (err) {
    if (!isDev) {
      throw new Error(
        "presentation-export runtime is missing in this container image. Rebuild the image so the runtime package is installed."
      );
    }

    console.warn("presentation-export runtime missing in dev mount. Syncing runtime package...");
    await runNodeScript(exportSyncScript, ["--force"]);
  }
};

process.env.USER_CONFIG_PATH = userConfigPath;
// Let Next.js middleware reach FastAPI over the loopback interface inside the
// container without having to bounce through nginx (the host-facing port is
// not reachable from inside the Next.js process).
if (!process.env.FAST_API_INTERNAL_URL) {
  process.env.FAST_API_INTERNAL_URL = `http://127.0.0.1:${fastapiPort}`;
}
// Presentation export renders Next.js pages that reference /app_data assets.
// Route it through nginx so both surfaces share the same authenticated origin.
if (!process.env.NEXT_INTERNAL_URL) {
  process.env.NEXT_INTERNAL_URL = `http://127.0.0.1:${nginxListenPort}`;
}

//? UserConfig is only setup if API Keys can be changed
const setupUserConfigFromEnv = () => {
  let existingConfig = readUserConfig();

  const llmSetting = userConfigSettings.find((setting) => setting.key === "LLM");
  if (!llmSetting.choices.includes(existingConfig.LLM)) {
    existingConfig.LLM = undefined;
  }

  const envValue = (key) => {
    const value = process.env[key];
    return value === undefined || value === "" ? undefined : value;
  };

  const parseBooleanLike = (value) => {
    if (typeof value === "boolean") {
      return value;
    }
    if (typeof value !== "string") {
      return undefined;
    }

    const normalized = value.trim().toLowerCase();
    if (["1", "true", "yes", "on"].includes(normalized)) {
      return true;
    }
    if (["0", "false", "no", "off"].includes(normalized)) {
      return false;
    }
    return undefined;
  };

  const normalizeImageConfig = (config) => {
    const parsedDisableImageGeneration = parseBooleanLike(
      config.DISABLE_IMAGE_GENERATION
    );
    if (parsedDisableImageGeneration !== undefined) {
      config.DISABLE_IMAGE_GENERATION = parsedDisableImageGeneration;
    }

    if (config.DISABLE_IMAGE_GENERATION || config.IMAGE_PROVIDER) {
      return config;
    }

    if (config.COMFYUI_URL) {
      config.IMAGE_PROVIDER = "comfyui";
    } else if (config.PEXELS_API_KEY) {
      config.IMAGE_PROVIDER = "pexels";
    } else if (config.PIXABAY_API_KEY) {
      config.IMAGE_PROVIDER = "pixabay";
    } else if (config.LLM === "openai" && config.OPENAI_API_KEY) {
      config.IMAGE_PROVIDER = "gpt-image-1.5";
      config.GPT_IMAGE_1_5_QUALITY = config.GPT_IMAGE_1_5_QUALITY || "medium";
    } else if (config.LLM === "google" && config.GOOGLE_API_KEY) {
      config.IMAGE_PROVIDER = "gemini_flash";
    } else {
      config.DISABLE_IMAGE_GENERATION = true;
    }

    return config;
  };

  const userConfig = { ...existingConfig };
  for (const setting of userConfigSettings) {
    const value = envValue(setting.key);
    if (value === undefined) {
      continue;
    }
    if (setting.type === "boolean") {
      const parsed = parseBooleanLike(value);
      if (parsed !== undefined) {
        userConfig[setting.key] = parsed;
      }
    } else {
      userConfig[setting.key] = value;
    }
  }

  writeUserConfig(normalizeImageConfig(userConfig));
};

const startServers = async (nginxReadyPromise) => {
  const managedProcesses = [];
  const fastApiEnvironment = {
    ...process.env,
    // Compose already sets this value. Keep direct start.js launches equally
    // safe while preserving an explicit operator override such as "false".
    MIGRATE_DATABASE_ON_STARTUP:
      process.env.MIGRATE_DATABASE_ON_STARTUP ?? "true",
    WORKSPACE_OPERATION_WORKER_MODE:
      process.env.WORKSPACE_OPERATION_WORKER_MODE ?? "external",
  };
  const fastApiProcess = spawn(
    "python",
    [
      "server.py",
      "--port",
      fastapiPort.toString(),
      "--reload",
      isDev ? "true" : "false",
    ],
    {
      cwd: fastapiDir,
      stdio: ["ignore", "pipe", "pipe"],
      env: fastApiEnvironment,
    }
  );
  managedProcesses.push(fastApiProcess);

  fastApiProcess.on("error", (err) => {
    console.error("FastAPI process failed to start:", err);
  });

  if (shouldStartMcpServer) {
    const appmcpProcess = spawn(
      "python",
      [
        "mcp_server.py",
        "--port",
        appmcpPort.toString(),
        "--reload",
        isDev ? "true" : "false",
      ],
      {
        cwd: fastapiDir,
        stdio: "ignore",
        env: process.env,
      }
    );
    managedProcesses.push(appmcpProcess);

    appmcpProcess.on("error", (err) => {
      console.error("App MCP process failed to start:", err);
    });
  } else {
    console.log(
      "MCP server disabled; set ENABLE_MCP_SERVER=true to start it."
    );
  }

  const useStandaloneNextjs = !isDev && existsSync(nextjsStandaloneServer);

  const nextjsProcess = spawn(
    useStandaloneNextjs ? process.execPath : "npm",
    useStandaloneNextjs
      ? [nextjsStandaloneServer]
      : [
          "run",
          isDev ? "dev" : "start",
          "--",
          "-H",
          "127.0.0.1",
          "-p",
          nextjsPort.toString(),
        ],
    {
      cwd: nextjsDir,
      stdio: ["ignore", "pipe", "pipe"],
      env:
        useStandaloneNextjs
          ? {
              ...process.env,
              HOSTNAME: "127.0.0.1",
              PORT: nextjsPort.toString(),
            }
          : process.env,
    }
  );
  managedProcesses.push(nextjsProcess);

  nextjsProcess.on("error", (err) => {
    console.error("Next.js process failed to start:", err);
  });

  const shouldStartOllamaRuntime = shouldStartOllama();
  const ollamaInstalled = isOllamaInstalled();

  const fastApiReadyPromise = waitForProcessReady("FastAPI", fastApiProcess, [
    /Application startup complete\./i,
  ]);
  const nextjsReadyPromise = waitForProcessReady("Next.js", nextjsProcess, [
    /Ready in\s+\d+/i,
    /started server on/i,
  ]);

  const exitPromises = [
    new Promise((resolve) => fastApiProcess.on("exit", (code) => resolve({ name: "FastAPI", code }))),
    new Promise((resolve) => nextjsProcess.on("exit", (code) => resolve({ name: "Next.js", code }))),
  ];

  if (shouldStartOllamaRuntime && ollamaInstalled) {
    const ollamaProcess = spawn("ollama", ["serve"], {
      cwd: "/",
      stdio: "inherit",
      env: process.env,
    });
    managedProcesses.push(ollamaProcess);
    ollamaProcess.on("error", (err) => {
      console.error("Ollama process failed to start:", err);
    });
    exitPromises.push(new Promise((resolve) => ollamaProcess.on("exit", (code) => resolve({ name: "Ollama", code }))));
  } else if (shouldStartOllamaRuntime) {
    console.log(
      "Ollama requested, but the binary is not installed. Set START_OLLAMA=true to install it at startup, or set OLLAMA_URL to a remote daemon."
    );
  } else {
    console.log(
      "Ollama disabled (START_OLLAMA=false); use OLLAMA_URL for a remote daemon if needed."
    );
  }

  try {
    await Promise.all([fastApiReadyPromise, nextjsReadyPromise, nginxReadyPromise]);
    if (fastApiEnvironment.WORKSPACE_OPERATION_WORKER_MODE.trim().toLowerCase() === "external") {
      const workspaceWorkerProcess = spawn(
        "python",
        ["-m", "services.workspace_operation_worker"],
        {
          cwd: fastapiDir,
          stdio: ["ignore", "pipe", "pipe"],
          env: { ...fastApiEnvironment, MIGRATE_DATABASE_ON_STARTUP: "false" },
        }
      );
      managedProcesses.push(workspaceWorkerProcess);
      exitPromises.push(new Promise((resolve) => workspaceWorkerProcess.on("exit", (code) => resolve({ name: "Workspace operation worker", code }))));
      await waitForProcessReady("Workspace operation worker", workspaceWorkerProcess, [
        /Workspace operation external worker started/i,
      ]);
    }
    printPresentonStartupBanner({
      nextPort: nextjsPort,
      fastapiPort,
      nginxInternalPort: nginxListenPort,
    });
  } catch (err) {
    console.error(`Required service failed readiness: ${err.message}`);
    await stopManagedProcesses(managedProcesses);
    throw err;
  }

  // Keep the Node process alive until one of the servers exits
  const exited = await Promise.race(exitPromises);

  console.error(`${exited.name} exited unexpectedly. Exit code: ${exited.code}`);
  await stopManagedProcesses(managedProcesses);
  process.exitCode = typeof exited.code === "number" && exited.code !== 0 ? exited.code : 1;
};

// Start nginx service (reverse proxy: see nginx.conf listen + upstream ports)
const startNginx = () => {
  return new Promise((resolve, reject) => {
    const nginxProcess = spawn("service", ["nginx", "start"], {
      stdio: "inherit",
      env: process.env,
    });

    nginxProcess.on("error", (err) => {
      console.error("Nginx process failed to start:", err);
      reject(err);
    });

    nginxProcess.on("exit", (code) => {
      if (code === 0) {
        console.log("Nginx started successfully");
        resolve(true);
      } else {
        reject(new Error(`Nginx failed to start with exit code: ${code}`));
      }
    });
  });
};

const main = async () => {
  await ensurePresentationExportRuntime();
  await ensureOllamaRuntime();

  if (isDev) {
    await setupNodeModules();
  }

  if (canChangeKeys) {
    setupUserConfigFromEnv();
  }

  const nginxReadyPromise = startNginx();
  await startServers(nginxReadyPromise);
};

main().catch((error) => {
  console.error("Presenton startup failed:", error);
  process.exitCode = 1;
});
