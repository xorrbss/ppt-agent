import net from 'net'
import treeKill from 'tree-kill'
import fs from 'fs'
import { localhost, tempDir, userConfigPath } from './constants'

export function setUserConfig(userConfig: UserConfig) {
  let existingConfig: UserConfig = {}

  if (fs.existsSync(userConfigPath)) {
    const configData = fs.readFileSync(userConfigPath, 'utf-8')
    existingConfig = JSON.parse(configData)
  }

  const mergedConfig: UserConfig = {
    LLM: userConfig.LLM,
    OPENAI_API_KEY: userConfig.OPENAI_API_KEY,
    GOOGLE_API_KEY: userConfig.GOOGLE_API_KEY
  }
  fs.writeFileSync(userConfigPath, JSON.stringify(mergedConfig))
}

export function getUserConfig(): UserConfig {
  if (!fs.existsSync(userConfigPath)) {
    return {}
  }
  const configData = fs.readFileSync(userConfigPath, 'utf-8')
  return JSON.parse(configData)
}

export function setupEnv(fastApiPort: number, nextjsPort: number) {
  process.env.NEXT_PUBLIC_FAST_API = `${localhost}:${fastApiPort}`;
  process.env.NEXT_PUBLIC_URL = `${localhost}:${nextjsPort}`;
  process.env.TEMP_DIRECTORY = tempDir;
  process.env.NEXT_PUBLIC_USER_CONFIG_PATH = userConfigPath;
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

