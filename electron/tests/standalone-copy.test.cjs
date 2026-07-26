const assert = require("node:assert/strict")
const fs = require("node:fs")
const os = require("node:os")
const path = require("node:path")
const test = require("node:test")
const { pathToFileURL } = require("node:url")

const {
  atomicReplaceDirectory,
  copyTreeSafe,
  removeMaterializedPnpmStore,
  validateCopiedLinks,
  validateLinkFreeTree,
} = require("../scripts/standalone-copy.cjs")

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
