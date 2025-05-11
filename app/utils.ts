import net from 'net'
import treeKill from 'tree-kill'
import { exec } from 'child_process'
import { promisify } from 'util'
import { platform } from 'os'
import type { App } from "electron"
import fs from 'fs'
import path from 'path'
import { localhost } from './constants'

const execAsync = promisify(exec)

export function createUserConfig(app: App, userConfig: UserConfig) {
  const dataDir = app.getPath("userData")
  const configPath = path.join(dataDir, "userConfig.json")

  let existingConfig: UserConfig = {}

  if (fs.existsSync(configPath)) {
    const configData = fs.readFileSync(configPath, 'utf-8')
    existingConfig = JSON.parse(configData)
  }

  const mergedConfig: UserConfig = {
    LLM: userConfig.LLM || existingConfig.LLM,
    OPENAI_API_KEY: userConfig.OPENAI_API_KEY || existingConfig.OPENAI_API_KEY,
    GOOGLE_API_KEY: userConfig.GOOGLE_API_KEY || existingConfig.GOOGLE_API_KEY,
  }

  fs.writeFileSync(configPath, JSON.stringify(mergedConfig))
}

export function setupEnv(app: App, fastApiPort: number, nextjsPort: number) {
  process.env.NEXT_PUBLIC_FAST_API = `${localhost}:${fastApiPort}`;
  process.env.NEXT_PUBLIC_URL = `${localhost}:${nextjsPort}`;
  process.env.TEMP_DIRECTORY = app.getPath("temp");
  process.env.NEXT_PUBLIC_USER_CONFIG_PATH = app.getPath("userData") + "/userConfig.json";
}


export function killProcess(pid: number) {
  return new Promise((resolve, reject) => {
    treeKill(pid, "SIGTERM", (err: any) => {
      if (err) {
        console.error(`Error killing process ${pid}:`, err)
        reject(err)
      } else {
        console.log(`Process ${pid} killed`)
        resolve(true)
      }
    })
  })
}

export async function findTwoUnusedPorts(startPort: number = 40000): Promise<[number, number]> {
  const ports: number[] = [];

  const isPortAvailable = (port: number): Promise<boolean> => {
    return new Promise((resolve) => {
      const server = net.createServer();
      server.once('error', () => {
        resolve(false);
      });
      server.once('listening', () => {
        server.close();
        resolve(true);
      });
      server.listen(port);
    });
  };

  let currentPort = startPort;
  while (ports.length < 2) {
    if (await isPortAvailable(currentPort)) {
      ports.push(currentPort);
    }
    currentPort++;
  }

  return [ports[0], ports[1]];
}

