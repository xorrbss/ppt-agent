import { spawn } from "child_process";
import { localhost, logsDir, userDataDir } from "./constants";
import http from "http";
import fs from "fs";

// @ts-ignore
import handler from "serve-handler";
import path from "path";

export async function startFastApiServer(
  directory: string,
  port: number,
  env: FastApiEnv,
  isDev: boolean,
) {
  // Start FastAPI server
  let command: string;
  let args: string[];

  if (isDev) {
    command = "uv";
    args = ["run", "python", "server.py", "--port", port.toString(), "--reload", "true"];
  } else {
    const binary = process.platform === "win32" ? "fastapi.exe" : "fastapi";
    command = path.join(directory, binary);
    args = ["--port", port.toString()];
  }

  const safeLog = (data: Buffer | string, logPath: string) => {
    try {
      fs.appendFileSync(logPath, data);
    } catch {
      /* ignore if logs dir not writable */
    }
  };
  const fastapiLogPath = path.join(logsDir, "fastapi-server.log");

  const fastApiProcess = spawn(
    command,
    args,
    {
      cwd: directory,
      stdio: ["inherit", "pipe", "pipe"],
      env: { ...process.env, ...env },
    }
  );
  fastApiProcess.stdout.on("data", (data: any) => {
    safeLog(data, fastapiLogPath);
    console.log(`FastAPI: ${data}`);
  });
  fastApiProcess.stderr.on("data", (data: any) => {
    safeLog(data, fastapiLogPath);
    console.error(`FastAPI: ${data}`);
  });
  fastApiProcess.on("error", (err) => {
    safeLog(`Spawn error: ${err.message}\n`, fastapiLogPath);
  });
  return {
    process: fastApiProcess,
    ready: waitForServer(`${localhost}:${port}/docs`),
  };
}

export async function startNextJsServer(
  directory: string,
  port: number,
  env: NextJsEnv,
  isDev: boolean,
) {
  let nextjsProcess;

  if (isDev) {
    // Start NextJS development server
    nextjsProcess = spawn(
      "npm",
      ["run", "dev", "--", "-p", port.toString()],
      {
        cwd: directory,
        stdio: ["inherit", "pipe", "pipe"],
        env: { ...process.env, ...env },
      }
    );
    const nextjsLogPath = path.join(logsDir, "nextjs-server.log");
    const safeNextLog = (d: Buffer | string) => {
      try {
        fs.appendFileSync(nextjsLogPath, d);
      } catch {
        /* ignore */
      }
    };
    nextjsProcess.stdout.on("data", (data: any) => {
      safeNextLog(data);
      console.log(`NextJS: ${data}`);
    });
    nextjsProcess.stderr.on("data", (data: any) => {
      safeNextLog(data);
      console.error(`NextJS: ${data}`);
    });
  } else {
    // Start NextJS build server
    nextjsProcess = await startNextjsBuildServer(directory, port);
  }

  return {
    process: nextjsProcess,
    ready: waitForServer(`${localhost}:${port}`),
  };
}

function startNextjsBuildServer(directory: string, port: number): Promise<http.Server> {
  return new Promise((resolve, reject) => {
    const server = http.createServer((req, res) => {
      return handler(req, res, {
        public: directory,
        cleanUrls: true,
      });
    });
    server.on("error", reject);
    server.listen(port, () => {
      server.off("error", reject);
      resolve(server);
    });
  });
}


async function waitForServer(url: string, timeout = 30000): Promise<void> {
  const startTime = Date.now();

  while (Date.now() - startTime < timeout) {
    try {
      await new Promise<void>((resolve, reject) => {
        http.get(url, (res) => {
          if (res.statusCode === 200 || res.statusCode === 304) {
            resolve();
          } else {
            reject(new Error(`Unexpected status code: ${res.statusCode}`));
          }
        }).on('error', reject);
      });
      return;
    } catch (error) {
      await new Promise(resolve => setTimeout(resolve, 1000));
    }
  }
  throw new Error(`Server did not start within ${timeout}ms`);
}