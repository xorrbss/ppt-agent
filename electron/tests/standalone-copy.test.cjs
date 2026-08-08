const assert = require("node:assert/strict")
const fs = require("node:fs")
const os = require("node:os")
const path = require("node:path")
const test = require("node:test")
const { pathToFileURL } = require("node:url")

const {
  atomicReplaceDirectory,
  copyTreeSafe,
  materializeTreeInPlace,
  preflightWindowsSymlinkFallback,
  removeMaterializedPnpmStore,
  validateCopiedLinks,
  validateLinkFreeTree,
} = require("../scripts/standalone-copy.cjs")
const {
  installWindowsSymlinkFallback,
} = require("../scripts/next-standalone-symlink-fallback.cjs")

test("Next standalone fallback converts a relative package link EPERM into a junction", async () => {
  const calls = []
  const root = path.join(path.sep, "build", "standalone")
  const traceRoot = path.join(path.sep, "source")
  const destination = path.join(root, "node_modules", "package")
  const realTarget = path.join(traceRoot, ".pnpm", "package")
  const fsImpl = {
    promises: {
      async symlink(target, destination, type) {
        calls.push({ target, destination, type })
        if (calls.length === 1) {
          const error = new Error("symlink privilege is not held")
          error.code = "EPERM"
          throw error
        }
      },
      async realpath(sourceLink) {
        assert.equal(sourceLink, path.resolve(traceRoot, "node_modules", "package"))
        return realTarget
      },
      async stat(target) {
        assert.equal(target, realTarget)
        return { isDirectory: () => true, isFile: () => false }
      },
    },
  }
  const result = installWindowsSymlinkFallback({
    fsImpl,
    platform: "win32",
    standaloneRoot: root,
    tracingRoot: traceRoot,
  })

  const relativeTarget = path.join("..", ".pnpm", "package")
  await fsImpl.promises.symlink(relativeTarget, destination)

  assert.equal(result.installed, true)
  assert.deepEqual(calls, [
    { target: relativeTarget, destination, type: undefined },
    {
      target: realTarget,
      destination,
      type: "junction",
    },
  ])
  result.restore()
})

test("Next standalone fallback never changes symlink behavior outside its output root", async () => {
  let calls = 0
  const primaryError = Object.assign(new Error("denied"), { code: "EPERM" })
  const fsImpl = {
    promises: {
      async symlink() {
        calls += 1
        throw primaryError
      },
    },
  }
  const result = installWindowsSymlinkFallback({
    fsImpl,
    platform: "win32",
    standaloneRoot: path.join(path.sep, "build", "standalone"),
    tracingRoot: path.join(path.sep, "source"),
  })

  await assert.rejects(
    fsImpl.promises.symlink("target", path.join(path.sep, "unrelated", "link")),
    (error) => error === primaryError,
  )
  assert.equal(calls, 1)
  result.restore()
})

test("Next standalone fallback leaves successful symlink creation unchanged", async () => {
  const calls = []
  const fsImpl = {
    promises: {
      async symlink(target, destination, type) {
        calls.push({ target, destination, type })
      },
    },
  }
  const result = installWindowsSymlinkFallback({
    fsImpl,
    platform: "win32",
    standaloneRoot: path.join(path.sep, "build", "standalone"),
    tracingRoot: path.join(path.sep, "source"),
  })

  await fsImpl.promises.symlink(
    "target",
    path.join(path.sep, "build", "standalone", "link"),
  )
  assert.equal(calls.length, 1)
  result.restore()
})

test("Next standalone fallback copies a traced file link when junctions are invalid", async () => {
  const root = path.join(path.sep, "build", "standalone")
  const traceRoot = path.join(path.sep, "source")
  const destination = path.join(root, "node_modules", ".bin", "tool")
  const realTarget = path.join(traceRoot, "package", "tool.js")
  let copied
  const fsImpl = {
    promises: {
      async symlink() {
        throw Object.assign(new Error("access denied"), { code: "EACCES" })
      },
      async realpath(sourceLink) {
        assert.equal(sourceLink, path.resolve(traceRoot, "node_modules", ".bin", "tool"))
        return realTarget
      },
      async stat() {
        return { isDirectory: () => false, isFile: () => true }
      },
      async copyFile(source, target) {
        copied = { source, target }
      },
    },
  }
  const result = installWindowsSymlinkFallback({
    fsImpl,
    platform: "win32",
    standaloneRoot: root,
    tracingRoot: traceRoot,
  })

  await fsImpl.promises.symlink("../tool.js", destination)

  assert.deepEqual(copied, { source: realTarget, target: destination })
  result.restore()
})

test("Next standalone fallback rejects a traced link that resolves outside the source root", async () => {
  const root = path.join(path.sep, "build", "standalone")
  const traceRoot = path.join(path.sep, "source")
  const primaryError = Object.assign(new Error("symlink privilege is not held"), {
    code: "EPERM",
  })
  let statCalled = false
  const fsImpl = {
    promises: {
      async symlink() {
        throw primaryError
      },
      async realpath() {
        return path.join(path.sep, "outside", "package")
      },
      async stat() {
        statCalled = true
      },
    },
  }
  const result = installWindowsSymlinkFallback({
    fsImpl,
    platform: "win32",
    standaloneRoot: root,
    tracingRoot: traceRoot,
  })

  await assert.rejects(
    fsImpl.promises.symlink("../../outside", path.join(root, "node_modules", "package")),
    (error) => error === primaryError,
  )
  assert.equal(statCalled, false)
  result.restore()
})

test("Windows symlink EPERM preflight selects the materialized-copy fallback", () => {
  let removed = false
  const result = preflightWindowsSymlinkFallback({
    platform: "win32",
    temporaryDirectory: "C:\\temp",
    fsImpl: {
      mkdtempSync: () => "C:\\temp\\presenton-link-preflight-test",
      mkdirSync: () => undefined,
      symlinkSync: () => {
        const error = new Error("symlink privilege is not held")
        error.code = "EPERM"
        throw error
      },
      rmSync: (_path, options) => {
        removed = true
        assert.equal(options.maxRetries, 12)
      },
    },
  })

  assert.equal(result.mode, "materialize")
  assert.equal(result.symlinkCapability, "unavailable")
  assert.match(result.reason, /EPERM/)
  assert.match(result.reason, /privilege-free materialized-copy fallback/)
  assert.equal(removed, true)
})

test("Windows symlink EACCES preflight uses the same privilege-free fallback", () => {
  const result = preflightWindowsSymlinkFallback({
    platform: "win32",
    temporaryDirectory: "C:\\temp",
    fsImpl: {
      mkdtempSync: () => "C:\\temp\\presenton-link-preflight-test",
      mkdirSync: () => undefined,
      symlinkSync: () => {
        const error = new Error("access denied")
        error.code = "EACCES"
        throw error
      },
      rmSync: () => undefined,
    },
  })

  assert.equal(result.mode, "materialize")
  assert.equal(result.symlinkCapability, "unavailable")
  assert.match(result.reason, /EACCES/)
})

test("Windows preflight records available symlinks but keeps packages link-free", () => {
  const result = preflightWindowsSymlinkFallback({
    platform: "win32",
    temporaryDirectory: "C:\\temp",
    fsImpl: {
      mkdtempSync: () => "C:\\temp\\presenton-link-preflight-test",
      mkdirSync: () => undefined,
      symlinkSync: () => undefined,
      rmSync: () => undefined,
    },
  })

  assert.deepEqual(result, {
    mode: "materialize",
    symlinkCapability: "available",
    reason: "directory symlink capability available; package policy remains link-free",
  })
})

test("preflight cleanup diagnostics do not block the materialized-copy fallback", () => {
  const result = preflightWindowsSymlinkFallback({
    platform: "win32",
    temporaryDirectory: "C:\\temp",
    fsImpl: {
      mkdtempSync: () => "C:\\temp\\presenton-link-preflight-test",
      mkdirSync: () => undefined,
      symlinkSync: () => {
        const error = new Error("symlink privilege is not held")
        error.code = "EPERM"
        throw error
      },
      rmSync: () => {
        const error = new Error("directory is still in use")
        error.code = "EBUSY"
        throw error
      },
    },
  })

  assert.equal(result.mode, "materialize")
  assert.match(result.cleanupWarning, /EBUSY/)
})

test("non-Windows preflight selects link-free materialization without filesystem probes", () => {
  const fsImpl = new Proxy({}, {
    get: () => {
      throw new Error("filesystem should not be accessed")
    },
  })

  assert.deepEqual(preflightWindowsSymlinkFallback({ platform: "linux", fsImpl }), {
    mode: "materialize",
    symlinkCapability: "not-applicable",
    reason: "link-free standalone copies are required",
  })
})

function fixture(t) {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "presenton-copy-test-"))
  t.after(() => fs.rmSync(root, { recursive: true, force: true }))
  return root
}

function directoryLink(target, linkPath) {
  if (process.platform === "win32") {
    // Directory symlinks require Developer Mode or elevation on Windows.
    // Junctions exercise the same lstat/readlink/realpath code paths without
    // making the standalone-copy suite depend on machine-wide privileges.
    const absoluteTarget = path.resolve(path.dirname(linkPath), target)
    fs.symlinkSync(absoluteTarget, linkPath, "junction")
    return
  }
  fs.symlinkSync(target, linkPath, "dir")
}

test("Next.js standalone tracing includes Sharp native runtime files", async () => {
  const configPath = path.join(__dirname, "..", "..", "servers", "nextjs", "next.config.mjs")
  const config = (await import(pathToFileURL(configPath).href)).default
  const includes = config.outputFileTracingIncludes?.["/*"] || []

  assert.ok(
    includes.includes("node_modules/@img/sharp-*/lib/**/*"),
    "standalone tracing must include npm-installed Sharp native libraries"
  )
  assert.ok(
    includes.includes(
      "node_modules/.pnpm/@img+sharp-*/node_modules/@img/sharp-*/lib/**/*"
    ),
    "standalone tracing must include pnpm-installed Sharp native libraries"
  )
})

test("copies internal links without dereferencing them", (t) => {
  const root = fixture(t)
  const source = path.join(root, "source")
  const destination = path.join(root, "destination")
  fs.mkdirSync(path.join(source, "packages", "dep"), { recursive: true })
  fs.writeFileSync(path.join(source, "packages", "dep", "marker.txt"), "ok")
  fs.mkdirSync(path.join(source, "node_modules"), { recursive: true })
  directoryLink(
    path.join("..", "packages", "dep"),
    path.join(source, "node_modules", "dep")
  )

  copyTreeSafe(source, destination)

  const copiedLink = path.join(destination, "node_modules", "dep")
  assert.equal(fs.lstatSync(copiedLink).isSymbolicLink(), true)
  assert.equal(
    fs.readFileSync(path.join(copiedLink, "marker.txt"), "utf8"),
    "ok"
  )
  validateCopiedLinks(destination)
})

test("materializes internal directory links for link-free packagers", (t) => {
  const root = fixture(t)
  const source = path.join(root, "source")
  const destination = path.join(root, "destination")
  fs.mkdirSync(path.join(source, "packages", "dep"), { recursive: true })
  fs.writeFileSync(path.join(source, "packages", "dep", "marker.txt"), "ok")
  fs.mkdirSync(path.join(source, "node_modules"), { recursive: true })
  directoryLink(
    path.join("..", "packages", "dep"),
    path.join(source, "node_modules", "dep")
  )

  copyTreeSafe(source, destination, { materializeLinks: true })

  const materialized = path.join(destination, "node_modules", "dep")
  assert.equal(fs.lstatSync(materialized).isDirectory(), true)
  assert.equal(fs.lstatSync(materialized).isSymbolicLink(), false)
  assert.equal(
    fs.readFileSync(path.join(materialized, "marker.txt"), "utf8"),
    "ok"
  )
  validateLinkFreeTree(destination)
})

test("materializes a macOS framework-style link hierarchy in place", {
  skip: process.platform === "win32",
}, (t) => {
  const root = fixture(t)
  const framework = path.join(root, "Python.framework")
  const version = path.join(framework, "Versions", "3.11")
  fs.mkdirSync(version, { recursive: true })
  fs.writeFileSync(path.join(version, "Python"), "framework-binary")
  directoryLink("3.11", path.join(framework, "Versions", "Current"))
  fs.symlinkSync(
    path.join("Versions", "Current", "Python"),
    path.join(framework, "Python"),
    "file"
  )

  assert.equal(materializeTreeInPlace(root), 2)
  validateLinkFreeTree(root)
  assert.equal(
    fs.readFileSync(path.join(framework, "Python"), "utf8"),
    "framework-binary"
  )
  assert.equal(
    fs.readFileSync(
      path.join(framework, "Versions", "Current", "Python"),
      "utf8"
    ),
    "framework-binary"
  )
})

test("materializes an internal directory link in place", (t) => {
  const root = fixture(t)
  const target = path.join(root, "Versions", "3.11")
  fs.mkdirSync(target, { recursive: true })
  fs.writeFileSync(path.join(target, "marker.txt"), "ok")
  directoryLink("3.11", path.join(root, "Versions", "Current"))

  assert.equal(materializeTreeInPlace(root), 1)
  validateLinkFreeTree(root)
  assert.equal(
    fs.readFileSync(path.join(root, "Versions", "Current", "marker.txt"), "utf8"),
    "ok"
  )
})

test("does not rewrite an already link-free resource tree", (t) => {
  const root = fixture(t)
  const marker = path.join(root, "fastapi")
  fs.writeFileSync(marker, "binary")

  assert.equal(materializeTreeInPlace(root), 0)
  assert.equal(fs.readFileSync(marker, "utf8"), "binary")
})

test("removes a pnpm store only after packages are materialized", (t) => {
  const root = fixture(t)
  const server = path.join(root, "server")
  const directPackage = path.join(server, "node_modules", "dep")
  const storePackage = path.join(
    server,
    "node_modules",
    ".pnpm",
    "dep@1.0.0",
    "node_modules",
    "dep"
  )
  fs.mkdirSync(directPackage, { recursive: true })
  fs.mkdirSync(storePackage, { recursive: true })
  fs.writeFileSync(path.join(directPackage, "index.js"), "module.exports = 1")
  fs.writeFileSync(path.join(storePackage, "index.js"), "module.exports = 1")

  assert.equal(removeMaterializedPnpmStore(server), true)
  assert.equal(fs.existsSync(path.join(server, "node_modules", ".pnpm")), false)
  assert.equal(fs.existsSync(path.join(directPackage, "index.js")), true)
  assert.equal(removeMaterializedPnpmStore(server), false)
})

test("refuses to prune a pnpm store while package links remain", (t) => {
  const root = fixture(t)
  const server = path.join(root, "server")
  const storePackage = path.join(
    server,
    "node_modules",
    ".pnpm",
    "dep@1.0.0",
    "node_modules",
    "dep"
  )
  fs.mkdirSync(storePackage, { recursive: true })
  fs.writeFileSync(path.join(storePackage, "index.js"), "module.exports = 1")
  directoryLink(
    path.join(".pnpm", "dep@1.0.0", "node_modules", "dep"),
    path.join(server, "node_modules", "dep")
  )

  assert.throws(
    () => removeMaterializedPnpmStore(server),
    /must not contain filesystem links/,
  )
  assert.equal(fs.existsSync(storePackage), true)
})

test("materialization rejects links that resolve outside the source root", (t) => {
  const root = fixture(t)
  const source = path.join(root, "source")
  const external = path.join(root, "external")
  fs.mkdirSync(path.join(source, "targets"), { recursive: true })
  fs.mkdirSync(external)
  fs.writeFileSync(path.join(external, "secret.txt"), "outside")
  directoryLink(external, path.join(source, "targets", "redirect"))
  directoryLink(
    path.join("targets", "redirect"),
    path.join(source, "package")
  )

  assert.throws(
    () => copyTreeSafe(source, path.join(root, "destination"), {
      materializeLinks: true,
      remapExternalLink: () => path.join(source, "targets", "redirect"),
    }),
    /resolves outside source root/
  )
})

test("rejects outside links unless safely remapped", (t) => {
  const root = fixture(t)
  const source = path.join(root, "source")
  const external = path.join(root, "external")
  fs.mkdirSync(path.join(source, "safe-target"), { recursive: true })
  fs.mkdirSync(external)
  directoryLink(external, path.join(source, "external-link"))

  assert.throws(
    () => copyTreeSafe(source, path.join(root, "rejected")),
    /escapes source root/
  )

  const destination = path.join(root, "remapped")
  copyTreeSafe(source, destination, {
    remapExternalLink: () => path.join(source, "safe-target"),
  })
  const copiedLink = path.join(destination, "external-link")
  assert.equal(fs.lstatSync(copiedLink).isSymbolicLink(), true)
  assert.equal(
    path.resolve(path.dirname(copiedLink), fs.readlinkSync(copiedLink)),
    path.join(destination, "safe-target")
  )
})

test("rejects self-referential directory links before copying", (t) => {
  const root = fixture(t)
  const source = path.join(root, "source")
  fs.mkdirSync(path.join(source, "nested"), { recursive: true })
  directoryLink("..", path.join(source, "nested", "back"))

  assert.throws(
    () => copyTreeSafe(source, path.join(root, "destination")),
    /self-referential/
  )
})

test("atomically replaces a validated sibling directory", (t) => {
  const root = fixture(t)
  const target = path.join(root, "bundle")
  const temp = path.join(root, "bundle-temp")
  fs.mkdirSync(target)
  fs.writeFileSync(path.join(target, "old.txt"), "old")
  fs.mkdirSync(temp)
  fs.writeFileSync(path.join(temp, "new.txt"), "new")

  atomicReplaceDirectory(temp, target)

  assert.equal(fs.existsSync(path.join(target, "old.txt")), false)
  assert.equal(fs.readFileSync(path.join(target, "new.txt"), "utf8"), "new")
  assert.equal(fs.existsSync(temp), false)
})

test("restores the old directory when atomic replacement fails", (t) => {
  const root = fixture(t)
  const target = path.join(root, "bundle")
  const missingTemp = path.join(root, "missing-temp")
  fs.mkdirSync(target)
  fs.writeFileSync(path.join(target, "old.txt"), "old")

  assert.throws(
    () => atomicReplaceDirectory(missingTemp, target),
    /ENOENT/
  )
  assert.equal(fs.readFileSync(path.join(target, "old.txt"), "utf8"), "old")
})

test("rejects a dangling link before atomic replacement and preserves target", (t) => {
  const root = fixture(t)
  const source = path.join(root, "source")
  const temp = path.join(root, "bundle-temp")
  const target = path.join(root, "bundle")
  fs.mkdirSync(source)
  directoryLink("missing-package", path.join(source, "optional-package"))
  fs.mkdirSync(target)
  fs.writeFileSync(path.join(target, "known-good.txt"), "valid")

  assert.throws(
    () => copyTreeSafe(source, temp),
    /link target is missing/
  )
  assert.equal(fs.readFileSync(path.join(target, "known-good.txt"), "utf8"), "valid")
  assert.equal(fs.existsSync(path.join(target, "optional-package")), false)
})
