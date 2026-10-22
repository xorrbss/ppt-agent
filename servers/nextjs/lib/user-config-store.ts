import fs from "fs";
import path from "path";

type ConfigSnapshot<T extends object> = {
  config: T;
  primaryValid: boolean;
};

const RETRY_DELAY_MS = 50;
const MAX_IO_ATTEMPTS = 6;

function sleepSync(ms: number): void {
  const end = Date.now() + ms;
  while (Date.now() < end) {
    // Keep lock acquisition synchronous without requiring worker primitives.
  }
}

function isRetryableFsError(error: unknown): boolean {
  const code = (error as NodeJS.ErrnoException | undefined)?.code;
  return code === "EPERM" || code === "EBUSY" || code === "EACCES";
}

function retrySync<T>(label: string, operation: () => T): T {
  let lastError: unknown;

  for (let attempt = 0; attempt < MAX_IO_ATTEMPTS; attempt += 1) {
    try {
      return operation();
    } catch (error) {
      lastError = error;
      if (!isRetryableFsError(error) || attempt === MAX_IO_ATTEMPTS - 1) {
        break;
      }
      sleepSync(RETRY_DELAY_MS * (attempt + 1));
    }
  }

  const message = lastError instanceof Error ? lastError.message : String(lastError);
  throw new Error(`Failed to ${label}: ${message}`);
}

function backupPath(configPath: string): string {
  return `${configPath}.bak`;
}

function ensureParentDirectory(configPath: string): void {
  retrySync("create user config directory", () => {
    fs.mkdirSync(path.dirname(configPath), { recursive: true });
  });
}

function readJsonIfValid<T extends object>(filePath: string): T | undefined {
  try {
    const content = retrySync(`read ${path.basename(filePath)}`, () =>
      fs.readFileSync(filePath, "utf8")
    );
    const trimmed = content.trim();
    if (!trimmed) {
      return {} as T;
    }
    const parsed = JSON.parse(trimmed);
    return parsed && typeof parsed === "object" && !Array.isArray(parsed)
      ? (parsed as T)
      : undefined;
  } catch {
    return undefined;
  }
}

function readSnapshot<T extends object>(configPath: string): ConfigSnapshot<T> {
  const primary = readJsonIfValid<T>(configPath);
  if (primary) {
    return { config: primary, primaryValid: true };
  }

  const backup = readJsonIfValid<T>(backupPath(configPath));
  return { config: backup ?? ({} as T), primaryValid: false };
}

export function readUserConfigFile<T extends object>(configPath: string): T {
  try {
    ensureParentDirectory(configPath);
    return readSnapshot<T>(configPath).config;
  } catch {
    return {} as T;
  }
}
