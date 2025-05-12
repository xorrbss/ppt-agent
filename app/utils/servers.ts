import { spawn, exec } from "child_process";
import util from "util";
import { localhost } from "./constants";
import http from "http";

// @ts-ignore
import handler from "serve-handler";

const execAsync = util.promisify(exec);

export async function startFastApiServer(
  directory: string,
  port: number,
  env: FastApiEnv,
  isDev: boolean,
) {
  // Start FastAPI server
  const startCommand = isDev ? [
    ".venv/bin/python",
    ["server_autoreload.py", "--port", port.toString()],
  ] : [
    "./fastapi", ["--port", port.toString()],
  ];


  const fastApiProcess = spawn(
    startCommand[0] as string,
    startCommand[1] as string[],
    {
      cwd: directory,
      stdio: ["inherit", "pipe", "pipe"],
      env: { ...process.env, ...env },
    }
  );
  fastApiProcess.stdout.on("data", (data: any) => {
    console.log(`FastAPI: ${data}`);
  });
  fastApiProcess.stderr.on("data", (data: any) => {
    console.error(`FastAPI Error: ${data}`);
  });
  // Wait for FastAPI server to start
  await execAsync(`npx wait-on ${localhost}:${port}/docs`);
  return fastApiProcess;
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
    nextjsProcess.stdout.on("data", (data: any) => {
      console.log(`NextJS: ${data}`);
    });
    nextjsProcess.stderr.on("data", (data: any) => {
      console.error(`NextJS Error: ${data}`);
    });
  } else {
    // Start NextJS build server
    nextjsProcess = startNextjsBuildServer(directory, port);
  }

  // Wait for NextJS server to start
  await execAsync(`npx wait-on ${localhost}:${port}`);
  return nextjsProcess;
}

async function startNextjsBuildServer(directory: string, port: number) {
  const server = http.createServer((req, res) => {
    return handler(req, res, {
      public: directory,
      cleanUrls: true,
    });
  });

  server.listen(port);
  return server;
}