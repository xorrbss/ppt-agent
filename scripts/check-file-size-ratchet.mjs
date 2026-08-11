import { execFile } from "node:child_process";
import { existsSync } from "node:fs";
import { readFile, readdir, writeFile } from "node:fs/promises";
import { promisify } from "node:util";
import path from "node:path";
import { pathToFileURL } from "node:url";

export const LINE_LIMIT = 500;
export const BASELINE_FILE = "scripts/file-size-ratchet-baseline.json";

const SOURCE_EXTENSIONS = new Set([
  ".cjs",
  ".js",
  ".jsx",
  ".mjs",
  ".py",
  ".ts",
  ".tsx",
]);
const EXCLUDED_DIRECTORY_NAMES = new Set([
  ".git",
  ".next",
  ".next-build",
  ".cache",
  ".agents",
  ".codex-tmp",
  ".do",
  ".local-runtime-logs",
  ".pytest-tmp-lifespan",
  ".ruff_cache",
  ".server-logs-main",
  ".server-logs-slidespec",
  ".test-runtime",
  ".tmp",
  "__pycache__",
  "app_data",
  "build",
  "coverage",
  "cypress",
  "dist",
  "document-extraction-liteparse",
  "node_modules",
  "presentation-export",
  "readme_assets",
  "test-artifacts",
  "test-results",
  "tests",
  "__tests__",
  "fixtures",
  "fastembed_cache",
  "tessdata",
  "vendor",
  "migrations",
]);
const execFileAsync = promisify(execFile);

function toRepoPath(root, filePath) {
  return path.relative(root, filePath).split(path.sep).join("/");
}

function isExcluded(relativePath) {
  const segments = relativePath.split("/");
  if (segments.some((segment) => EXCLUDED_DIRECTORY_NAMES.has(segment) || segment.startsWith(".next-"))) {
    return true;
  }
  if (relativePath.startsWith("servers/fastapi/alembic/versions/")) {
    return true;
  }
  return (
    /\.(?:test|spec|cy)\.[^.]+$/u.test(relativePath) ||
    relativePath.includes(".generated.") ||
    /_generated\.[^.]+$/u.test(relativePath)
  );
}

function lineCount(source) {
  if (source.length === 0) return 0;
  return source.replaceAll("\r\n", "\n").split("\n").length - (source.endsWith("\n") ? 1 : 0);
}

async function collectSourceFiles(root, directory = root, files = []) {
  for (const entry of await readdir(directory, { withFileTypes: true })) {
    const filePath = path.join(directory, entry.name);
    const relativePath = toRepoPath(root, filePath);
    if (entry.isDirectory()) {
      if (!isExcluded(`${relativePath}/`)) await collectSourceFiles(root, filePath, files);
      continue;
    }
    if (
      entry.isFile() &&
      SOURCE_EXTENSIONS.has(path.extname(entry.name)) &&
      !isExcluded(relativePath)
    ) {
      files.push({ path: relativePath, lines: lineCount(await readFile(filePath, "utf8")) });
    }
  }
  return files;
}

async function collectGitSourceFiles(root) {
  const { stdout } = await execFileAsync("git", ["ls-files", "--cached", "--others", "--exclude-standard"], {
    cwd: root,
    maxBuffer: 10 * 1024 * 1024,
  });
  const files = [];
  for (const relativePath of stdout.split(/\r?\n/u).filter(Boolean)) {
    const normalizedPath = relativePath.replaceAll("\\", "/");
    if (!SOURCE_EXTENSIONS.has(path.extname(normalizedPath)) || isExcluded(normalizedPath)) continue;
    const filePath = path.join(root, relativePath);
    // `git ls-files` keeps deleted tracked paths until the deletion is staged.
    // A working-tree ratchet must evaluate the files that actually exist.
    if (!existsSync(filePath)) continue;
    files.push({ path: normalizedPath, lines: lineCount(await readFile(filePath, "utf8")) });
  }
  return files;
}

export async function oversizedFiles(root) {
  const files = existsSync(path.join(root, ".git"))
    ? await collectGitSourceFiles(root)
    : await collectSourceFiles(root);
  return files.filter((file) => file.lines > LINE_LIMIT).sort((a, b) => a.path.localeCompare(b.path));
}

export async function buildBaseline(root) {
  const files = await oversizedFiles(root);
  return {
    lineLimit: LINE_LIMIT,
    files: Object.fromEntries(files.map((file) => [file.path, file.lines])),
  };
}

function baselineAllowance(entry) {
  return Number.isInteger(entry) ? entry : entry?.lines;
}

function validDocumentedException(entry) {
  return (
    typeof entry === "object" &&
    entry !== null &&
    Number.isInteger(entry.lines) &&
    [entry.rationale, entry.owner, entry.removalCondition].every(
      (value) => typeof value === "string" && value.trim().length > 0
    )
  );
}

export async function checkRatchet(root, baselinePath = path.join(root, BASELINE_FILE)) {
  const baseline = JSON.parse(await readFile(baselinePath, "utf8"));
  if (baseline.lineLimit !== LINE_LIMIT || !baseline.files || Array.isArray(baseline.files)) {
    throw new Error(`Invalid file-size baseline: ${toRepoPath(root, baselinePath)}`);
  }
  const current = await buildBaseline(root);
  const failures = [];
  for (const [filePath, entry] of Object.entries(baseline.files)) {
    const allowedLines = baselineAllowance(entry);
    if (
      !Number.isInteger(allowedLines) ||
      allowedLines < LINE_LIMIT + 1 ||
      filePath.includes("\\") ||
      path.posix.isAbsolute(filePath) ||
      filePath.split("/").includes("..")
    ) {
      failures.push(`invalid baseline entry: ${filePath}`);
    } else if (typeof entry === "object" && !validDocumentedException(entry)) {
      failures.push(`undocumented baseline exception: ${filePath}`);
    } else if (current.files[filePath] === undefined) {
      failures.push(`baseline entry no longer maps to an oversized source: ${filePath}`);
    }
  }
  for (const [filePath, lines] of Object.entries(current.files)) {
    const entry = baseline.files[filePath];
    const allowedLines = baselineAllowance(entry);
    if (entry === undefined) failures.push(`new oversized file: ${filePath} (${lines} lines)`);
    else if (!Number.isInteger(allowedLines) || allowedLines < LINE_LIMIT + 1) failures.push(`invalid baseline entry: ${filePath}`);
    else if (lines > allowedLines) failures.push(`oversized file grew: ${filePath} (${allowedLines} -> ${lines} lines)`);
  }
  return failures;
}

async function main() {
  const args = process.argv.slice(2);
  const rootOption = args.indexOf("--root");
  const root = rootOption === -1 ? path.resolve(import.meta.dirname, "..") : path.resolve(args[rootOption + 1]);
  const baselineOption = args.indexOf("--baseline");
  const baselinePath = baselineOption === -1 ? path.join(root, BASELINE_FILE) : path.resolve(root, args[baselineOption + 1]);
  if (args.includes("--write-baseline")) {
    await writeFile(baselinePath, `${JSON.stringify(await buildBaseline(root), null, 2)}\n`);
    return;
  }
  const failures = await checkRatchet(root, baselinePath);
  if (failures.length) throw new Error(`500-line file-size ratchet failed:\n- ${failures.join("\n- ")}`);
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  main().catch((error) => {
    console.error(error.message);
    process.exitCode = 1;
  });
}
