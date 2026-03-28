import fs from "fs";
import path from "path";
import { spawnSync } from "child_process";
import { baseDir, isDev } from "./constants";

export function getLiteParseRunnerPath(): string {
  return isDev
    ? path.join(baseDir, "resources", "document-extraction", "liteparse_runner.mjs")
    : path.join(baseDir, "resources", "document-extraction", "liteparse_runner.mjs");
}

export function getLiteParseDependencyPath(): string {
  return path.join(baseDir, "node_modules", "@llamaindex", "liteparse");
}

export function isLiteParseInstalled(): boolean {
  const runnerPath = getLiteParseRunnerPath();
  const liteparsePackagePath = getLiteParseDependencyPath();

  if (!fs.existsSync(runnerPath)) return false;
  if (!fs.existsSync(liteparsePackagePath)) return false;

  const nodeCheck = spawnSync("node", ["--version"], {
    stdio: "pipe",
    windowsHide: true,
  });
  return nodeCheck.status === 0;
}
