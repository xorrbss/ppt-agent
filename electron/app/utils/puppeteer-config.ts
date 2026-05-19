import fs from "fs";
import os from "os";
import path from "path";
import puppeteer from "puppeteer";
import { getCacheDir } from "./constants";
import { safeWarn } from "./safe-console";

type MutablePuppeteerConfig = {
  cacheDirectory?: string;
  executablePath?: string;
  skipDownload?: boolean;
};

type PuppeteerRuntime = typeof puppeteer & {
  configuration?: MutablePuppeteerConfig;
  defaultDownloadPath?: string;
};

function normalizePathCandidate(value: unknown): string | undefined {
  if (typeof value !== "string") return undefined;
  const trimmed = value.trim();
  return trimmed ? path.resolve(trimmed) : undefined;
}

function safePathCandidate(factory: () => string | undefined): string | undefined {
  try {
    return normalizePathCandidate(factory());
  } catch {
    return undefined;
  }
}

function usableDirectory(candidate: string): string | undefined {
  try {
    fs.mkdirSync(candidate, { recursive: true });
    fs.accessSync(candidate, fs.constants.W_OK);
    return candidate;
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error);
    safeWarn(`[Puppeteer] Ignoring unusable cache directory ${candidate}: ${message}`);
    return undefined;
  }
}

export function getPuppeteerRuntime(): PuppeteerRuntime {
  return puppeteer as PuppeteerRuntime;
}

export function getPuppeteerCacheDir(): string {
  const runtime = getPuppeteerRuntime();
  const candidates = [
    process.env.PUPPETEER_CACHE_DIR,
    runtime.configuration?.cacheDirectory,
    runtime.defaultDownloadPath,
    safePathCandidate(() => path.join(getCacheDir(), "puppeteer")),
    safePathCandidate(() => {
      const home = os.homedir();
      return home ? path.join(home, ".cache", "puppeteer") : undefined;
    }),
    safePathCandidate(() => path.join(os.tmpdir(), "presenton-puppeteer")),
  ];

  for (const candidate of candidates) {
    const normalized = normalizePathCandidate(candidate);
    if (!normalized) continue;
    const usable = usableDirectory(normalized);
    if (!usable) continue;

    if (!runtime.configuration) {
      runtime.configuration = {};
    }
    runtime.configuration.cacheDirectory = usable;
    process.env.PUPPETEER_CACHE_DIR = usable;
    return usable;
  }

  throw new Error("Unable to resolve a writable Puppeteer cache directory.");
}
