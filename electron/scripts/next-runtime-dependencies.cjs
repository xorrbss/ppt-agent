const crypto = require("node:crypto")
const fs = require("node:fs")
const path = require("node:path")
const { copyTreeSafe } = require("./standalone-copy.cjs")

const SCHEMA = "presenton.next-runtime-dependency-closure/v1"
const RECEIPT_NAME = ".presenton-runtime-dependencies.json"
const ROOT_PACKAGES = ["tailwindcss"]

function packagePath(root, name) {
  const segments = name.split("/")
  const scoped = name.startsWith("@")
  if (
    segments.some(
      (segment) =>
        !segment ||
        segment === "." ||
        segment === ".." ||
        segment.includes("\\") ||
        !/^[A-Za-z0-9@._-]+$/u.test(segment)
    ) ||
    (scoped ? segments.length !== 2 : segments.length !== 1)
  ) {
    throw new Error(`Unsafe runtime dependency package name: ${name}`)
  }
  return path.join(root, ...segments)
}

function resourcePath(serverRoot, relative) {
  const nodeModulesRoot = path.resolve(serverRoot, "node_modules")
  const target = path.resolve(nodeModulesRoot, ...relative.split("/"))
  const within = path.relative(nodeModulesRoot, target)
  if (
    !relative ||
    within === ".." ||
    within.startsWith(`..${path.sep}`) ||
    path.isAbsolute(within)
  ) {
    throw new Error(`Runtime dependency destination escapes node_modules: ${relative}`)
  }
  return target
}

function readPackage(root) {
  const manifestPath = path.join(root, "package.json")
  const manifest = JSON.parse(fs.readFileSync(manifestPath, "utf8"))
  if (!manifest.name || !manifest.version) {
    throw new Error(`Runtime dependency identity is incomplete: ${manifestPath}`)
  }
  return manifest
}

function resolveDependencyRoot(nextjsRoot, fromRoot, name) {
  let current = path.resolve(fromRoot)
  const boundary = path.resolve(nextjsRoot, "..", "..")
  while (current.startsWith(boundary)) {
    const candidate = packagePath(path.join(current, "node_modules"), name)
    if (fs.statSync(candidate, { throwIfNoEntry: false })?.isDirectory()) {
      return fs.realpathSync(candidate)
    }
    const parent = path.dirname(current)
    if (parent === current) break
    current = parent
  }
  throw new Error(`Production runtime dependency is not installed: ${name} (from ${fromRoot})`)
}

function resolveProductionClosure(nextjsRoot) {
  const resolvedNextjsRoot = path.resolve(nextjsRoot)
  const roots = ROOT_PACKAGES.map((name) => ({
    name,
    root: fs.realpathSync(packagePath(path.join(resolvedNextjsRoot, "node_modules"), name)),
  }))
  const packages = new Map()
  const flatVersions = new Map()
  const pending = []
  const rootIdentities = []

  for (const entry of roots) {
    const manifest = readPackage(entry.root)
    if (manifest.name !== entry.name) {
      throw new Error(`Runtime root package identity mismatch: expected ${entry.name}, found ${manifest.name}`)
    }
    rootIdentities.push({ name: manifest.name, version: manifest.version })
    for (const dependency of Object.keys(manifest.dependencies || {}).sort()) {
      const root = resolveDependencyRoot(resolvedNextjsRoot, entry.root, dependency)
      const dependencyManifest = readPackage(root)
      const relative = dependency.split("/").join(path.posix.sep)
      const flat = {
        name: dependencyManifest.name,
        version: dependencyManifest.version,
        root,
        relative,
      }
      flatVersions.set(dependencyManifest.name, flat)
      pending.push(flat)
    }
  }

  while (pending.length > 0) {
    const request = pending.shift()
    const manifest = readPackage(request.root)
    const existing = packages.get(request.relative)
    if (existing) {
      if (existing.name !== manifest.name || existing.version !== manifest.version) {
        throw new Error(
          `Runtime dependency path conflict at ${request.relative}: ` +
            `${existing.name}@${existing.version} vs ${manifest.name}@${manifest.version}`
        )
      }
      continue
    }
    if (packages.size >= 500) {
      throw new Error("Runtime dependency closure exceeds 500 materialized package instances")
    }
    const entry = {
      name: manifest.name,
      version: manifest.version,
      root: request.root,
      relative: request.relative,
    }
    packages.set(entry.relative, entry)
    for (const dependency of Object.keys(manifest.dependencies || {}).sort()) {
      const root = resolveDependencyRoot(resolvedNextjsRoot, entry.root, dependency)
      const dependencyManifest = readPackage(root)
      const flat = flatVersions.get(dependencyManifest.name)
      const relative =
        flat?.version === dependencyManifest.version
          ? flat.relative
          : path.posix.join(
              entry.relative,
              "node_modules",
              ...dependencyManifest.name.split("/")
            )
      pending.push({
        name: dependencyManifest.name,
        version: dependencyManifest.version,
        root,
        relative,
      })
    }
  }

  return {
    roots: rootIdentities.sort((left, right) => left.name.localeCompare(right.name)),
    packages: [...packages.values()].sort((left, right) =>
      left.relative.localeCompare(right.relative)
    ),
  }
}

function inventoryPackage(root) {
  const files = []
  const pending = [{ absolute: root, relative: "" }]
  while (pending.length > 0) {
    const current = pending.pop()
    for (const entry of fs.readdirSync(current.absolute, { withFileTypes: true })) {
      if (entry.name === "node_modules") continue
      const absolute = path.join(current.absolute, entry.name)
      const relative = current.relative
        ? `${current.relative}/${entry.name}`
        : entry.name
      const stat = fs.statSync(absolute)
      if (stat.isDirectory()) pending.push({ absolute, relative })
      else if (stat.isFile()) files.push({ absolute, relative })
    }
  }
  return files.sort((left, right) => left.relative.localeCompare(right.relative))
}

function packageIdentity(root) {
  const digest = crypto.createHash("sha256")
  let byteCount = 0
  const files = inventoryPackage(root)
  for (const file of files) {
    const content = fs.readFileSync(file.absolute)
    digest.update(Buffer.from(`${file.relative}\0${content.length}\0`, "utf8"))
    digest.update(content)
    digest.update(Buffer.from("\0", "utf8"))
    byteCount += content.length
  }
  return {
    sha256: digest.digest("hex"),
    fileCount: files.length,
    byteCount,
  }
}

function buildReceipt(closure) {
  const uniquePackages = new Set(
    closure.packages.map(({ name, version }) => `${name}@${version}`)
  )
  return {
    schema: SCHEMA,
    roots: closure.roots,
    packageInstanceCount: closure.packages.length,
    uniquePackageCount: uniquePackages.size,
    packages: closure.packages.map(({ name, version, root, relative }) => ({
      name,
      version,
      path: relative,
      ...packageIdentity(root),
    })),
  }
}

function copyPackage(source, destination) {
  fs.mkdirSync(destination, { recursive: true })
  for (const entry of fs.readdirSync(source, { withFileTypes: true })) {
    if (entry.name === "node_modules") continue
    const sourceEntry = path.join(source, entry.name)
    const destinationEntry = path.join(destination, entry.name)
    if (entry.isFile()) fs.copyFileSync(sourceEntry, destinationEntry)
    else {
      copyTreeSafe(sourceEntry, destinationEntry, {
        materializeLinks: true,
      })
    }
  }
}

function materializeTailwindRuntimeClosure({ nextjsRoot, serverRoot }) {
  const closure = resolveProductionClosure(nextjsRoot)
  for (const entry of [...closure.packages].sort(
    (left, right) => left.relative.split("/").length - right.relative.split("/").length
  )) {
    const destination = resourcePath(serverRoot, entry.relative)
    fs.rmSync(destination, { recursive: true, force: true })
    copyPackage(entry.root, destination)
  }
  const receipt = buildReceipt(closure)
  fs.writeFileSync(
    path.join(serverRoot, RECEIPT_NAME),
    `${JSON.stringify(receipt, null, 2)}\n`,
    "utf8"
  )
  return receipt
}

function verifyTailwindRuntimeClosure({ nextjsRoot, serverRoot }) {
  const closure = resolveProductionClosure(nextjsRoot)
  const expected = buildReceipt(closure)
  let observed
  try {
    observed = JSON.parse(fs.readFileSync(path.join(serverRoot, RECEIPT_NAME), "utf8"))
  } catch (error) {
    throw new Error(`Next.js runtime dependency closure receipt is missing or invalid: ${error.message}`)
  }
  if (JSON.stringify(observed) !== JSON.stringify(expected)) {
    throw new Error("Next.js runtime dependency closure receipt is stale; rebuild the Next.js resource")
  }
  for (const entry of expected.packages) {
    const resourceRoot = resourcePath(serverRoot, entry.path)
    let resourceIdentity
    try {
      resourceIdentity = packageIdentity(resourceRoot)
    } catch (error) {
      throw new Error(`Packaged Next.js runtime dependency is missing: ${entry.name}: ${error.message}`)
    }
    for (const key of ["sha256", "fileCount", "byteCount"]) {
      if (resourceIdentity[key] !== entry[key]) {
        throw new Error(
          `Packaged Next.js runtime dependency is stale: ${entry.name} ${key}; ` +
            "rebuild the Next.js resource"
        )
      }
    }
  }
  return expected
}

module.exports = {
  RECEIPT_NAME,
  ROOT_PACKAGES,
  SCHEMA,
  materializeTailwindRuntimeClosure,
  resolveProductionClosure,
  verifyTailwindRuntimeClosure,
}
