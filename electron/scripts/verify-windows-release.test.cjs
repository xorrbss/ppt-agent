const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const test = require("node:test");

const {
  APP_RESOURCE_PREFIX,
  EXPECTED_APPX_VERSION,
  EXPECTED_EXPORT_VERSION,
  EXPECTED_IDENTITY,
  EXPECTED_PUBLISHER,
  inspectPeBuffer,
  readExpectedAppxVersion,
  readExpectedExportVersion,
  validateAppx,
  validateWindowsRelease,
  xmlTagAttributes,
} = require("./verify-windows-release.cjs");

function peFixture(machine = 0x8664) {
  const buffer = Buffer.alloc(512);
  buffer.write("MZ", 0, "ascii");
  buffer.writeUInt32LE(0x80, 0x3c);
  buffer.write("PE\0\0", 0x80, "binary");
  buffer.writeUInt16LE(machine, 0x84);
  return buffer;
}

function validSignature() {
  return {
    Status: "Valid",
    StatusMessage: "Signature verified",
    Subject: "CN=Fixture",
    Thumbprint: "0123456789",
  };
}

function archiveFixture(overrides = {}) {
  const prefix = APP_RESOURCE_PREFIX;
  const nextRoot = `${prefix}nextjs/servers/nextjs/`;
  const manifest = `<?xml version="1.0"?><Package><Identity Name="${EXPECTED_IDENTITY}" Publisher='${EXPECTED_PUBLISHER}' Version="${EXPECTED_APPX_VERSION}" ProcessorArchitecture="x64"/><Applications><Application Id="PresentonAI.Presenton" Executable="Presenton.exe"/></Applications></Package>`;
  const data = new Map([
    ["AppxManifest.xml", Buffer.from(manifest)],
    ["AppxBlockMap.xml", Buffer.from("<BlockMap/>")],
    ["[Content_Types].xml", Buffer.from("<Types/>")],
    ["AppxSignature.p7x", Buffer.from("signature")],
    ["Presenton.exe", peFixture()],
    [`${prefix}export/.installed-version`, Buffer.from(`${EXPECTED_EXPORT_VERSION}\n`)],
    [`${prefix}export/index.js`, Buffer.alloc(512)],
    [`${prefix}export/py/convert-win32-x64.exe`, peFixture()],
    [`${prefix}fastapi/fastapi.exe`, peFixture()],
    [`${nextRoot}server.js`, Buffer.from("server")],
    [`${nextRoot}.next-build/static/chunks/webpack.js`, Buffer.from("static")],
    [`${nextRoot}public/favicon.ico`, Buffer.from("public")],
  ]);
  for (const [name, value] of Object.entries(overrides)) {
    if (value === null) data.delete(name);
    else data.set(name, Buffer.isBuffer(value) ? value : Buffer.from(value));
  }
  return {
    list: () => new Set(data.keys()),
    read: (_file, entry) => {
      if (!data.has(entry)) throw new Error(`fixture entry missing: ${entry}`);
      return data.get(entry);
    },
  };
}

function write(filePath, contents) {
  fs.mkdirSync(path.dirname(filePath), { recursive: true });
  fs.writeFileSync(filePath, contents);
}

function releaseFixture() {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "presenton-release-"));
  const installer = path.join(root, "Presenton-0.8.6-beta.exe");
  const appx = path.join(root, "Presenton-0.8.6-beta.appx");
  write(installer, peFixture());
  write(appx, Buffer.from("PK\u0003\u0004fixture", "binary"));
  return {
    root,
    installer,
    appx,
    cleanup: () => fs.rmSync(root, { recursive: true, force: true }),
  };
}

function addUnpacked(fixture) {
  const resources = path.join(
    fixture.root,
    "win-unpacked",
    "resources",
    "app",
    "resources"
  );
  write(path.join(fixture.root, "win-unpacked", "Presenton.exe"), peFixture());
  write(
    path.join(resources, "export", ".installed-version"),
    `${EXPECTED_EXPORT_VERSION}\n`
  );
  write(path.join(resources, "export", "index.js"), Buffer.alloc(512));
  write(
    path.join(resources, "export", "py", "convert-win32-x64.exe"),
    peFixture()
  );
  write(path.join(resources, "fastapi", "fastapi.exe"), peFixture());
  const next = path.join(resources, "nextjs", "servers", "nextjs");
  write(path.join(next, "server.js"), "server");
  write(path.join(next, ".next-build", "static", "chunk.js"), "static");
  write(path.join(next, "public", "logo.png"), "public");
}

test("parses PE and AppX manifest attributes", () => {
  assert.equal(inspectPeBuffer(peFixture()).machine, 0x8664);
  assert.throws(() => inspectPeBuffer(Buffer.from("not PE")), /not a PE file/);
  const attributes = xmlTagAttributes(
    `<Identity Name="Presenton" Publisher='CN=Fixture'>`,
    "Identity"
  );
  assert.deepEqual(attributes, { Name: "Presenton", Publisher: "CN=Fixture" });
});

test("derives the exact four-part AppX version from the Electron package", () => {
  assert.equal(readExpectedAppxVersion(), "2026.7.2401.0");
});

test("accepts a complete AppX structure and embedded runtime resources", () => {
  const fixture = releaseFixture();
  try {
    const result = validateAppx(fixture.appx, {
      archiveReader: archiveFixture(),
    });
    assert.equal(result.architecture, "x64");
    assert.match(result.nextServer, /servers\/nextjs\/server\.js$/);
  } finally {
    fixture.cleanup();
  }
});

test("rejects AppX manifest identity, signature entry, and export marker regressions", async (t) => {
  await t.test("identity", () => {
    const fixture = releaseFixture();
    try {
      const badManifest = `<Package><Identity Name="Wrong" Publisher='${EXPECTED_PUBLISHER}' Version="1.0.0.0" ProcessorArchitecture="x64"/><Application Executable="Presenton.exe"/></Package>`;
      assert.throws(
        () =>
          validateAppx(fixture.appx, {
            archiveReader: archiveFixture({ "AppxManifest.xml": badManifest }),
          }),
        /Unexpected AppX Identity Name/
      );
    } finally {
      fixture.cleanup();
    }
  });
  await t.test("signature entry", () => {
    const fixture = releaseFixture();
    try {
      assert.throws(
        () =>
          validateAppx(fixture.appx, {
            archiveReader: archiveFixture({ "AppxSignature.p7x": null }),
            requireSignature: true,
          }),
        /missing AppxSignature\.p7x/
      );
    } finally {
      fixture.cleanup();
    }
  });
  await t.test("marker", () => {
    const fixture = releaseFixture();
    try {
      assert.throws(
        () =>
          validateAppx(fixture.appx, {
            archiveReader: archiveFixture({
              [`${APP_RESOURCE_PREFIX}export/.installed-version`]: "v0.3.3",
            }),
          }),
        /export marker must be v0\.4\.2/
      );
    } finally {
      fixture.cleanup();
    }
  });
});

test("rejects missing Next, FastAPI, and invalid embedded PE resources", async (t) => {
  const nextServer = `${APP_RESOURCE_PREFIX}nextjs/servers/nextjs/server.js`;
  const fastapi = `${APP_RESOURCE_PREFIX}fastapi/fastapi.exe`;
  await t.test("Next", () => {
    const fixture = releaseFixture();
    try {
      assert.throws(
        () =>
          validateAppx(fixture.appx, {
            archiveReader: archiveFixture({ [nextServer]: null }),
          }),
        /Next\.js server\.js is missing/
      );
    } finally {
      fixture.cleanup();
    }
  });
  await t.test("FastAPI", () => {
    const fixture = releaseFixture();
    try {
      assert.throws(
        () =>
          validateAppx(fixture.appx, {
            archiveReader: archiveFixture({ [fastapi]: null }),
          }),
        /FastAPI executable is missing/
      );
    } finally {
      fixture.cleanup();
    }
  });
  await t.test("PE", () => {
    const fixture = releaseFixture();
    try {
      assert.throws(
        () =>
          validateAppx(fixture.appx, {
            archiveReader: archiveFixture({ [fastapi]: "not PE" }),
          }),
        /AppX FastAPI executable is not a PE file/
      );
    } finally {
      fixture.cleanup();
    }
  });
});

test("requires valid Authenticode without changing certificate stores", () => {
  const fixture = releaseFixture();
  try {
    assert.throws(
      () =>
        validateWindowsRelease({
          distRoot: fixture.root,
          archiveReader: archiveFixture(),
          requireSignature: true,
          signatureCheck: () => ({
            Status: "NotSigned",
            StatusMessage: "The file is not digitally signed.",
          }),
        }),
      /Windows signing blockers: NSIS installer Authenticode status is NotSigned.*AppX package Authenticode status is NotSigned/
    );
    const result = validateWindowsRelease({
      distRoot: fixture.root,
      archiveReader: archiveFixture(),
      requireSignature: true,
      signatureCheck: validSignature,
    });
    assert.equal(result.appx.architecture, "x64");
  } finally {
    fixture.cleanup();
  }
});

test("unsigned structure mode does not invoke Authenticode for unpacked output", () => {
  const fixture = releaseFixture();
  addUnpacked(fixture);
  try {
    const result = validateWindowsRelease({
      distRoot: fixture.root,
      archiveReader: archiveFixture(),
      expectedExportVersion: EXPECTED_EXPORT_VERSION,
      signatureCheck: () => {
        throw new Error("signature check must not run");
      },
    });
    assert.equal(result.unpacked.signature, null);
  } finally {
    fixture.cleanup();
  }
});

test("rejects root and Electron presentation-export version mismatch", () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "presenton-version-"));
  const electronRoot = path.join(root, "electron");
  try {
    write(
      path.join(root, "package.json"),
      JSON.stringify({ presentationExportVersion: "v0.4.2" })
    );
    write(
      path.join(electronRoot, "package.json"),
      JSON.stringify({ exportVersion: "v0.4.3" })
    );
    assert.throws(
      () => readExpectedExportVersion({ repoRoot: root, electronRoot }),
      /presentation-export version mismatch: root=v0\.4\.2, electron=v0\.4\.3/
    );
  } finally {
    fs.rmSync(root, { recursive: true, force: true });
  }
});
