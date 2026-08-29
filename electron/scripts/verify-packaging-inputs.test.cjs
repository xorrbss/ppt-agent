const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const test = require("node:test");
const crypto = require("node:crypto");
const {
  digestSourceTree,
  writeResourceProvenance,
} = require("./resource-provenance.cjs");
const {
  materializeTailwindRuntimeClosure,
} = require("./next-runtime-dependencies.cjs");

const {
  EXPECTED_EXPORT_VERSION,
  AUTHORED_SEMANTIC_CONTRACT_RELATIVE,
  OOXML_CONTRACT_RELATIVE,
  TEMPLATE_ENGINE_RELATIVE,
  TEMPLATE_RENDERER_RELATIVE,
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

function sha256(filePath) {
  return crypto.createHash("sha256").update(fs.readFileSync(filePath)).digest("hex");
}

function readinessOverlayFixture() {
  return Buffer.from(
    `${"x".repeat(512)}data-presenton-template-v2-readiness ` +
      'templateV2ResourceError==="true" ' +
      "Template V2 required resource failed"
  );
}

test("source provenance excludes generated Next.js .next-* runtime trees", () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "presenton-provenance-"));
  try {
    writeFile(path.join(root, "app", "page.tsx"), "export default function Page() {}\n");
    writeFile(
      path.join(root, ".next-authored-vnext-dev-1234", "server", "artifact.js"),
      "generated-one\n"
    );
    const before = digestSourceTree(root);

    writeFile(
      path.join(root, ".next-authored-vnext-dev-1234", "server", "artifact.js"),
      "generated-two\n"
    );
    const afterGeneratedChange = digestSourceTree(root);
    assert.deepEqual(afterGeneratedChange, before);

    writeFile(path.join(root, "app", "page.tsx"), "export default function Changed() {}\n");
    assert.notEqual(digestSourceTree(root).digest, before.digest);
  } finally {
    fs.rmSync(root, { recursive: true, force: true });
  }
});

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
  const electronSourceRoot = path.join(electronRoot, "app");
  const electronBundleRoot = path.join(electronRoot, "app_dist");
  const nextSourceRoot = path.join(workspaceRoot, "servers", "nextjs");
  const fastapiSourceRoot = path.join(workspaceRoot, "servers", "fastapi");
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
  writeFile(path.join(exportRoot, "index.js"), readinessOverlayFixture());
  writeFile(
    path.join(exportRoot, "py", converterName),
    binaryHeader(platform)
  );
  writeFile(path.join(electronSourceRoot, "main.ts"), "export const fixture = true;");
  writeFile(path.join(electronBundleRoot, "main.js"), "exports.fixture = true;");
  writeFile(path.join(nextSourceRoot, "app", "page.tsx"), "export default function Page() {}");
  writeFile(
    path.join(nextSourceRoot, "node_modules", "tailwindcss", "package.json"),
    JSON.stringify({
      name: "tailwindcss",
      version: "3.4.19",
      dependencies: { "@alloc/quick-lru": "^5.2.0" },
    })
  );
  writeFile(
    path.join(nextSourceRoot, "node_modules", "tailwindcss", "index.js"),
    "module.exports = function tailwindcss() {};\n"
  );
  writeFile(
    path.join(nextSourceRoot, "node_modules", "@alloc", "quick-lru", "index.js"),
    "module.exports = class QuickLRU {};\n"
  );
  writeFile(
    path.join(nextSourceRoot, "node_modules", "@alloc", "quick-lru", "package.json"),
    JSON.stringify({ name: "@alloc/quick-lru", version: "5.2.0" })
  );
  materializeTailwindRuntimeClosure({ nextjsRoot: nextSourceRoot, serverRoot });
  writeFile(path.join(fastapiSourceRoot, "main.py"), "app = object()\n");
  const ooxmlContract = "# generated OOXML contract fixture\n" + "x".repeat(100);
  writeFile(
    path.join(workspaceRoot, "contracts", OOXML_CONTRACT_RELATIVE),
    ooxmlContract
  );
  const templateRegistry = JSON.stringify({
    schemaVersion: "presenton.template-package-registry/v1",
    packages: [],
    legacyCompatibility: { enabled: false, templateIds: [] },
  });
  writeFile(
    path.join(workspaceRoot, "templates", "package-registry.v1.json"),
    templateRegistry
  );
  writeFile(
    path.join(electronRoot, "resources", "templates", "package-registry.v1.json"),
    templateRegistry
  );
  const templateEngine = "{\"engineId\":\"template-v2-json\"}" + " ".repeat(100);
  writeFile(
    path.join(workspaceRoot, "contracts", TEMPLATE_ENGINE_RELATIVE),
    templateEngine
  );
  const templateRenderer = "export const rendererEvidence = true;\n" + "x".repeat(100);
  writeFile(
    path.join(workspaceRoot, TEMPLATE_RENDERER_RELATIVE),
    templateRenderer
  );
  writeFile(
    path.join(electronRoot, "resources", TEMPLATE_RENDERER_RELATIVE),
    templateRenderer
  );
  writeFile(
    path.join(electronRoot, "resources", "contracts", TEMPLATE_ENGINE_RELATIVE),
    templateEngine
  );
  writeFile(
    path.join(electronRoot, "resources", "contracts", OOXML_CONTRACT_RELATIVE),
    ooxmlContract
  );
  const authoredSemanticProfiles = {
    "release-profile.v2.json": JSON.stringify({ schemaVersion: "authored-release-profile/v2" }),
    "release-profile.v3.json": JSON.stringify({ schemaVersion: "authored-release-profile/v3" }),
  };
  for (const [name, contents] of Object.entries(authoredSemanticProfiles)) {
    writeFile(
      path.join(workspaceRoot, "contracts", AUTHORED_SEMANTIC_CONTRACT_RELATIVE, name),
      contents
    );
    writeFile(
      path.join(electronRoot, "resources", "contracts", AUTHORED_SEMANTIC_CONTRACT_RELATIVE, name),
      contents
    );
  }
  writeFile(path.join(workspaceRoot, "LICENSE"), "L".repeat(1000));
  writeFile(path.join(workspaceRoot, "NOTICE"), "N".repeat(1000));
  writeFile(
    path.join(workspaceRoot, "compliance", "release-license-policy.json"),
    JSON.stringify({ schema: "presenton.release-license-policy/v1", padding: "P".repeat(100) }),
  );
  writeFile(
    path.join(workspaceRoot, "compliance", "copyleft-source-offer.md"),
    "S".repeat(100),
  );
  writeFile(path.join(workspaceRoot, "compliance", "fonts", "OFL-1.1.txt"), "O".repeat(1000));
  writeFile(path.join(workspaceRoot, "compliance", "fonts", "README.md"), "F".repeat(100));
  writeFile(
    path.join(workspaceRoot, "docs", "experiments", "authored-vnext", "03-ooxml-roundtrip", "compliance", "LICENSE.genoffice.txt"),
    "G".repeat(1000)
  );
  writeFile(
    path.join(workspaceRoot, "docs", "experiments", "authored-vnext", "03-ooxml-roundtrip", "compliance", "NOTICE.genoffice.txt"),
    "G".repeat(100)
  );
  writeFile(
    path.join(workspaceRoot, "compliance", "release-sbom.spdx.json"),
    JSON.stringify({
      spdxVersion: "SPDX-2.3",
      dataLicense: "CC0-1.0",
      packages: [{
        SPDXID: "SPDXRef-fixture",
        name: "fixture",
        licenseDeclared: "MIT",
        licenseConcluded: "MIT",
      }],
      annotations: [{
        annotator: "Tool: scripts/generate-release-sbom.mjs",
        comment: JSON.stringify({
          schema: "presenton.release-license-attestation/v1",
          policySha256: sha256(path.join(workspaceRoot, "compliance", "release-license-policy.json")),
          noticeSha256: sha256(path.join(workspaceRoot, "NOTICE")),
          sourceOfferSha256: sha256(path.join(workspaceRoot, "compliance", "copyleft-source-offer.md")),
        }),
      }],
      padding: "S".repeat(1000),
    })
  );
  for (const [kind, sourceRoot, resourceRoot] of [
    ["electron", electronSourceRoot, electronBundleRoot],
    ["nextjs", nextSourceRoot, nextRoot],
    ["fastapi", fastapiSourceRoot, fastapiRoot],
    [
      "template-packages",
      path.join(workspaceRoot, "templates"),
      path.join(electronRoot, "resources", "templates"),
    ],
  ]) {
    writeResourceProvenance({ kind, sourceRoot, resourceRoot, workspaceRoot });
  }
  writeFile(
    path.join(
      fastapiRoot,
      platform === "win32" ? "fastapi.exe" : "fastapi"
    ),
    binaryHeader(platform)
  );
  writeFile(
    path.join(
      fastapiRoot,
      platform === "win32"
        ? "workspace-operation-worker.exe"
        : "workspace-operation-worker"
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

test("rejects stale and missing source provenance", async (t) => {
  await t.test("stale", () => {
    const fixture = createFixture();
    try {
      writeFile(
        path.join(fixture.workspaceRoot, "servers", "nextjs", "app", "page.tsx"),
        "export default function Changed() {}"
      );
      assert.throws(() => validate(fixture), /Packaged nextjs resource is stale/);
    } finally {
      fixture.cleanup();
    }
  });

  await t.test("missing", () => {
    const fixture = createFixture();
    try {
      fs.rmSync(path.join(fixture.electronRoot, "app_dist", ".source-provenance.json"));
      assert.throws(() => validate(fixture), /Resource provenance is missing or invalid for electron/);
    } finally {
      fixture.cleanup();
    }
  });

  await t.test("generated build output is excluded", () => {
    const fixture = createFixture();
    try {
      writeFile(
        path.join(fixture.workspaceRoot, "servers", "fastapi", "build", "server", "Analysis-00.toc"),
        "generated pyinstaller state"
      );
      assert.doesNotThrow(() => validate(fixture));
    } finally {
      fixture.cleanup();
    }
  });
});

test("rejects a stale Next.js quick-lru NFT runtime dependency", () => {
  const fixture = createFixture({ nested: true });
  try {
    writeFile(
      path.join(
        fixture.serverRoot,
        "node_modules",
        "@alloc",
        "quick-lru",
        "package.json"
      ),
      JSON.stringify({ name: "@alloc/quick-lru", version: "0.0.0" })
    );
    assert.throws(
      () => validate(fixture),
      /Packaged Next\.js runtime dependency is stale: @alloc\/quick-lru sha256; rebuild the Next\.js resource/
    );
  } finally {
    fixture.cleanup();
  }
});

test("rejects incomplete release compliance inputs", () => {
  const fixture = createFixture();
  try {
    fs.rmSync(path.join(fixture.workspaceRoot, "compliance", "fonts", "OFL-1.1.txt"));
    assert.throws(() => validate(fixture), /Bundled font OFL text is missing/);
  } finally {
    fixture.cleanup();
  }
});

test("rejects a stale packaged authored OOXML contract", () => {
  const fixture = createFixture();
  try {
    writeFile(
      path.join(
        fixture.electronRoot,
        "resources",
        "contracts",
        OOXML_CONTRACT_RELATIVE
      ),
      "# stale generated contract\n" + "x".repeat(100)
    );
    assert.throws(
      () => validate(fixture),
      /Packaged authored OOXML contract is stale/
    );
  } finally {
    fixture.cleanup();
  }
});

test("rejects stale packaged template assets", () => {
  const fixture = createFixture();
  try {
    writeFile(
      path.join(
        fixture.electronRoot,
        "resources",
        "templates",
        "package-registry.v1.json"
      ),
      JSON.stringify({ legacyCompatibility: { enabled: true } })
    );
    assert.throws(
      () => validate(fixture),
      /Packaged template package tree file is stale/
    );
  } finally {
    fixture.cleanup();
  }
});

test("rejects stale packaged template renderer evidence", () => {
  const fixture = createFixture();
  try {
    writeFile(
      path.join(
        fixture.electronRoot,
        "resources",
        TEMPLATE_RENDERER_RELATIVE
      ),
      "export const rendererEvidence = false;\n" + "x".repeat(100)
    );
    assert.throws(
      () => validate(fixture),
      /Packaged template package renderer evidence is stale/
    );
  } finally {
    fixture.cleanup();
  }
});

test("rejects a template registry that re-enables legacy compatibility", () => {
  const fixture = createFixture();
  try {
    const registry = JSON.stringify({
      schemaVersion: "presenton.template-package-registry/v1",
      packages: [],
      legacyCompatibility: { enabled: true, templateIds: [] },
    });
    const sourceRoot = path.join(fixture.workspaceRoot, "templates");
    const resourceRoot = path.join(
      fixture.electronRoot,
      "resources",
      "templates"
    );
    writeFile(path.join(sourceRoot, "package-registry.v1.json"), registry);
    writeFile(path.join(resourceRoot, "package-registry.v1.json"), registry);
    writeResourceProvenance({
      kind: "template-packages",
      sourceRoot,
      resourceRoot,
      workspaceRoot: fixture.workspaceRoot,
    });
    assert.throws(
      () => validate(fixture),
      /template registry must keep legacyCompatibility\.enabled=false/
    );
  } finally {
    fixture.cleanup();
  }
});

test("rejects unknown licenses and stale license attestation", async (t) => {
  await t.test("unknown", () => {
    const fixture = createFixture();
    try {
      const sbomPath = path.join(fixture.workspaceRoot, "compliance", "release-sbom.spdx.json");
      const document = JSON.parse(fs.readFileSync(sbomPath, "utf8"));
      document.packages[0].licenseConcluded = "NOASSERTION";
      writeFile(sbomPath, JSON.stringify(document));
      assert.throws(() => validate(fixture), /Release SBOM has an unknown license/);
    } finally {
      fixture.cleanup();
    }
  });

  await t.test("stale notice", () => {
    const fixture = createFixture();
    try {
      writeFile(path.join(fixture.workspaceRoot, "NOTICE"), "changed".repeat(200));
      assert.throws(() => validate(fixture), /Release SBOM license attestation is stale/);
    } finally {
      fixture.cleanup();
    }
  });
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
        /Export runtime marker must be v0\.4\.8, received v0\.3\.3/
      );
    } finally {
      fixture.cleanup();
    }
  });

  await t.test("readiness overlay", () => {
    const fixture = createFixture();
    try {
      writeFile(
        path.join(fixture.exportRoot, "index.js"),
        Buffer.alloc(512, 1)
      );
      assert.throws(
        () => validate(fixture),
        /missing the fail-closed renderer readiness overlay/
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
        /Electron Sharp native addon is not loadable[\s\S]*damaged fixture Sharp/
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
        /Next\.js Sharp native addon is not loadable[\s\S]*Cannot find module 'sharp'/
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
        /Next\.js Sharp native addon is not loadable[\s\S]*damaged fixture Next Sharp/
      );
    } finally {
      fixture.cleanup();
    }
  });
});

test("rejects incompatible packaged executable formats", async (t) => {
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

  await t.test("workspace worker", () => {
    const fixture = createFixture();
    try {
      writeFile(
        path.join(
          fixture.electronRoot,
          "resources",
          "fastapi",
          "workspace-operation-worker.exe"
        ),
        "bad!"
      );
      assert.throws(
        () => validate(fixture),
        /Workspace operation worker executable has incompatible format/
      );
    } finally {
      fixture.cleanup();
    }
  });

  await t.test("missing workspace worker", () => {
    const fixture = createFixture();
    try {
      fs.rmSync(
        path.join(
          fixture.electronRoot,
          "resources",
          "fastapi",
          "workspace-operation-worker.exe"
        )
      );
      assert.throws(
        () => validate(fixture),
        /Workspace operation worker executable is missing/
      );
    } finally {
      fixture.cleanup();
    }
  });
});

test("rejects stale or incomplete packaged authored semantic contract tree", () => {
  const fixture = createFixture();
  try {
    const resourceRoot = path.join(
      fixture.electronRoot,
      "resources",
      "contracts",
      AUTHORED_SEMANTIC_CONTRACT_RELATIVE
    );
    writeFile(path.join(resourceRoot, "release-profile.v2.json"), "{\"stale\":true}");
    assert.throws(
      () => validate(fixture),
      /Packaged authored semantic contract tree file is stale: release-profile\.v2\.json; rebuild the FastAPI resource/
    );

    const fresh = createFixture();
    try {
      fs.rmSync(
        path.join(
          fresh.electronRoot,
          "resources",
          "contracts",
          AUTHORED_SEMANTIC_CONTRACT_RELATIVE,
          "release-profile.v3.json"
        )
      );
      assert.throws(
        () => validate(fresh),
        /Packaged authored semantic contract tree inventory is stale; rebuild the FastAPI resource/
      );
    } finally {
      fresh.cleanup();
    }
  } finally {
    fixture.cleanup();
  }
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

test("rejects a FastAPI framework link that was not materialized", (t) => {
  const fixture = createFixture();
  const fastapiRoot = path.join(
    fixture.electronRoot,
    "resources",
    "fastapi"
  );
  const target = path.join(fastapiRoot, "_internal", "Python.framework", "3.11");
  const linkPath = path.join(
    fastapiRoot,
    "_internal",
    "Python.framework",
    "Current"
  );
  try {
    writeFile(path.join(target, "Python"), "fixture");
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
