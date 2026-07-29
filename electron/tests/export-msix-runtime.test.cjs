const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const test = require("node:test");

const electronRoot = path.join(__dirname, "..");
const compiledModulePath = path.join(
  electronRoot,
  "app_dist",
  "utils",
  "export-msix-runtime.js"
);
const compiledConstantsPath = path.join(
  electronRoot,
  "app_dist",
  "utils",
  "constants.js"
);
const compiledSafeConsolePath = path.join(
  electronRoot,
  "app_dist",
  "utils",
  "safe-console.js"
);

function stubCompiledModule(modulePath, exports) {
  require.cache[modulePath] = {
    id: modulePath,
    filename: modulePath,
    loaded: true,
    exports,
    children: [],
    paths: [],
  };
}

function loadRuntimeModule() {
  assert.ok(
    fs.existsSync(compiledModulePath),
    "Run `npm run build:ts` before the MSIX export runtime tests."
  );
  delete require.cache[compiledModulePath];
  stubCompiledModule(compiledConstantsPath, {
    baseDir: electronRoot,
    getCacheDir: () => path.join(os.tmpdir(), "presenton-test-cache"),
  });
  stubCompiledModule(compiledSafeConsolePath, {
    safeLog: () => {},
  });
  return require(compiledModulePath);
}

function writePackage(modulesRoot, packageName, version) {
  const packageRoot = packageName.startsWith("@")
    ? path.join(modulesRoot, ...packageName.split("/"))
    : path.join(modulesRoot, packageName);
  fs.mkdirSync(packageRoot, { recursive: true });
  fs.writeFileSync(
    path.join(packageRoot, "package.json"),
    JSON.stringify({ name: packageName, version })
  );
  return packageRoot;
}

test("selects only the packaged export runtime node_modules directory and cache v2", () => {
  const {
    getMsixExportCacheRoot,
    getPackagedExportModulesRoot,
  } = loadRuntimeModule();
  const packagedExportRoot = path.join(
    "C:",
    "Program Files",
    "WindowsApps",
    "Presenton",
    "resources",
    "export"
  );

  assert.equal(
    getPackagedExportModulesRoot(packagedExportRoot),
    path.join(packagedExportRoot, "node_modules")
  );
  assert.match(
    getMsixExportCacheRoot("v0.4.2"),
    /msix-export-runtime[\\/]2[\\/]v0\.4\.2$/
  );
});

test("rejects a platform Sharp addon whose version differs from sharp", async () => {
  const {
    assertSharpRuntimeVersionParity,
    materializeMsixExportRuntime,
  } = loadRuntimeModule();
  const fixtureRoot = fs.mkdtempSync(path.join(os.tmpdir(), "presenton-msix-mismatch-"));
  const exportRoot = path.join(fixtureRoot, "packaged-export");
  const modulesRoot = path.join(fixtureRoot, "node_modules");
  const cacheRoot = path.join(fixtureRoot, "existing-cache");
  const packages = ["sharp", "@img/sharp-win32-x64"];

  try {
    fs.mkdirSync(exportRoot, { recursive: true });
    fs.writeFileSync(path.join(exportRoot, "index.js"), "module.exports = {};\n");
    fs.mkdirSync(cacheRoot, { recursive: true });
    fs.writeFileSync(path.join(cacheRoot, "sentinel"), "keep");
    writePackage(modulesRoot, "sharp", "0.34.4");
    writePackage(modulesRoot, "@img/sharp-win32-x64", "0.35.3");

    await assert.rejects(
      assertSharpRuntimeVersionParity(modulesRoot, packages),
      /sharp@0\.34\.4 does not match @img\/sharp-win32-x64@0\.35\.3/
    );
    await assert.rejects(
      materializeMsixExportRuntime(exportRoot, modulesRoot, cacheRoot, packages),
      /sharp@0\.34\.4 does not match @img\/sharp-win32-x64@0\.35\.3/
    );
    assert.equal(
      fs.readFileSync(path.join(cacheRoot, "sentinel"), "utf8"),
      "keep",
      "source validation must fail before replacing an existing cache"
    );
  } finally {
    fs.rmSync(fixtureRoot, { recursive: true, force: true });
  }
});

test("copies a version-matched isolated Sharp runtime and validates the result", async () => {
  const {
    assertSharpRuntimeVersionParity,
    isMsixExportCacheCurrent,
    materializeMsixExportRuntime,
  } = loadRuntimeModule();
  const fixtureRoot = fs.mkdtempSync(path.join(os.tmpdir(), "presenton-msix-copy-"));
  const exportRoot = path.join(fixtureRoot, "packaged-export");
  const modulesRoot = path.join(exportRoot, "node_modules");
  const cacheRoot = path.join(fixtureRoot, "cache");
  const packages = [
    "sharp",
    "@img/sharp-win32-x64",
    "@img/colour",
    "detect-libc",
    "semver",
  ];

  try {
    fs.mkdirSync(exportRoot, { recursive: true });
    fs.writeFileSync(path.join(exportRoot, "index.js"), "module.exports = {};\n");
    for (const packageName of packages) {
      const packageRoot = writePackage(modulesRoot, packageName, "0.34.4");
      if (packageName === "@img/sharp-win32-x64") {
        const libRoot = path.join(packageRoot, "lib");
        fs.mkdirSync(libRoot, { recursive: true });
        fs.writeFileSync(path.join(libRoot, "sharp-win32-x64.node"), "fixture");
      }
    }

    await materializeMsixExportRuntime(
      exportRoot,
      modulesRoot,
      cacheRoot,
      packages
    );

    assert.equal(
      await assertSharpRuntimeVersionParity(
        path.join(cacheRoot, "node_modules"),
        packages
      ),
      "0.34.4"
    );
    assert.equal(
      JSON.parse(
        fs.readFileSync(
          path.join(cacheRoot, "node_modules", "sharp", "package.json"),
          "utf8"
        )
      ).version,
      "0.34.4"
    );
    assert.ok(fs.existsSync(path.join(cacheRoot, ".source-fingerprint")));

    const expectedFingerprint = fs
      .readFileSync(path.join(cacheRoot, ".source-fingerprint"), "utf8")
      .trim();
    assert.equal(
      await isMsixExportCacheCurrent(cacheRoot, expectedFingerprint, packages),
      true
    );

    writePackage(
      path.join(cacheRoot, "node_modules"),
      "@img/sharp-win32-x64",
      "0.35.3"
    );
    await assert.rejects(
      isMsixExportCacheCurrent(cacheRoot, expectedFingerprint, packages),
      /sharp@0\.34\.4 does not match @img\/sharp-win32-x64@0\.35\.3/
    );
  } finally {
    fs.rmSync(fixtureRoot, { recursive: true, force: true });
  }
});
