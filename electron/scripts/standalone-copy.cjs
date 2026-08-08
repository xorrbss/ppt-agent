const fs = require("fs")
const os = require("os")
const path = require("path")

function preflightWindowsSymlinkFallback({
  fsImpl = fs,
  platform = process.platform,
  temporaryDirectory = os.tmpdir(),
} = {}) {
  // The packager always materializes links, but probe Windows explicitly so a
  // Developer Mode/elevation policy change is recorded instead of surfacing as
  // an opaque EPERM halfway through a standalone copy.
  if (platform !== "win32") {
    return {
      mode: "materialize",
      symlinkCapability: "not-applicable",
      reason: "link-free standalone copies are required",
    }
  }
  const root = fsImpl.mkdtempSync(path.join(temporaryDirectory, "presenton-link-preflight-"))
  let result
  let primaryError
  try {
    const target = path.join(root, "target")
    const link = path.join(root, "link")
    fsImpl.mkdirSync(target)
    fsImpl.symlinkSync(target, link, "dir")
    result = {
      mode: "materialize",
      symlinkCapability: "available",
      reason: "directory symlink capability available; package policy remains link-free",
    }
  } catch (error) {
    if (error?.code === "EPERM" || error?.code === "EACCES") {
      result = {
        mode: "materialize",
        symlinkCapability: "unavailable",
        reason: `directory symlink unavailable (${error.code}); using privilege-free materialized-copy fallback`,
      }
    } else {
      primaryError = error
    }
  } finally {
    try {
      fsImpl.rmSync(root, { recursive: true, force: true, maxRetries: 12, retryDelay: 250 })
    } catch (cleanupError) {
      if (!primaryError && result) {
        result.cleanupWarning = `could not remove symlink preflight directory (${cleanupError.code || "unknown"}): ${cleanupError.message}`
      }
    }
  }
  if (primaryError) throw primaryError
  return result
}

function isWithin(root, candidate) {
  const relative = path.relative(root, candidate)
  return relative === "" || (
    relative !== ".." &&
    !relative.startsWith(`..${path.sep}`) &&
    !path.isAbsolute(relative)
  )
}

function collectLinks(sourceRoot) {
  const links = []
  function visit(directory) {
    for (const name of fs.readdirSync(directory)) {
      const sourcePath = path.join(directory, name)
      const stat = fs.lstatSync(sourcePath)
      if (stat.isSymbolicLink()) {
        links.push({
          sourcePath,
          rawTarget: fs.readlinkSync(sourcePath),
        })
      } else if (stat.isDirectory()) {
        visit(sourcePath)
      }
    }
  }
  visit(sourceRoot)
  return links
}

function firstLinkInPath(targetPath, linkPaths) {
  let match = null
  for (const linkPath of linkPaths) {
    if (
      targetPath === linkPath ||
      targetPath.startsWith(`${linkPath}${path.sep}`)
    ) {
      if (!match || linkPath.length > match.length) match = linkPath
    }
  }
  return match
}

function assertNoLinkCycles(linkTargets) {
  const linkPaths = [...linkTargets.keys()]
  const edges = new Map()
  for (const [linkPath, targetPath] of linkTargets) {
    if (
      linkPath === targetPath ||
      linkPath.startsWith(`${targetPath}${path.sep}`)
    ) {
      throw new Error(
        `Standalone link is self-referential: ${linkPath} -> ${targetPath}`
      )
    }
    const nextLink = firstLinkInPath(targetPath, linkPaths)
    if (nextLink) edges.set(linkPath, nextLink)
  }

  const visiting = new Set()
  const visited = new Set()
  function visit(linkPath) {
    if (visiting.has(linkPath)) {
      throw new Error(`Standalone link cycle detected at: ${linkPath}`)
    }
    if (visited.has(linkPath)) return
    visiting.add(linkPath)
    const nextLink = edges.get(linkPath)
    if (nextLink) visit(nextLink)
    visiting.delete(linkPath)
    visited.add(linkPath)
  }
  for (const linkPath of linkPaths) visit(linkPath)
}

function planLinks(sourceRoot, remapExternalLink) {
  const absoluteRoot = path.resolve(sourceRoot)
  const plans = new Map()
  const linkTargets = new Map()
  for (const link of collectLinks(absoluteRoot)) {
    const originalTarget = path.resolve(
      path.dirname(link.sourcePath),
      link.rawTarget
    )
    let sourceTarget = originalTarget
    if (!isWithin(absoluteRoot, sourceTarget)) {
      sourceTarget = remapExternalLink?.({
        sourceRoot: absoluteRoot,
        linkPath: link.sourcePath,
        rawTarget: link.rawTarget,
        resolvedTarget: originalTarget,
      })
      if (!sourceTarget) {
        throw new Error(
          `Standalone link escapes source root: ${link.sourcePath} -> ${originalTarget}`
        )
      }
      sourceTarget = path.resolve(sourceTarget)
    }
    if (!isWithin(absoluteRoot, sourceTarget)) {
      throw new Error(
        `Remapped standalone link still escapes source root: ${link.sourcePath} -> ${sourceTarget}`
      )
    }
    let realTarget
    try {
      realTarget = fs.realpathSync(sourceTarget)
    } catch (error) {
      throw new Error(
        `Standalone source link target is missing: ${link.sourcePath} -> ${sourceTarget}`,
        { cause: error }
      )
    }
    if (!isWithin(absoluteRoot, realTarget)) {
      throw new Error(
        `Standalone source link resolves outside source root: ${link.sourcePath} -> ${realTarget}`
      )
    }
    plans.set(link.sourcePath, { sourceTarget, realTarget })
    linkTargets.set(link.sourcePath, sourceTarget)
  }
  assertNoLinkCycles(linkTargets)
  return plans
}

function linkTypeFor(targetPath) {
  try {
    return fs.lstatSync(targetPath).isDirectory() ? "dir" : "file"
  } catch {
    // pnpm can retain a dangling optional-dependency directory link.
    return "dir"
  }
}

function copyTreeSafe(sourceRoot, destinationRoot, options = {}) {
  const absoluteSource = path.resolve(sourceRoot)
  const absoluteDestination = path.resolve(destinationRoot)
  if (isWithin(absoluteSource, absoluteDestination)) {
    throw new Error("Standalone destination must be outside the source tree")
  }
  const linkPlans = planLinks(absoluteSource, options.remapExternalLink)

  function copy(sourcePath, destinationPath, activeDirectories = new Set()) {
    const stat = fs.lstatSync(sourcePath)
    if (stat.isSymbolicLink()) {
      const { sourceTarget, realTarget } = linkPlans.get(sourcePath)
      if (options.materializeLinks) {
        copy(realTarget, destinationPath, activeDirectories)
        return
      }
      const destinationTarget = path.join(
        absoluteDestination,
        path.relative(absoluteSource, sourceTarget)
      )
      const relativeTarget =
        path.relative(path.dirname(destinationPath), destinationTarget) || "."
      fs.mkdirSync(path.dirname(destinationPath), { recursive: true })
      const linkType = linkTypeFor(sourceTarget)
      if (process.platform === "win32" && linkType === "dir") {
        // Creating directory symlinks requires Developer Mode or elevation on
        // Windows. Junctions preserve link semantics for standalone package
        // directories and accept the absolute destination target directly.
        fs.symlinkSync(destinationTarget, destinationPath, "junction")
      } else {
        fs.symlinkSync(relativeTarget, destinationPath, linkType)
      }
      return
    }
    if (stat.isDirectory()) {
      const realDirectory = fs.realpathSync(sourcePath)
      if (activeDirectories.has(realDirectory)) {
        throw new Error(
          `Standalone directory cycle detected while materializing links: ${sourcePath}`
        )
      }
      const nextActiveDirectories = new Set(activeDirectories)
      nextActiveDirectories.add(realDirectory)
      fs.mkdirSync(destinationPath, { recursive: true })
      for (const name of fs.readdirSync(sourcePath)) {
        copy(
          path.join(sourcePath, name),
          path.join(destinationPath, name),
          nextActiveDirectories
        )
      }
      return
    }
    if (!stat.isFile()) {
      throw new Error(`Unsupported standalone filesystem entry: ${sourcePath}`)
    }
    fs.mkdirSync(path.dirname(destinationPath), { recursive: true })
    fs.copyFileSync(sourcePath, destinationPath)
    fs.chmodSync(destinationPath, stat.mode)
  }

  copy(absoluteSource, absoluteDestination)
  validateCopiedLinks(absoluteDestination)
  if (options.materializeLinks) validateLinkFreeTree(absoluteDestination)
}

function validateCopiedLinks(destinationRoot) {
  const absoluteRoot = path.resolve(destinationRoot)
  const realRoot = fs.realpathSync(absoluteRoot)
  const linkTargets = new Map()
  for (const link of collectLinks(absoluteRoot)) {
    const target = path.resolve(path.dirname(link.sourcePath), link.rawTarget)
    if (!isWithin(absoluteRoot, target)) {
      throw new Error(
        `Copied standalone link escapes destination root: ${link.sourcePath} -> ${target}`
      )
    }
    let realTarget
    try {
      realTarget = fs.realpathSync(target)
    } catch (error) {
      throw new Error(
        `Copied standalone link target is missing: ${link.sourcePath} -> ${target}`,
        { cause: error }
      )
    }
    if (!isWithin(realRoot, realTarget)) {
      throw new Error(
        `Copied standalone link resolves outside destination root: ${link.sourcePath} -> ${realTarget}`
      )
    }
    linkTargets.set(link.sourcePath, target)
  }
  assertNoLinkCycles(linkTargets)
}

function validateLinkFreeTree(root) {
  const absoluteRoot = path.resolve(root)
  const links = collectLinks(absoluteRoot)
  if (links.length > 0) {
    throw new Error(
      `Packaged standalone must not contain filesystem links: ${links[0].sourcePath}`
    )
  }
}

function removeMaterializedPnpmStore(serverRoot) {
  const absoluteRoot = path.resolve(serverRoot)
  validateLinkFreeTree(absoluteRoot)
  const store = path.join(absoluteRoot, "node_modules", ".pnpm")
  if (!fs.existsSync(store)) {
    return false
  }
  fs.rmSync(store, { recursive: true, force: true })
  return true
}

function atomicReplaceDirectory(tempDirectory, targetDirectory) {
  const temp = path.resolve(tempDirectory)
  const target = path.resolve(targetDirectory)
  if (path.dirname(temp) !== path.dirname(target)) {
    throw new Error("Atomic replacement requires temp and target to be siblings")
  }
  const backup = `${target}.backup-${process.pid}-${Date.now()}`
  const hadTarget = fs.existsSync(target)
  try {
    if (hadTarget) fs.renameSync(target, backup)
    fs.renameSync(temp, target)
  } catch (error) {
    if (hadTarget && fs.existsSync(backup) && !fs.existsSync(target)) {
      fs.renameSync(backup, target)
    }
    throw error
  }
  if (hadTarget) fs.rmSync(backup, { recursive: true, force: true })
}

function materializeTreeInPlace(rootDirectory) {
  const root = path.resolve(rootDirectory)
  const links = collectLinks(root)
  if (links.length === 0) {
    validateLinkFreeTree(root)
    return 0
  }

  const temp = `${root}.materialized-${process.pid}-${Date.now()}`
  try {
    copyTreeSafe(root, temp, { materializeLinks: true })
    atomicReplaceDirectory(temp, root)
    validateLinkFreeTree(root)
  } catch (error) {
    if (fs.existsSync(temp)) {
      fs.rmSync(temp, { recursive: true, force: true })
    }
    throw error
  }
  return links.length
}

module.exports = {
  atomicReplaceDirectory,
  copyTreeSafe,
  isWithin,
  materializeTreeInPlace,
  preflightWindowsSymlinkFallback,
  removeMaterializedPnpmStore,
  validateCopiedLinks,
  validateLinkFreeTree,
}
