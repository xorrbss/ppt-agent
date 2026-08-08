import { spawnSync } from "node:child_process";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const scriptDirectory = path.dirname(fileURLToPath(import.meta.url));
const nextjsDirectory = path.resolve(scriptDirectory, "..");
const repositoryDirectory = path.resolve(nextjsDirectory, "../..");

/**
 * Load the repository-root `.env` into the build.
 *
 * Next.js only reads `.env` from its own project directory, but this repo keeps
 * one at the root for the backend, the proxy and the app together.  `NEXT_PUBLIC_*`
 * values are baked into the client bundle at build time, so a build that cannot
 * see them silently ships the feature flags as `undefined` - which is how the
 * chat-first workbench falls back to the legacy popup UI.  Reading it here means
 * `npm run build` cannot produce that bundle by accident.
 *
 * Values already present in the environment win, so CI and explicit overrides
 * still take precedence.
 */
function repositoryEnvironment() {
  const envPath = path.join(repositoryDirectory, ".env");
  let contents;
  try {
    contents = fs.readFileSync(envPath, "utf8");
  } catch (error) {
    if (error.code === "ENOENT") return {};
    throw error;
  }
  const loaded = {};
  for (const line of contents.split(/\r?\n/)) {
    const match = /^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)$/.exec(line);
    if (!match) continue;
    const [, name, rawValue] = match;
    if (process.env[name] !== undefined) continue;
    const value = rawValue.trim();
    loaded[name] =
      (value.startsWith('"') && value.endsWith('"')) ||
      (value.startsWith("'") && value.endsWith("'"))
        ? value.slice(1, -1)
        : value;
  }
  return loaded;
}
const nextBinary = path.join(
  nextjsDirectory,
  "node_modules",
  "next",
  "dist",
  "bin",
  "next",
);

const childEnvironment = { ...repositoryEnvironment(), ...process.env };

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
