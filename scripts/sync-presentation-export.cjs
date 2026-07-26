/**
 * Download presenton-export release (Linux x64) into repo-root `presentation-export/`.
 * Same release host as Electron (`electron/sync_export_runtime.js`); Docker uses this at build time.
 *
 * Version resolution defaults to package.json → presentationExportVersion.
 * EXPORT_RUNTIME_VERSION is only honored with --allow-version-override so an
 * ambient build environment cannot silently replace the pinned runtime.
 *
 * CLI: --force  re-download even if valid runtime already exists
 *       --check-only  verify installed version + runtime files and exit 0/1
 *       --allow-version-override  permit EXPORT_RUNTIME_VERSION to replace the pin
 *       --help  show usage
 *
 * Normal sync repairs index.cjs from index.js. --check-only is read-only and
 * rejects a missing or stale CommonJS entrypoint.
 */
const fs = require("fs");
const path = require("path");
const https = require("https");
const http = require("http");
const crypto = require("crypto");
const { execFileSync } = require("child_process");

const repoRoot = path.join(__dirname, "..");
const targetRoot = path.join(repoRoot, "presentation-export");
const targetPyDir = path.join(targetRoot, "py");
const targetIndexJs = path.join(targetRoot, "index.js");
const targetIndexCjs = path.join(targetRoot, "index.cjs");
const installedVersionFileName = ".installed-version";
const installedVersionFile = path.join(targetRoot, installedVersionFileName);
const packageJsonFile = path.join(repoRoot, "package.json");
const cacheDir = path.join(repoRoot, ".cache", "presentation-export");
const exportRepoBase =
  "https://github.com/presenton/presenton-export/releases/download";
// The export runtime publishes one archive per OS/arch. Pick the one matching the
// current platform instead of always fetching Linux — otherwise Windows/macOS get a
// Linux converter binary they can't execute (export then 404s "converter not found").
// Docker/Linux resolves to the same export-Linux-X64.zip as before (no change there).
function getExportAssetName() {
  const osName =
    { win32: "Windows", darwin: "macOS", linux: "Linux" }[process.platform] ||
    "Linux";
  const archName =
    { x64: "X64", arm64: "ARM64", ia32: "ia32" }[process.arch] || "X64";
  return `export-${osName}-${archName}.zip`;
}
const exportAssetName = getExportAssetName();

const helpText = [
  "Usage: node scripts/sync-presentation-export.cjs [options]",
  "",
  "Options:",
  "  --force                   Re-download even when the runtime is valid",
  "  --check-only              Verify the installed runtime without changing it",
  "  --allow-version-override  Honor EXPORT_RUNTIME_VERSION instead of the package pin",
  "  --help                    Show this help",
].join("\n");

function parseCliArgs(args = process.argv.slice(2)) {
  const supported = new Set([
    "--force",
    "--check-only",
    "--allow-version-override",
    "--help",
  ]);
  const unknown = args.filter((arg) => !supported.has(arg));
  if (unknown.length > 0) {
    throw new Error(
      `Unknown option${unknown.length === 1 ? "" : "s"}: ${unknown.join(", ")}\n\n${helpText}`
    );
  }
  const parsed = new Set(args);
  return {
    forceDownload: parsed.has("--force"),
    checkOnly: parsed.has("--check-only"),
    allowVersionOverride: parsed.has("--allow-version-override"),
    showHelp: parsed.has("--help"),
  };
}

function ensureDir(dirPath) {
  fs.mkdirSync(dirPath, { recursive: true });
}

function readInstalledVersion(markerFile = installedVersionFile) {
  if (!fs.existsSync(markerFile)) {
    return null;
  }
  const version = fs.readFileSync(markerFile, "utf8").trim();
  return version || null;
}

function writeInstalledVersionAtomic(
  version,
  markerFile = installedVersionFile
) {
  ensureDir(path.dirname(markerFile));
  const tempFile = `${markerFile}.${process.pid}.${Date.now()}.tmp`;
  try {
    fs.writeFileSync(tempFile, `${version}\n`, "utf8");
    moveFileAtomic(tempFile, markerFile);
  } finally {
    fs.rmSync(tempFile, { force: true });
  }
}

function readPinnedVersion() {
  if (!fs.existsSync(packageJsonFile)) {
    throw new Error(
      `Missing ${path.relative(repoRoot, packageJsonFile)}. Add \"presentationExportVersion\": \"vX.Y.Z\".`
    );
  }
  const raw = JSON.parse(fs.readFileSync(packageJsonFile, "utf8"));
  const v = (raw.presentationExportVersion || "").trim();
  if (!v) {
    throw new Error(
      `${path.relative(repoRoot, packageJsonFile)} must set \"presentationExportVersion\" (e.g. \"v0.2.0\").`
    );
  }
  return v;
}

async function getTargetVersion({
  env = process.env,
  allowOverride = false,
  readPinned = readPinnedVersion,
  resolveLatest = resolveLatestTag,
} = {}) {
  const fromEnv = (env.EXPORT_RUNTIME_VERSION || "").trim();
  if (allowOverride && fromEnv) {
    return fromEnv === "latest" ? await resolveLatest() : fromEnv;
  }
  const pinned = readPinned();
  if (pinned === "latest") {
    return await resolveLatest();
  }
  return pinned;
}

function requestJson(url, redirects = 5) {
  return new Promise((resolve, reject) => {
    const client = url.startsWith("https:") ? https : http;
    const req = client.get(
      url,
      {
        headers: {
          "User-Agent": "presenton-presentation-export-sync",
          Accept: "application/vnd.github+json",
        },
      },
      (res) => {
        if ([301, 302, 303, 307, 308].includes(res.statusCode) && res.headers.location) {
          if (redirects <= 0) {
            reject(new Error(`Too many redirects for JSON request: ${url}`));
            return;
          }
          requestJson(res.headers.location, redirects - 1).then(resolve).catch(reject);
          return;
        }
        if (res.statusCode < 200 || res.statusCode >= 300) {
          reject(new Error(`Failed to fetch ${url}. HTTP ${res.statusCode}`));
          return;
        }
        let payload = "";
        res.setEncoding("utf8");
        res.on("data", (chunk) => {
          payload += chunk;
        });
        res.on("end", () => {
          try {
            resolve(JSON.parse(payload));
          } catch (e) {
            reject(new Error(`Invalid JSON from ${url}: ${e.message}`));
          }
        });
      }
    );
    req.on("error", reject);
  });
}

async function resolveLatestTag() {
  const apiUrl =
    "https://api.github.com/repos/presenton/presenton-export/releases/latest";
  const latest = await requestJson(apiUrl);
  if (!latest.tag_name) {
    throw new Error(`Could not resolve latest tag from ${apiUrl}`);
  }
  return latest.tag_name;
}

function chmodIfPossible(filePath) {
  if (process.platform !== "win32") {
    fs.chmodSync(filePath, 0o755);
  }
}

function getConverterCandidates(baseDir = targetPyDir) {
  const ext = process.platform === "win32" ? ".exe" : "";
  // Match the backend's _resolve_converter_path naming (convert-<platform>-<arch>),
  // e.g. convert-win32-x64.exe on Windows, convert-linux-x64 on Linux. Legacy Linux
  // names are kept so an already-synced older runtime still validates.
  const names = [
    `convert-${process.platform}-${process.arch}${ext}`,
    `convert-${process.platform}${ext}`,
    `convert${ext}`,
    "convert",
    "convert-linux-x64",
    "convert-linux-amd64",
  ];
  return names.map((name) => path.join(baseDir, name));
}

function hasRuntimeBundle(baseDir) {
  const indexPath = path.join(baseDir, "index.js");
  if (!fs.existsSync(indexPath)) {
    return false;
  }

  const pyCandidates = getConverterCandidates(path.join(baseDir, "py"));
  const rootCandidates = getConverterCandidates(baseDir);
  return [...pyCandidates, ...rootCandidates].some((candidate) =>
    fs.existsSync(candidate)
  );
}

function moveFileAtomic(src, dest) {
  try {
    fs.renameSync(src, dest);
  } catch {
    fs.copyFileSync(src, dest);
    fs.rmSync(src, { force: true });
  }
}

function normalizeRuntimeLayout(runtimeRoot = targetRoot) {
  if (!fs.existsSync(runtimeRoot)) {
    return;
  }

  const runtimePyDir = path.join(runtimeRoot, "py");
  ensureDir(runtimePyDir);

  const rootCandidates = getConverterCandidates(runtimeRoot);
  for (const sourcePath of rootCandidates) {
    if (!fs.existsSync(sourcePath)) {
      continue;
    }

    const destinationPath = path.join(runtimePyDir, path.basename(sourcePath));
    if (!fs.existsSync(destinationPath)) {
      moveFileAtomic(sourcePath, destinationPath);
    }
  }
}

function ensureCommonJsEntrypoint(
  runtimeRoot = targetRoot,
  { repair = true } = {}
) {
  const indexJs = path.join(runtimeRoot, "index.js");
  const indexCjs = path.join(runtimeRoot, "index.cjs");
  if (!fs.existsSync(indexJs)) {
    return { ok: false, reason: `Missing runtime bundle: ${indexJs}` };
  }

  if (!repair) {
    if (!fs.existsSync(indexCjs)) {
      return {
        ok: false,
        reason: `Missing CommonJS runtime bundle: ${indexCjs}`,
      };
    }

    try {
      if (!fs.readFileSync(indexJs).equals(fs.readFileSync(indexCjs))) {
        return {
          ok: false,
          reason: `CommonJS runtime bundle does not match ${indexJs}: ${indexCjs}`,
        };
      }
      return { ok: true, entrypointPath: indexCjs };
    } catch (err) {
      return {
        ok: false,
        reason: `Failed to verify CommonJS entrypoint ${indexCjs}: ${err.message}`,
      };
    }
  }

  try {
    fs.copyFileSync(indexJs, indexCjs);
    return { ok: true, entrypointPath: indexCjs };
  } catch (err) {
    return {
      ok: false,
      reason: `Failed to create CommonJS entrypoint ${indexCjs}: ${err.message}`,
    };
  }
}

function validateExistingRuntime(
  expectedVersion,
  runtimeRoot = targetRoot,
  { repair = true } = {}
) {
  const markerFile = path.join(runtimeRoot, installedVersionFileName);
  const installedVersion = readInstalledVersion(markerFile);
  if (!installedVersion) {
    return {
      ok: false,
      reason: `Missing installed-version marker: ${markerFile}`,
    };
  }
  if (installedVersion !== expectedVersion) {
    return {
      ok: false,
      reason:
        `Installed presentation-export version ${installedVersion} does not match ` +
        `requested version ${expectedVersion}.`,
    };
  }

  if (repair) {
    normalizeRuntimeLayout(runtimeRoot);
  }

  const entrypoint = ensureCommonJsEntrypoint(runtimeRoot, { repair });
  if (!entrypoint.ok) {
    return { ok: false, reason: entrypoint.reason };
  }

  const runtimePyDir = path.join(runtimeRoot, "py");
  const candidates = getConverterCandidates(runtimePyDir);
  const converterPath = candidates.find((c) => fs.existsSync(c));
  if (!converterPath) {
    return {
      ok: false,
      reason: `No converter binary under ${runtimePyDir} or ${runtimeRoot}.`,
    };
  }
  if (repair) {
    chmodIfPossible(converterPath);
  }
  return {
    ok: true,
    version: installedVersion,
    entrypointPath: entrypoint.entrypointPath,
    converterPath,
  };
}

function downloadFile(url, outputPath, redirects = 5) {
  return new Promise((resolve, reject) => {
    const client = url.startsWith("https:") ? https : http;
    const req = client.get(
      url,
      {
        headers: {
          "User-Agent": "presenton-presentation-export-sync",
          Accept: "application/octet-stream",
        },
      },
      (res) => {
        if ([301, 302, 303, 307, 308].includes(res.statusCode) && res.headers.location) {
          if (redirects <= 0) {
            reject(new Error(`Too many redirects while downloading ${url}`));
            return;
          }
          downloadFile(res.headers.location, outputPath, redirects - 1)
            .then(resolve)
            .catch(reject);
          return;
        }
        if (res.statusCode < 200 || res.statusCode >= 300) {
          reject(new Error(`Failed to download ${url}. HTTP ${res.statusCode}`));
          return;
        }
        ensureDir(path.dirname(outputPath));
        const fileStream = fs.createWriteStream(outputPath);
        res.pipe(fileStream);
        fileStream.on("finish", () => {
          fileStream.close(resolve);
        });
        fileStream.on("error", reject);
      }
    );
    req.on("error", reject);
  });
}

function unzipArchive(zipPath, destDir) {
  ensureDir(destDir);
  if (process.platform === "win32") {
    // Windows has no `unzip`; use PowerShell's built-in Expand-Archive.
    // Single quotes in paths are escaped by doubling for PowerShell.
    const ps = (p) => p.replace(/'/g, "''");
    execFileSync(
      "powershell",
      [
        "-NoProfile",
        "-NonInteractive",
        "-Command",
        `Expand-Archive -LiteralPath '${ps(zipPath)}' -DestinationPath '${ps(destDir)}' -Force`,
      ],
      { stdio: "inherit" }
    );
  } else {
    execFileSync("unzip", ["-o", zipPath, "-d", destDir], { stdio: "inherit" });
  }
}

function resolveExtractedRoot(extractDir) {
  if (hasRuntimeBundle(extractDir)) {
    return extractDir;
  }

  const children = fs.readdirSync(extractDir, { withFileTypes: true });
  for (const entry of children) {
    if (!entry.isDirectory()) continue;
    const candidate = path.join(extractDir, entry.name);
    if (hasRuntimeBundle(candidate)) {
      return candidate;
    }
  }
  throw new Error(`Unable to locate export runtime root under ${extractDir}`);
}

// The release archive is a version-pinned but mutable network input: GitHub lets a
// tag's asset be re-uploaded. When the compatibility manifest pins a SHA-256 for the
// requested version+asset, verify the downloaded bytes before extracting so a
// swapped or corrupted archive fails closed instead of installing silently.
function expectedAssetSha256(tag, assetName) {
  const manifestPath = path.join(
    repoRoot,
    "compatibility",
    "upstream-compatibility.json"
  );
  let manifest;
  try {
    manifest = JSON.parse(fs.readFileSync(manifestPath, "utf8"));
  } catch {
    return null;
  }
  const runtime = manifest.exportRuntime;
  if (!runtime || runtime.version !== tag) {
    return null;
  }
  const hex = runtime.assets && runtime.assets[assetName];
  return typeof hex === "string" && /^[0-9a-f]{64}$/.test(hex) ? hex : null;
}

function verifyDownloadedAsset(zipPath, tag, assetName) {
  const expected = expectedAssetSha256(tag, assetName);
  if (!expected) {
    console.log(
      `[presentation-export] No pinned checksum for ${assetName}@${tag}; skipping integrity check.`
    );
    return;
  }
  const actual = crypto
    .createHash("sha256")
    .update(fs.readFileSync(zipPath))
    .digest("hex");
  if (actual !== expected) {
    throw new Error(
      `Checksum mismatch for ${assetName}@${tag}: expected ${expected}, got ${actual}. ` +
        "The pinned release asset may have been re-uploaded or corrupted."
    );
  }
  console.log(`[presentation-export] Verified ${assetName} sha256 ${actual}`);
}

async function downloadAndInstallRuntime(tag) {
  const downloadUrl = `${exportRepoBase}/${tag}/${exportAssetName}`;

  ensureDir(cacheDir);
  const zipPath = path.join(cacheDir, exportAssetName);
  const extractDir = path.join(cacheDir, `extract-${Date.now()}`);

  console.log(`[presentation-export] Downloading ${downloadUrl}`);
  await downloadFile(downloadUrl, zipPath);
  verifyDownloadedAsset(zipPath, tag, exportAssetName);

  console.log(`[presentation-export] Extracting ${zipPath}`);
  unzipArchive(zipPath, extractDir);

  const sourceRoot = resolveExtractedRoot(extractDir);
  fs.rmSync(targetRoot, { recursive: true, force: true });
  ensureDir(targetRoot);
  fs.cpSync(sourceRoot, targetRoot, { recursive: true, force: true });

  fs.rmSync(extractDir, { recursive: true, force: true });

  return { tag, downloadUrl };
}

// The prebuilt runtime bundles sharp's JS but not its libvips native addon, and a
// fresh install wipes any previously-installed node_modules. Install sharp next
// to the runtime before recording the installed-version marker.
function detectBundledSharpVersion(runtimeRoot = targetRoot) {
  const fallback = "0.34.4";
  try {
    const bundle = fs.readFileSync(path.join(runtimeRoot, "index.cjs"), "utf8");
    const m = bundle.match(/@img\/sharp-[a-z0-9-]+"\s*:\s*"(\d+\.\d+\.\d+)"/);
    return (m && m[1]) || fallback;
  } catch {
    return fallback;
  }
}

function canLoadRuntimeSharp(runtimeRoot = targetRoot) {
  const sharpRoot = path.join(runtimeRoot, "node_modules", "sharp");
  const packagePath = path.join(sharpRoot, "package.json");
  try {
    const installedVersion = JSON.parse(fs.readFileSync(packagePath, "utf8")).version;
    if (installedVersion !== detectBundledSharpVersion(runtimeRoot)) {
      return false;
    }
    execFileSync(process.execPath, ["-e", `require(${JSON.stringify(sharpRoot)})`], {
      cwd: runtimeRoot,
      stdio: "ignore",
    });
    return true;
  } catch {
    return false;
  }
}

function assertRuntimeSharpLoadable(runtimeRoot = targetRoot) {
  if (!canLoadRuntimeSharp(runtimeRoot)) {
    throw new Error(
      "presentation-export requires a loadable sharp native addon. " +
        "Run `npm run sync:presentation-export` to repair the runtime."
    );
  }
}

function ensureRuntimeSharp() {
  if (canLoadRuntimeSharp()) return;

  const version = detectBundledSharpVersion();
  const installArgs = [
    "install",
    "--prefix",
    targetRoot,
    "--no-save",
    "--no-audit",
    "--no-fund",
    `sharp@${version}`,
  ];
  let npmCmd = "npm";
  let npmArgs = installArgs;
  if (process.platform === "win32") {
    const npmCliCandidates = [
      process.env.npm_execpath,
      path.join(
        path.dirname(process.execPath),
        "node_modules",
        "npm",
        "bin",
        "npm-cli.js"
      ),
    ].filter(Boolean);
    const npmCli = npmCliCandidates.find((candidate) => fs.existsSync(candidate));
    if (!npmCli) {
      throw new Error(
        "Could not locate npm-cli.js for the Windows Sharp installation. " +
          `Install it manually with:\n  npm install --prefix presentation-export --no-save sharp@${version}`
      );
    }
    // Running npm.cmd directly through CreateProcess fails with EINVAL on
    // Windows. Invoke npm's JavaScript entrypoint with the current Node binary.
    npmCmd = process.execPath;
    npmArgs = [npmCli, ...installArgs];
  }
  console.log(
    `[presentation-export] Installing sharp@${version} for byte-PPTX export (${process.platform}/${process.arch})`
  );
  try {
    execFileSync(npmCmd, npmArgs, { stdio: "inherit" });
  } catch (err) {
    throw new Error(
      `Could not install sharp (${err.message}). Install it manually with:\n` +
        `  npm install --prefix presentation-export --no-save sharp@${version}`
    );
  }

  try {
    assertRuntimeSharpLoadable();
  } catch (err) {
    throw new Error(
      `sharp@${version} was installed but cannot be loaded by presentation-export: ${err.message}`
    );
  }
}

function prepareRuntimeArtifacts(runtimeRoot = targetRoot) {
  normalizeRuntimeLayout(runtimeRoot);
  const entrypoint = ensureCommonJsEntrypoint(runtimeRoot);
  if (!entrypoint.ok) {
    throw new Error(entrypoint.reason);
  }
}

function finalizeRuntimeInstall(
  tag,
  {
    prepareRuntime,
    ensureSharp = ensureRuntimeSharp,
    writeMarker = writeInstalledVersionAtomic,
  } = {}
) {
  if (prepareRuntime) {
    prepareRuntime();
  }
  ensureSharp();
  writeMarker(tag);
}

async function main(args = process.argv.slice(2)) {
  const {
    forceDownload,
    checkOnly,
    allowVersionOverride,
    showHelp,
  } = parseCliArgs(args);
  if (showHelp) {
    console.log(helpText);
    return;
  }
  const targetVersion = await getTargetVersion({
    allowOverride: allowVersionOverride,
  });
  const existing = validateExistingRuntime(targetVersion, targetRoot, {
    repair: !checkOnly,
  });

  if (checkOnly) {
    if (!existing.ok) {
      throw new Error(existing.reason);
    }
    assertRuntimeSharpLoadable();
    console.log("[presentation-export] OK");
    console.log(`  - version: ${existing.version}`);
    console.log(`  - ${existing.entrypointPath}`);
    console.log(`  - ${existing.converterPath}`);
    return;
  }

  if (existing.ok && !forceDownload) {
    console.log("[presentation-export] Using existing runtime:");
    console.log(`  - ${existing.entrypointPath}`);
    console.log(`  - ${existing.converterPath}`);
    ensureRuntimeSharp();
    return;
  }

  const { tag, downloadUrl } = await downloadAndInstallRuntime(targetVersion);
  finalizeRuntimeInstall(tag, {
    prepareRuntime: () => prepareRuntimeArtifacts(targetRoot),
  });

  const installed = validateExistingRuntime(targetVersion, targetRoot, {
    repair: false,
  });
  if (!installed.ok) {
    throw new Error(installed.reason);
  }

  console.log("[presentation-export] Synced successfully:");
  console.log(`  - release: ${tag}`);
  console.log(`  - url: ${downloadUrl}`);
  console.log(`  - ${installed.entrypointPath}`);
  console.log(`  - ${installed.converterPath}`);
}

if (require.main === module) {
  main().catch((err) => {
    console.error(`[presentation-export] ${err.message}`);
    process.exit(1);
  });
} else {
  module.exports = {
    installedVersionFileName,
    assertRuntimeSharpLoadable,
    finalizeRuntimeInstall,
    getTargetVersion,
    helpText,
    parseCliArgs,
    readInstalledVersion,
    writeInstalledVersionAtomic,
    validateExistingRuntime,
  };
}
