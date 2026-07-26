const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const test = require("node:test");

const {
  installedVersionFileName,
  assertRuntimeSharpLoadable,
  finalizeRuntimeInstall,
  getTargetVersion,
  helpText,
  parseCliArgs,
  readInstalledVersion,
  writeInstalledVersionAtomic,
  validateExistingRuntime,
} = require("./sync-presentation-export.cjs");
const {
  assertElectronSharpLoadable,
  finalizeRuntimeInstall: finalizeElectronRuntimeInstall,
  validateExistingRuntime: validateExistingElectronRuntime,
  writeInstalledVersionAtomic: writeElectronInstalledVersionAtomic,
} = require("../electron/sync_export_runtime.js");

const rootPackage = require("../package.json");
const electronPackage = require("../electron/package.json");

test("CLI accepts only documented presentation-export sync options", () => {
  assert.deepEqual(parseCliArgs(["--force", "--allow-version-override"]), {
    forceDownload: true,
    checkOnly: false,
    allowVersionOverride: true,
    showHelp: false,
  });
  assert.throws(
    () => parseCliArgs(["--allow-ambient-override"]),
    /Unknown option: --allow-ambient-override/
  );
  assert.match(helpText, /--allow-version-override\s+Honor EXPORT_RUNTIME_VERSION/);
});

test("ambient export version override is ignored without explicit opt-in", async () => {
  let latestRequests = 0;
  const version = await getTargetVersion({
    env: { EXPORT_RUNTIME_VERSION: "latest" },
    allowOverride: false,
    readPinned: () => "v0.4.2",
    resolveLatest: async () => {
      latestRequests += 1;
      return "v9.9.9";
    },
  });
  assert.equal(version, "v0.4.2");
  assert.equal(latestRequests, 0);
});

test("explicit export version override supports pinned and latest values", async () => {
  assert.equal(
    await getTargetVersion({
      env: { EXPORT_RUNTIME_VERSION: "v0.4.3" },
      allowOverride: true,
      readPinned: () => "v0.4.2",
    }),
    "v0.4.3"
  );

  let latestRequests = 0;
  assert.equal(
    await getTargetVersion({
      env: { EXPORT_RUNTIME_VERSION: "latest" },
      allowOverride: true,
      readPinned: () => "v0.4.2",
      resolveLatest: async () => {
        latestRequests += 1;
        return "v0.4.4";
      },
    }),
    "v0.4.4"
  );
  assert.equal(latestRequests, 1);
});

function createRuntimeFixture(version) {
  const runtimeRoot = fs.mkdtempSync(
    path.join(os.tmpdir(), "presentation-export-sync-")
  );
  fs.mkdirSync(path.join(runtimeRoot, "py"), { recursive: true });
  fs.writeFileSync(path.join(runtimeRoot, "index.js"), "module.exports = {};\n");

  const converterName =
    process.platform === "win32"
      ? `convert-${process.platform}-${process.arch}.exe`
      : `convert-${process.platform}-${process.arch}`;
  fs.writeFileSync(path.join(runtimeRoot, "py", converterName), "fixture\n");

  if (version !== undefined) {
    writeInstalledVersionAtomic(
      version,
      path.join(runtimeRoot, installedVersionFileName)
    );
  }
  return runtimeRoot;
}

function removeFixture(runtimeRoot) {
  fs.rmSync(runtimeRoot, { recursive: true, force: true });
}

function installDamagedSharpFixture(runtimeRoot) {
  const sharpDir = path.join(runtimeRoot, "node_modules", "sharp");
  fs.mkdirSync(sharpDir, { recursive: true });
  fs.writeFileSync(
    path.join(sharpDir, "index.js"),
    "throw new Error('damaged Sharp fixture');\n"
  );
}

function createElectronRuntimeFixture(version) {
  const runtimeRoot = fs.mkdtempSync(
    path.join(os.tmpdir(), "electron-export-sync-")
  );
  const pyDir = path.join(runtimeRoot, "py");
  fs.mkdirSync(pyDir, { recursive: true });
  fs.writeFileSync(path.join(runtimeRoot, "index.js"), "module.exports = {};\n");

  const converterName =
    process.platform === "win32"
      ? `convert-${process.platform}-${process.arch}.exe`
      : `convert-${process.platform}-${process.arch}`;
  const binaryHeader =
    process.platform === "win32"
      ? Buffer.from([0x4d, 0x5a, 0x00, 0x00])
      : process.platform === "darwin"
        ? Buffer.from([0xfe, 0xed, 0xfa, 0xcf])
        : Buffer.from([0x7f, 0x45, 0x4c, 0x46]);
  fs.writeFileSync(path.join(pyDir, converterName), binaryHeader);

  if (version !== undefined) {
    writeElectronInstalledVersionAtomic(
      version,
      path.join(runtimeRoot, ".installed-version")
    );
  }
  return runtimeRoot;
}

test("atomic marker round-trips the installed version", () => {
  const runtimeRoot = createRuntimeFixture(undefined);
  const markerFile = path.join(runtimeRoot, installedVersionFileName);
  try {
    writeInstalledVersionAtomic("v0.4.2", markerFile);
    assert.equal(readInstalledVersion(markerFile), "v0.4.2");
    assert.deepEqual(
      fs.readdirSync(runtimeRoot).filter((name) => name.endsWith(".tmp")),
      []
    );
  } finally {
    removeFixture(runtimeRoot);
  }
});

test("matching installed version validates", () => {
  const runtimeRoot = createRuntimeFixture("v0.4.2");
  try {
    const result = validateExistingRuntime("v0.4.2", runtimeRoot);
    assert.equal(result.ok, true);
    assert.equal(result.version, "v0.4.2");
    assert.equal(fs.existsSync(path.join(runtimeRoot, "index.cjs")), true);
  } finally {
    removeFixture(runtimeRoot);
  }
});

test("version mismatch is rejected before runtime reuse", () => {
  const runtimeRoot = createRuntimeFixture("v0.3.3");
  try {
    const result = validateExistingRuntime("v0.4.2", runtimeRoot);
    assert.equal(result.ok, false);
    assert.match(result.reason, /v0\.3\.3.*does not match.*v0\.4\.2/);
  } finally {
    removeFixture(runtimeRoot);
  }
});

test("missing marker is rejected by check-only validation", () => {
  const runtimeRoot = createRuntimeFixture(undefined);
  try {
    const result = validateExistingRuntime("v0.4.2", runtimeRoot, {
      repair: false,
    });
    assert.equal(result.ok, false);
    assert.match(result.reason, /Missing installed-version marker/);
    assert.equal(fs.existsSync(path.join(runtimeRoot, "index.cjs")), false);
  } finally {
    removeFixture(runtimeRoot);
  }
});

test("strict validation rejects a missing CommonJS bundle without repairing it", () => {
  const runtimeRoot = createRuntimeFixture("v0.4.2");
  try {
    const result = validateExistingRuntime("v0.4.2", runtimeRoot, {
      repair: false,
    });
    assert.equal(result.ok, false);
    assert.match(result.reason, /Missing CommonJS runtime bundle/);
    assert.equal(fs.existsSync(path.join(runtimeRoot, "index.cjs")), false);
  } finally {
    removeFixture(runtimeRoot);
  }
});

test("strict validation rejects a stale CommonJS bundle", () => {
  const runtimeRoot = createRuntimeFixture("v0.4.2");
  try {
    const repaired = validateExistingRuntime("v0.4.2", runtimeRoot);
    assert.equal(repaired.ok, true);
    fs.writeFileSync(path.join(runtimeRoot, "index.cjs"), "stale bundle\n");

    const result = validateExistingRuntime("v0.4.2", runtimeRoot, {
      repair: false,
    });
    assert.equal(result.ok, false);
    assert.match(result.reason, /does not match/);
    assert.equal(
      fs.readFileSync(path.join(runtimeRoot, "index.cjs"), "utf8"),
      "stale bundle\n"
    );
  } finally {
    removeFixture(runtimeRoot);
  }
});

test("root and Electron pin the same v0.4.2 export release and Sharp range", () => {
  assert.equal(rootPackage.presentationExportVersion, "v0.4.2");
  assert.equal(
    electronPackage.exportVersion,
    rootPackage.presentationExportVersion
  );
  assert.equal(
    electronPackage.dependencies.sharp,
    rootPackage.dependencies.sharp
  );
});

test("Sharp validation rejects a damaged native addon", () => {
  const runtimeRoot = createRuntimeFixture("v0.4.2");
  try {
    installDamagedSharpFixture(runtimeRoot);
    assert.throws(
      () => assertRuntimeSharpLoadable(runtimeRoot),
      /requires a loadable sharp native addon/
    );
  } finally {
    removeFixture(runtimeRoot);
  }
});

test("root install finalization prepares artifacts and verifies Sharp before the marker", () => {
  const events = [];
  finalizeRuntimeInstall("v0.4.2", {
    prepareRuntime: () => events.push("runtime"),
    ensureSharp: () => events.push("sharp"),
    writeMarker: () => events.push("marker"),
  });
  assert.deepEqual(events, ["runtime", "sharp", "marker"]);
});

test("root install finalization never writes a marker after runtime preparation failure", () => {
  let markerWrites = 0;
  assert.throws(
    () =>
      finalizeRuntimeInstall("v0.4.2", {
        prepareRuntime: () => {
          throw new Error("damaged CommonJS bundle");
        },
        ensureSharp: () => {
          throw new Error("Sharp should not run");
        },
        writeMarker: () => {
          markerWrites += 1;
        },
      }),
    /damaged CommonJS bundle/
  );
  assert.equal(markerWrites, 0);
});

test("root install finalization never writes a marker after Sharp failure", () => {
  let markerWrites = 0;
  assert.throws(
    () =>
      finalizeRuntimeInstall("v0.4.2", {
        ensureSharp: () => {
          throw new Error("damaged Sharp");
        },
        writeMarker: () => {
          markerWrites += 1;
        },
      }),
    /damaged Sharp/
  );
  assert.equal(markerWrites, 0);
});

test("Electron runtime accepts a matching v0.4.2 marker and native converter", () => {
  const runtimeRoot = createElectronRuntimeFixture("v0.4.2");
  try {
    const result = validateExistingElectronRuntime("v0.4.2", runtimeRoot);
    assert.equal(result.ok, true);
    assert.equal(result.version, "v0.4.2");
  } finally {
    removeFixture(runtimeRoot);
  }
});

test("Electron runtime rejects a stale marker without changing it", () => {
  const runtimeRoot = createElectronRuntimeFixture("v0.3.3");
  const markerFile = path.join(runtimeRoot, ".installed-version");
  try {
    const result = validateExistingElectronRuntime("v0.4.2", runtimeRoot);
    assert.equal(result.ok, false);
    assert.match(result.reason, /v0\.3\.3.*does not match.*v0\.4\.2/);
    assert.equal(fs.readFileSync(markerFile, "utf8").trim(), "v0.3.3");
  } finally {
    removeFixture(runtimeRoot);
  }
});

test("Electron Sharp validation rejects a damaged native addon", () => {
  const runtimeRoot = createElectronRuntimeFixture("v0.4.2");
  try {
    installDamagedSharpFixture(runtimeRoot);
    assert.throws(
      () => assertElectronSharpLoadable(runtimeRoot),
      /requires a loadable sharp native addon/
    );
  } finally {
    removeFixture(runtimeRoot);
  }
});

test("Electron install finalization verifies Sharp before writing the marker", () => {
  const events = [];
  finalizeElectronRuntimeInstall("v0.4.2", {
    assertSharp: () => events.push("sharp"),
    writeMarker: () => events.push("marker"),
  });
  assert.deepEqual(events, ["sharp", "marker"]);
});

test("Electron install finalization never writes a marker after Sharp failure", () => {
  let markerWrites = 0;
  assert.throws(
    () =>
      finalizeElectronRuntimeInstall("v0.4.2", {
        assertSharp: () => {
          throw new Error("damaged Electron Sharp");
        },
        writeMarker: () => {
          markerWrites += 1;
        },
      }),
    /damaged Electron Sharp/
  );
  assert.equal(markerWrites, 0);
});
