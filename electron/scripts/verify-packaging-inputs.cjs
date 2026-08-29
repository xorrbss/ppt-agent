const fs = require("fs");
const path = require("path");
const { spawnSync } = require("child_process");
const { requireReadinessOverlay } = require("./export-readiness-verifier.cjs");
const { validateReleaseCompliance } = require("./release-compliance.cjs");
const { verifyResourceProvenance } = require("./resource-provenance.cjs");
const {
  verifyTailwindRuntimeClosure,
} = require("./next-runtime-dependencies.cjs");

const EXPECTED_EXPORT_VERSION = "v0.4.8";
const OOXML_CONTRACT_RELATIVE = path.join(
  "authored_vnext",
  "ooxml",
  "v1",
  "generated",
  "ooxml_package.py"
);
const AUTHORED_SEMANTIC_CONTRACT_RELATIVE = path.join(
  "authored_vnext",
  "semantic"
);
const TEMPLATE_ENGINE_RELATIVE = path.join(
  "template_package",
  "engines",
  "template-v2-json.v1.json"
);
const TEMPLATE_RENDERER_RELATIVE = path.join(
  "servers",
  "nextjs",
  "lib",
  "template-v2-json-to-html.ts"
);
const DEFAULT_MAX_STANDALONE_FILES = 150000;
const DEFAULT_MAX_STANDALONE_BYTES = 3 * 1024 * 1024 * 1024;
const ALLOWED_STANDALONE_ROOTS = new Set([
  ".source-provenance.json",
  ".presenton-runtime-dependencies.json",
  ".next-build",
  "node_modules",
  "package.json",
  "presentation-export",
  "presentation-templates",
  "public",
  "server.js",
  "servers",
]);

function fail(message) {
  throw new Error(message);
}

function isWithin(parent, child, platform = process.platform) {
  const normalize = (value) => {
    const resolved = path.resolve(value);
    return platform === "win32" ? resolved.toLowerCase() : resolved;
  };
  const normalizedParent = normalize(parent);
  const normalizedChild = normalize(child);
  const relative = path.relative(normalizedParent, normalizedChild);
  return (
    relative === "" ||
    (!relative.startsWith(`..${path.sep}`) &&
      relative !== ".." &&
      !path.isAbsolute(relative))
  );
}

function requireFile(filePath, label, minimumBytes = 1) {
  let stat;
  try {
    stat = fs.statSync(filePath);
  } catch {
    fail(`${label} is missing: ${filePath}`);
  }
  if (!stat.isFile()) {
    fail(`${label} is not a file: ${filePath}`);
  }
  if (stat.size < minimumBytes) {
    fail(`${label} is too small (${stat.size} bytes): ${filePath}`);
  }
  return stat;
}

function directoryHasFile(directory) {
  const pending = [directory];
  while (pending.length > 0) {
    const current = pending.pop();
    let entries;
    try {
      entries = fs.readdirSync(current, { withFileTypes: true });
    } catch {
      return false;
    }
    for (const entry of entries) {
      if (entry.isFile()) return true;
      if (entry.isDirectory() && !entry.isSymbolicLink()) {
        pending.push(path.join(current, entry.name));
      }
    }
  }
  return false;
}

function requirePopulatedDirectory(directory, label) {
  let stat;
  try {
    stat = fs.statSync(directory);
  } catch {
    fail(`${label} is missing: ${directory}`);
  }
  if (!stat.isDirectory()) {
    fail(`${label} is not a directory: ${directory}`);
  }
  if (!directoryHasFile(directory)) {
    fail(`${label} contains no files: ${directory}`);
  }
}

function requireDirectoryByteParity(sourceRoot, resourceRoot, label, rebuildTarget) {
  const inventory = (root, ignoreProvenance) => {
    const files = new Map();
    const pending = [root];
    while (pending.length > 0) {
      const current = pending.pop();
      for (const entry of fs.readdirSync(current, { withFileTypes: true })) {
        const entryPath = path.join(current, entry.name);
        if (entry.isDirectory()) pending.push(entryPath);
        if (entry.isFile()) {
          const relative = path.relative(root, entryPath).split(path.sep).join("/");
          if (ignoreProvenance && relative === ".source-provenance.json") continue;
          files.set(relative, entryPath);
        }
      }
    }
    return files;
  };
  const sourceFiles = inventory(sourceRoot, false);
  const resourceFiles = inventory(resourceRoot, true);
  if (
    sourceFiles.size !== resourceFiles.size ||
    [...sourceFiles.keys()].some((relative) => !resourceFiles.has(relative))
  ) {
    fail(`${label} inventory is stale; rebuild the ${rebuildTarget} resource`);
  }
  for (const [relative, source] of sourceFiles) {
    const resource = resourceFiles.get(relative);
    if (!fs.readFileSync(source).equals(fs.readFileSync(resource))) {
      fail(`${label} file is stale: ${relative}; rebuild the ${rebuildTarget} resource`);
    }
  }
}

function resolveNextStandalone(nextRoot) {
  const direct = path.join(nextRoot, "server.js");
  if (fs.existsSync(direct)) {
    return { serverScript: direct, serverRoot: nextRoot, layout: "direct" };
  }

  const nested = path.join(nextRoot, "servers", "nextjs", "server.js");
  if (fs.existsSync(nested)) {
    return {
      serverScript: nested,
      serverRoot: path.dirname(nested),
      layout: "nested",
    };
  }

  fail(
    `Next.js standalone server is missing. Expected ${direct} or ${nested}`
  );
}

function scanTree(
  root,
  {
    workspaceRoot,
    maxFiles = Number.POSITIVE_INFINITY,
    maxBytes = Number.POSITIVE_INFINITY,
    allowedTopLevel,
    allowLinks = true,
    platform = process.platform,
  }
) {
  const rootStat = fs.lstatSync(root);
  if (rootStat.isSymbolicLink()) {
    fail(`Packaging scan root must not be a link: ${root}`);
  }
  const resolvedWorkspace = fs.realpathSync(workspaceRoot);
  const resolvedRoot = fs.realpathSync(root);
  if (!isWithin(resolvedWorkspace, resolvedRoot, platform)) {
    fail(`Packaging scan root escapes workspace: ${root} -> ${resolvedRoot}`);
  }
  const pending = [root];
  let fileCount = 0;
  let totalBytes = 0;

  if (allowedTopLevel) {
    for (const name of fs.readdirSync(root)) {
      if (!allowedTopLevel.has(name)) {
        fail(`Unexpected standalone root entry: ${path.join(root, name)}`);
      }
    }
  }

  while (pending.length > 0) {
    const current = pending.pop();
    for (const entry of fs.readdirSync(current, { withFileTypes: true })) {
      const entryPath = path.join(current, entry.name);
      const stat = fs.lstatSync(entryPath);

      if (stat.isSymbolicLink()) {
        fileCount += 1;
        let target;
        try {
          target = fs.realpathSync(entryPath);
        } catch (error) {
          fail(`Broken link in packaging input: ${entryPath} (${error.message})`);
        }
        if (!isWithin(resolvedRoot, target, platform)) {
          fail(
            `Packaging input link escapes packaging root: ${entryPath} -> ${target}`
          );
        }
        if (!allowLinks) {
          fail(
            `Packaging input must be link-free for AppX compatibility: ${entryPath}`
          );
        }
      } else if (stat.isDirectory()) {
        pending.push(entryPath);
      } else if (stat.isFile()) {
        fileCount += 1;
        totalBytes += stat.size;
      }

      if (fileCount > maxFiles) {
        fail(
          `Next.js standalone exceeds file limit (${fileCount} > ${maxFiles}): ${root}`
        );
      }
      if (totalBytes > maxBytes) {
        fail(
          `Next.js standalone exceeds size limit (${totalBytes} > ${maxBytes} bytes): ${root}`
        );
      }
    }
  }

  return { fileCount, totalBytes };
}

function detectBinaryFormat(filePath) {
  const header = Buffer.alloc(4);
  const descriptor = fs.openSync(filePath, "r");
  try {
    fs.readSync(descriptor, header, 0, header.length, 0);
  } finally {
    fs.closeSync(descriptor);
  }

  if (
    header[0] === 0x7f &&
    header[1] === 0x45 &&
    header[2] === 0x4c &&
    header[3] === 0x46
  ) {
    return "elf";
  }
  if (header[0] === 0x4d && header[1] === 0x5a) {
    return "pe";
  }

  const magic = header.readUInt32BE(0);
  if (
    magic === 0xfeedface ||
    magic === 0xcefaedfe ||
    magic === 0xfeedfacf ||
    magic === 0xcffaedfe ||
    magic === 0xcafebabe ||
    magic === 0xbebafeca
  ) {
    return "mach-o";
  }
  return "unknown";
}

function expectedBinaryFormat(platform) {
  if (platform === "win32") return "pe";
  if (platform === "linux") return "elf";
  if (platform === "darwin") return "mach-o";
  fail(`Unsupported packaging platform: ${platform}`);
}

function requireCompatibleBinary(filePath, label, platform) {
  requireFile(filePath, label, 4);
  const actual = detectBinaryFormat(filePath);
  const expected = expectedBinaryFormat(platform);
  if (actual !== expected) {
    fail(
      `${label} has incompatible format ${actual}; expected ${expected}: ${filePath}`
    );
  }
}

function converterCandidates(exportRoot, platform, arch) {
  const platformAliases = {
    linux: ["linux"],
    darwin: ["darwin", "macos", "mac"],
    win32: ["win32", "windows", "win"],
  };
  const archAliases = {
    x64: ["x64", "amd64"],
    arm64: ["arm64", "aarch64"],
  };
  const names = [];
  for (const platformName of platformAliases[platform] || [platform]) {
    for (const archName of archAliases[arch] || [arch]) {
      names.push(`convert-${platformName}-${archName}`);
      names.push(`convert-${platformName}-${archName}.exe`);
    }
    names.push(`convert-${platformName}`);
    names.push(`convert-${platformName}.exe`);
  }
  names.push(platform === "win32" ? "convert.exe" : "convert");
  return [...new Set(names)].map((name) =>
    path.join(exportRoot, "py", name)
  );
}

function defaultSharpCheck(root, label) {
  const check = spawnSync(process.execPath, ["-e", "require('sharp')"], {
    cwd: root,
    encoding: "utf8",
    stdio: ["ignore", "pipe", "pipe"],
    windowsHide: true,
  });
  if (check.error) {
    fail(`${label} Sharp native addon check could not start: ${check.error.message}`);
  }
  if (check.status !== 0) {
    const detail = [check.stderr, check.stdout]
      .filter((value) => typeof value === "string" && value.trim())
      .map((value) => value.trim())
      .join("\n");
    fail(
      `${label} Sharp native addon is not loadable (exit ${check.status})` +
        (detail ? `\n${detail}` : "")
    );
  }
}

function positiveLimit(value, fallback, name) {
  if (value === undefined || value === "") return fallback;
  const parsed = Number(value);
  if (!Number.isSafeInteger(parsed) || parsed <= 0) {
    fail(`${name} must be a positive integer, received: ${value}`);
  }
  return parsed;
}

function createStageProfiler(options = {}) {
  const emit = options.onStage || (
    process.env.PRESENTON_PREFLIGHT_PROFILE === "1"
      ? (name, elapsedMs) =>
          console.log(`[packaging-preflight] ${name}: ${elapsedMs}ms`)
      : null
  );
  return (name, operation) => {
    const startedAt = process.hrtime.bigint();
    try {
      return operation();
    } finally {
      emit?.(name, Number(process.hrtime.bigint() - startedAt) / 1_000_000);
    }
  };
}

function validatePackagingInputs(options = {}) {
  const stage = createStageProfiler(options);
  const electronRoot = path.resolve(options.electronRoot || path.join(__dirname, ".."));
  const workspaceRoot = path.resolve(
    options.workspaceRoot || path.join(electronRoot, "..")
  );
  const platform = options.platform || process.platform;
  const arch = options.arch || process.arch;
  const packageFile = path.join(electronRoot, "package.json");
  const packageJson = JSON.parse(fs.readFileSync(packageFile, "utf8"));
  const maxFiles = positiveLimit(
    options.maxFiles ?? process.env.PRESENTON_PREFLIGHT_MAX_FILES,
    DEFAULT_MAX_STANDALONE_FILES,
    "PRESENTON_PREFLIGHT_MAX_FILES"
  );
  const maxBytes = positiveLimit(
    options.maxBytes ?? process.env.PRESENTON_PREFLIGHT_MAX_BYTES,
    DEFAULT_MAX_STANDALONE_BYTES,
    "PRESENTON_PREFLIGHT_MAX_BYTES"
  );

  if (packageJson.exportVersion !== EXPECTED_EXPORT_VERSION) {
    fail(
      `Electron exportVersion must be ${EXPECTED_EXPORT_VERSION}, received ${packageJson.exportVersion}`
    );
  }

  const resourcesRoot = path.join(electronRoot, "resources");
  const electronSourceRoot = path.join(electronRoot, "app");
  const electronBundleRoot = path.join(electronRoot, "app_dist");
  const nextSourceRoot = path.join(workspaceRoot, "servers", "nextjs");
  const fastapiSourceRoot = path.join(workspaceRoot, "servers", "fastapi");
  const nextRoot = path.join(resourcesRoot, "nextjs");
  const exportRoot = path.join(resourcesRoot, "export");
  const fastapiRoot = path.join(resourcesRoot, "fastapi");
  const ooxmlContractSource = path.join(
    workspaceRoot,
    "contracts",
    OOXML_CONTRACT_RELATIVE
  );
  const ooxmlContractResource = path.join(
    resourcesRoot,
    "contracts",
    OOXML_CONTRACT_RELATIVE
  );
  const authoredSemanticContractSource = path.join(
    workspaceRoot,
    "contracts",
    AUTHORED_SEMANTIC_CONTRACT_RELATIVE
  );
  const authoredSemanticContractResource = path.join(
    resourcesRoot,
    "contracts",
    AUTHORED_SEMANTIC_CONTRACT_RELATIVE
  );
  const templateEngineSource = path.join(
    workspaceRoot,
    "contracts",
    TEMPLATE_ENGINE_RELATIVE
  );
  const templateEngineResource = path.join(
    resourcesRoot,
    "contracts",
    TEMPLATE_ENGINE_RELATIVE
  );
  const templatePackagesSource = path.join(workspaceRoot, "templates");
  const templatePackagesResource = path.join(resourcesRoot, "templates");
  const templateRendererSource = path.join(workspaceRoot, TEMPLATE_RENDERER_RELATIVE);
  const templateRendererResource = path.join(
    resourcesRoot,
    TEMPLATE_RENDERER_RELATIVE
  );
  const standalone = resolveNextStandalone(nextRoot);

  const provenanceCheck = options.provenanceCheck || verifyResourceProvenance;
  const compliance = stage("release-compliance", () =>
    (options.complianceCheck || validateReleaseCompliance)(workspaceRoot)
  );
  const provenance = {
    electron: stage("resource-provenance:electron", () => provenanceCheck({
      kind: "electron",
      sourceRoot: electronSourceRoot,
      resourceRoot: electronBundleRoot,
      workspaceRoot,
    })),
    nextjs: stage("resource-provenance:nextjs", () => provenanceCheck({
      kind: "nextjs",
      sourceRoot: nextSourceRoot,
      resourceRoot: nextRoot,
      workspaceRoot,
    })),
    fastapi: stage("resource-provenance:fastapi", () => provenanceCheck({
      kind: "fastapi",
      sourceRoot: fastapiSourceRoot,
      resourceRoot: fastapiRoot,
      workspaceRoot,
    })),
    templatePackages: stage("resource-provenance:template-packages", () => provenanceCheck({
      kind: "template-packages",
      sourceRoot: templatePackagesSource,
      resourceRoot: templatePackagesResource,
      workspaceRoot,
    })),
  };

  requireFile(standalone.serverScript, "Next.js standalone server");
  requirePopulatedDirectory(
    path.join(standalone.serverRoot, ".next-build", "static"),
    "Next.js static assets"
  );
  requirePopulatedDirectory(
    path.join(standalone.serverRoot, "public"),
    "Next.js public assets"
  );
  const nextRuntimeDependencies = stage("next-runtime-dependency-closure", () =>
    verifyTailwindRuntimeClosure({
      nextjsRoot: nextSourceRoot,
      serverRoot: standalone.serverRoot,
    })
  );

  const standaloneStats = stage("next-standalone-scan", () => scanTree(nextRoot, {
    workspaceRoot,
    maxFiles,
    maxBytes,
    allowedTopLevel: ALLOWED_STANDALONE_ROOTS,
    allowLinks: false,
    platform,
  }));

  const markerFile = path.join(exportRoot, ".installed-version");
  requireFile(markerFile, "Export runtime marker");
  const installedVersion = fs.readFileSync(markerFile, "utf8").trim();
  if (installedVersion !== EXPECTED_EXPORT_VERSION) {
    fail(
      `Export runtime marker must be ${EXPECTED_EXPORT_VERSION}, received ${installedVersion || "(empty)"}`
    );
  }
  const exportIndex = path.join(exportRoot, "index.js");
  requireFile(exportIndex, "Export runtime bundle", 512);
  requireReadinessOverlay(
    fs.readFileSync(exportIndex, "utf8"),
    "Export runtime bundle"
  );
  const converter = converterCandidates(exportRoot, platform, arch).find((candidate) =>
    fs.existsSync(candidate)
  );
  if (!converter) {
    fail(`Export converter is missing for ${platform}/${arch}: ${exportRoot}`);
  }
  requireCompatibleBinary(converter, "Export converter", platform);

  const fastapiBinary = path.join(
    fastapiRoot,
    platform === "win32" ? "fastapi.exe" : "fastapi"
  );
  requireCompatibleBinary(fastapiBinary, "FastAPI executable", platform);
  const workspaceWorkerBinary = path.join(
    fastapiRoot,
    platform === "win32"
      ? "workspace-operation-worker.exe"
      : "workspace-operation-worker"
  );
  requireCompatibleBinary(
    workspaceWorkerBinary,
    "Workspace operation worker executable",
    platform
  );
  requireFile(ooxmlContractSource, "Authored OOXML contract source", 100);
  requireFile(ooxmlContractResource, "Packaged authored OOXML contract", 100);
  if (
    !fs.readFileSync(ooxmlContractSource).equals(fs.readFileSync(ooxmlContractResource))
  ) {
    fail("Packaged authored OOXML contract is stale; rebuild the FastAPI resource");
  }
  stage("authored-semantic-contract-byte-parity", () => requireDirectoryByteParity(
    authoredSemanticContractSource,
    authoredSemanticContractResource,
    "Packaged authored semantic contract tree",
    "FastAPI"
  ));
  requireFile(templateEngineSource, "Template package engine source", 100);
  requireFile(templateEngineResource, "Packaged template package engine", 100);
  if (
    !fs.readFileSync(templateEngineSource).equals(fs.readFileSync(templateEngineResource))
  ) {
    fail("Packaged template package engine is stale; rebuild the FastAPI resource");
  }
  requireFile(templateRendererSource, "Template package renderer evidence source", 100);
  requireFile(templateRendererResource, "Packaged template package renderer evidence", 100);
  if (
    !fs.readFileSync(templateRendererSource).equals(fs.readFileSync(templateRendererResource))
  ) {
    fail("Packaged template package renderer evidence is stale; rebuild the FastAPI resource");
  }
  stage("template-package-byte-parity", () => requireDirectoryByteParity(
    templatePackagesSource,
    templatePackagesResource,
    "Packaged template package tree",
    "FastAPI"
  ));
  const packagedRegistry = JSON.parse(
    fs.readFileSync(path.join(templatePackagesResource, "package-registry.v1.json"), "utf8")
  );
  if (packagedRegistry.legacyCompatibility?.enabled !== false) {
    fail("Packaged template registry must keep legacyCompatibility.enabled=false");
  }

  stage("export-runtime-scan", () => scanTree(exportRoot, { workspaceRoot, platform }));
  stage("fastapi-runtime-scan", () => scanTree(fastapiRoot, {
    workspaceRoot,
    allowLinks: false,
    platform,
  }));
  stage("nextjs-sharp", () =>
    (options.nextSharpCheck ||
      ((root) => defaultSharpCheck(root, "Next.js")))(standalone.serverRoot)
  );
  stage("electron-sharp", () =>
    (options.sharpCheck ||
      ((root) => defaultSharpCheck(root, "Electron")))(electronRoot)
  );

  return {
    exportVersion: installedVersion,
    converter,
    fastapiBinary,
    workspaceWorkerBinary,
    nextLayout: standalone.layout,
    nextServer: standalone.serverScript,
    provenance,
    compliance,
    nextRuntimeDependencies,
    ...standaloneStats,
  };
}

function main() {
  try {
    const result = validatePackagingInputs();
    console.log("[packaging-preflight] OK");
    console.log(`  - Next.js: ${result.nextLayout} (${result.nextServer})`);
    console.log(
      `  - standalone: ${result.fileCount} files, ${result.totalBytes} bytes`
    );
    console.log(`  - export: ${result.exportVersion} (${result.converter})`);
    console.log(`  - FastAPI: ${result.fastapiBinary}`);
    console.log(`  - Workspace worker: ${result.workspaceWorkerBinary}`);
  } catch (error) {
    console.error(`[packaging-preflight] ${error.message}`);
    process.exitCode = 1;
  }
}

if (require.main === module) {
  main();
} else {
  module.exports = {
    ALLOWED_STANDALONE_ROOTS,
    AUTHORED_SEMANTIC_CONTRACT_RELATIVE,
    EXPECTED_EXPORT_VERSION,
    OOXML_CONTRACT_RELATIVE,
    TEMPLATE_ENGINE_RELATIVE,
    TEMPLATE_RENDERER_RELATIVE,
    detectBinaryFormat,
    createStageProfiler,
    isWithin,
    resolveNextStandalone,
    scanTree,
    validatePackagingInputs,
  };
}
