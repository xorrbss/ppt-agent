import { spawn, exec } from "child_process";
import util from "util";
import { localhost } from "./constants";

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
    ["server.py", "--port", port.toString()],
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