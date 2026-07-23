const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const test = require("node:test");

const {
  EXPECTED_EXPORT_VERSION,
  validatePackagingInputs,
} = require("./verify-packaging-inputs.cjs");

function binaryHeader(platform) {
  if (platform === "win32") return Buffer.from([0x4d, 0x5a, 0, 0]);
  if (platform === "linux") return Buffer.from([0x7f, 0x45, 0x4c, 0x46]);
  return Buffer.from([0xfe, 0xed, 0xfa, 0xcf]);
}

function writeFile(filePath, contents = "fixture") {
  fs.mkdirSync(path.dirname(filePath), { recursive: true });
  fs.writeFileSync(filePath, contents);
}

function createFixture({ nested = false, platform = "win32", arch = "x64" } = {}) {
  const workspaceRoot = fs.mkdtempSync(
    path.join(os.tmpdir(), "presenton-preflight-")
  );
  const electronRoot = path.join(workspaceRoot, "electron");
  const nextRoot = path.join(electronRoot, "resources", "nextjs");
  const serverRoot = nested
    ? path.join(nextRoot, "servers", "nextjs")
    : nextRoot;
  const exportRoot = path.join(electronRoot, "resources", "export");
  const fastapiRoot = path.join(electronRoot, "resources", "fastapi");
  const converterName =
    platform === "win32"
      ? `convert-${platform}-${arch}.exe`
      : `convert-${platform}-${arch}`;

  writeFile(
    path.join(electronRoot, "package.json"),
    JSON.stringify({ exportVersion: EXPECTED_EXPORT_VERSION })
  );
  writeFile(path.join(serverRoot, "server.js"));
  writeFile(path.join(serverRoot, ".next-build", "static", "chunk.js"));
  writeFile(path.join(serverRoot, "public", "logo.png"));
  writeFile(
    path.join(exportRoot, ".installed-version"),
    `${EXPECTED_EXPORT_VERSION}\n`
  );
  writeFile(path.join(exportRoot, "index.js"), Buffer.alloc(512, 1));
  writeFile(
    path.join(exportRoot, "py", converterName),
    binaryHeader(platform)
  );
  writeFile(
    path.join(
      fastapiRoot,
      platform === "win32" ? "fastapi.exe" : "fastapi"
    ),
    binaryHeader(platform)
  );

  return {
    arch,
    electronRoot,
    exportRoot,
    nextRoot,
    platform,
    serverRoot,
    workspaceRoot,
    cleanup() {
      fs.rmSync(workspaceRoot, { recursive: true, force: true });
    },
  };
}

function validate(fixture, overrides = {}) {
  return validatePackagingInputs({
    electronRoot: fixture.electronRoot,
    workspaceRoot: fixture.workspaceRoot,
    platform: fixture.platform,
    arch: fixture.arch,
    sharpCheck: () => {},
    nextSharpCheck: () => {},
    ...overrides,
  });
}

test("accepts direct standalone with complete packaging inputs", () => {
  const fixture = createFixture();
  try {
    const result = validate(fixture);
    assert.equal(result.nextLayout, "direct");
    assert.equal(result.exportVersion, EXPECTED_EXPORT_VERSION);
    assert.ok(result.fileCount >= 3);
  } finally {
    fixture.cleanup();
  }
});

test("accepts nested standalone only when assets sit beside nested server", () => {
  const fixture = createFixture({ nested: true });
  try {
    const result = validate(fixture);
    assert.equal(result.nextLayout, "nested");
    assert.equal(result.nextServer, path.join(fixture.serverRoot, "server.js"));
  } finally {
    fixture.cleanup();
  }
});

test("rejects missing static or public assets", async (t) => {
  await t.test("static", () => {
    const fixture = createFixture();
    try {
      fs.rmSync(path.join(fixture.serverRoot, ".next-build", "static"), {
        recursive: true,
      });
      assert.throws(() => validate(fixture), /Next\.js static assets is missing/);
    } finally {
      fixture.cleanup();
    }
  });

  await t.test("public", () => {
    const fixture = createFixture();
    try {
      fs.rmSync(path.join(fixture.serverRoot, "public"), { recursive: true });
      assert.throws(() => validate(fixture), /Next\.js public assets is missing/);
    } finally {
      fixture.cleanup();
    }
  });
});

test("rejects export marker mismatch and damaged Sharp", async (t) => {
  await t.test("marker", () => {
    const fixture = createFixture();
    try {
      writeFile(path.join(fixture.exportRoot, ".installed-version"), "v0.3.3\n");
      assert.throws(
        () => validate(fixture),
        /Export runtime marker must be v0\.4\.2, received v0\.3\.3/
      );
    } finally {
      fixture.cleanup();
    }
  });

  await t.test("Sharp", () => {
    const fixture = createFixture();
    try {
      writeFile(
        path.join(fixture.electronRoot, "node_modules", "sharp", "index.js"),
        "throw new Error('damaged fixture Sharp')"
      );
      assert.throws(
        () => validate(fixture, { sharpCheck: undefined }),
        /Electron Sharp native addon is not loadable/
      );
    } finally {
      fixture.cleanup();
    }
  });
});

test("rejects missing or damaged Next.js Sharp from the standalone cwd", async (t) => {
  await t.test("missing", () => {
    const fixture = createFixture();
    try {
      assert.throws(
        () => validate(fixture, { nextSharpCheck: undefined }),
        /Next\.js Sharp native addon is not loadable/
      );
    } finally {
      fixture.cleanup();
    }
  });

  await t.test("damaged", () => {
    const fixture = createFixture();
    try {
      writeFile(
        path.join(fixture.serverRoot, "node_modules", "sharp", "index.js"),
        "throw new Error('damaged fixture Next Sharp')"
      );
      assert.throws(
        () => validate(fixture, { nextSharpCheck: undefined }),
        /Next\.js Sharp native addon is not loadable/
      );
    } finally {
      fixture.cleanup();
    }
  });
});

test("rejects incompatible converter and FastAPI executable formats", async (t) => {
  await t.test("converter", () => {
    const fixture = createFixture();
    try {
      const converter = fs.readdirSync(path.join(fixture.exportRoot, "py"))[0];
      writeFile(path.join(fixture.exportRoot, "py", converter), "bad!");
      assert.throws(() => validate(fixture), /Export converter has incompatible format/);
    } finally {
      fixture.cleanup();
    }
  });

  await t.test("FastAPI", () => {
    const fixture = createFixture();
    try {
      writeFile(
        path.join(fixture.electronRoot, "resources", "fastapi", "fastapi.exe"),
        "bad!"
      );
      assert.throws(
        () => validate(fixture),
        /FastAPI executable has incompatible format/
      );
    } finally {
      fixture.cleanup();
    }
  });

  await t.test("missing FastAPI", () => {
    const fixture = createFixture();
    try {
      fs.rmSync(
        path.join(fixture.electronRoot, "resources", "fastapi", "fastapi.exe")
      );
      assert.throws(
        () => validate(fixture),
        /FastAPI executable is missing/
      );
    } finally {
      fixture.cleanup();
    }
  });
});

test("rejects unexpected standalone roots, excessive files, and excessive bytes", async (t) => {
  await t.test("root", () => {
    const fixture = createFixture();
    try {
      writeFile(path.join(fixture.nextRoot, "accidental-workspace", "source.ts"));
      assert.throws(() => validate(fixture), /Unexpected standalone root entry/);
    } finally {
      fixture.cleanup();
    }
  });

  await t.test("files", () => {
    const fixture = createFixture();
    try {
      assert.throws(
        () => validate(fixture, { maxFiles: 2 }),
        /exceeds file limit/
      );
    } finally {
      fixture.cleanup();
    }
  });

  await t.test("bytes", () => {
    const fixture = createFixture();
    try {
      assert.throws(
        () => validate(fixture, { maxBytes: 10 }),
        /exceeds size limit/
      );
    } finally {
      fixture.cleanup();
    }
  });
});

test("rejects a packaging link whose target escapes the workspace", (t) => {
  const fixture = createFixture();
  const outside = fs.mkdtempSync(path.join(os.tmpdir(), "presenton-outside-"));
  const linkPath = path.join(fixture.serverRoot, "node_modules");
  try {
    try {
      fs.symlinkSync(
        outside,
        linkPath,
        process.platform === "win32" ? "junction" : "dir"
      );
    } catch (error) {
      if (error.code === "EPERM" || error.code === "EACCES") {
        t.skip(`Link creation is unavailable: ${error.code}`);
        return;
      }
      throw error;
    }
    assert.throws(() => validate(fixture), /link escapes packaging root/);
  } finally {
    fixture.cleanup();
    fs.rmSync(outside, { recursive: true, force: true });
  }
});

test("rejects a link that stays in the workspace but escapes its packaging root", (t) => {
  const fixture = createFixture();
  const sharedTarget = path.join(
    fixture.electronRoot,
    "resources",
    "shared-node-modules"
  );
  const linkPath = path.join(fixture.serverRoot, "node_modules");
  try {
    writeFile(path.join(sharedTarget, "sharp", "index.js"), "module.exports = {}");
    try {
      fs.symlinkSync(
        sharedTarget,
        linkPath,
        process.platform === "win32" ? "junction" : "dir"
      );
    } catch (error) {
      if (error.code === "EPERM" || error.code === "EACCES") {
        t.skip(`Link creation is unavailable: ${error.code}`);
        return;
      }
      throw error;
    }
    assert.throws(() => validate(fixture), /link escapes packaging root/);
  } finally {
    fixture.cleanup();
  }
});

test("rejects an internal standalone link that AppX cannot map", (t) => {
  const fixture = createFixture();
  const target = path.join(
    fixture.serverRoot,
    "node_modules",
    ".pnpm",
    "next",
    "node_modules",
    "next"
  );
  const linkPath = path.join(fixture.serverRoot, "node_modules", "next");
  try {
    writeFile(path.join(target, "package.json"), "{}");
    fs.mkdirSync(path.dirname(linkPath), { recursive: true });
    try {
      fs.symlinkSync(
        target,
        linkPath,
        process.platform === "win32" ? "junction" : "dir"
      );
    } catch (error) {
      if (error.code === "EPERM" || error.code === "EACCES") {
        t.skip(`Link creation is unavailable: ${error.code}`);
        return;
      }
      throw error;
    }
    assert.throws(
      () => validate(fixture),
      /must be link-free for AppX compatibility/
    );
  } finally {
    fixture.cleanup();
  }
});

test("rejects a packaging scan root that is itself a workspace-internal link", (t) => {
  const fixture = createFixture();
  const sharedNext = path.join(fixture.electronRoot, "shared-nextjs");
  try {
    fs.renameSync(fixture.nextRoot, sharedNext);
    try {
      fs.symlinkSync(
        sharedNext,
        fixture.nextRoot,
        process.platform === "win32" ? "junction" : "dir"
      );
    } catch (error) {
      if (error.code === "EPERM" || error.code === "EACCES") {
        t.skip(`Link creation is unavailable: ${error.code}`);
        return;
      }
      throw error;
    }
    assert.throws(
      () => validate(fixture),
      /Packaging scan root must not be a link/
    );
  } finally {
    fixture.cleanup();
  }
});
