import { spawnSync } from "node:child_process";
import path from "node:path";
import { fileURLToPath } from "node:url";

const scriptDirectory = path.dirname(fileURLToPath(import.meta.url));
const nextjsDirectory = path.resolve(scriptDirectory, "..");
const repositoryDirectory = path.resolve(nextjsDirectory, "../..");
const nextBinary = path.join(
  nextjsDirectory,
  "node_modules",
  "next",
  "dist",
  "bin",
  "next",
);

const childEnvironment = { ...process.env };

if (process.platform === "win32") {
  const distDirectory =
    process.env.PRESENTON_TEST_NEXT_DIST_DIR || ".next-build";
  const fallbackModule = path.join(
    repositoryDirectory,
    "electron",
    "scripts",
    "next-standalone-symlink-fallback.cjs",
  );
  const nodeOptions = [
    process.env.NODE_OPTIONS,
    `--require=${JSON.stringify(fallbackModule)}`,
  ]
    .filter(Boolean)
    .join(" ");

  Object.assign(childEnvironment, {
    NODE_OPTIONS: nodeOptions,
    PRESENTON_NEXT_STANDALONE_LINK_FALLBACK: "1",
    PRESENTON_NEXT_STANDALONE_ROOT: path.resolve(
      nextjsDirectory,
      distDirectory,
      "standalone",
    ),
    PRESENTON_NEXT_TRACE_ROOT: repositoryDirectory,
  });
}

const result = spawnSync(
  process.execPath,
  [nextBinary, "build", ...process.argv.slice(2)],
  {
    cwd: nextjsDirectory,
    env: childEnvironment,
    stdio: "inherit",
    shell: false,
  },
);

if (result.error) {
  console.error(result.error);
  process.exit(1);
}

process.exit(result.status ?? 1);
