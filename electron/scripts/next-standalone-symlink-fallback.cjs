const fs = require("fs")
const path = require("path")

const ENABLE_ENV = "PRESENTON_NEXT_STANDALONE_LINK_FALLBACK"
const ROOT_ENV = "PRESENTON_NEXT_STANDALONE_ROOT"
const TRACE_ROOT_ENV = "PRESENTON_NEXT_TRACE_ROOT"

function isWithin(root, candidate) {
  const relative = path.relative(path.resolve(root), path.resolve(candidate))
  return relative === "" || (
    relative !== ".." &&
    !relative.startsWith(`..${path.sep}`) &&
    !path.isAbsolute(relative)
  )
}

function installWindowsSymlinkFallback({
  fsImpl = fs,
  platform = process.platform,
  standaloneRoot = process.env[ROOT_ENV],
  tracingRoot = process.env[TRACE_ROOT_ENV],
} = {}) {
  if (platform !== "win32") {
    return { installed: false, reason: "non-Windows platform" }
  }
  if (!standaloneRoot) {
    return { installed: false, reason: `${ROOT_ENV} is not set` }
  }
  if (!tracingRoot) {
    return { installed: false, reason: `${TRACE_ROOT_ENV} is not set` }
  }

  const originalSymlink = fsImpl.promises.symlink.bind(fsImpl.promises)
  const resolvedRoot = path.resolve(standaloneRoot)
  const resolvedTracingRoot = path.resolve(tracingRoot)

  fsImpl.promises.symlink = async (target, destination, type) => {
    try {
      return await originalSymlink(target, destination, type)
    } catch (error) {
      if (
        (error?.code !== "EPERM" && error?.code !== "EACCES") ||
        type !== undefined ||
        !isWithin(resolvedRoot, destination)
      ) {
        throw error
      }

      // Next.js recreates pnpm package-directory links with their original
      // relative target. Its built-in Windows fallback only converts absolute
      // targets to junctions, so a non-elevated build fails before Presenton's
      // link-free packaging pass can run. Junctions do not require the Windows
      // symlink privilege. Map Next's mirrored output path back to the traced
      // source so directory links can use an absolute junction and file links
      // can be copied. The packaging pass materializes directory links later.
      const tracedLink = path.resolve(
        resolvedTracingRoot,
        path.relative(resolvedRoot, destination),
      )
      if (!isWithin(resolvedTracingRoot, tracedLink)) {
        throw error
      }
      const realTarget = await fsImpl.promises.realpath(tracedLink)
      if (!isWithin(resolvedTracingRoot, realTarget)) {
        throw error
      }
      const targetStat = await fsImpl.promises.stat(realTarget)
      if (targetStat.isDirectory()) {
        return originalSymlink(realTarget, destination, "junction")
      }
      if (targetStat.isFile()) {
        return fsImpl.promises.copyFile(realTarget, destination)
      }
      throw error
    }
  }

  return {
    installed: true,
    reason: `EPERM/EACCES package links under ${resolvedRoot} fall back to junctions`,
    restore() {
      fsImpl.promises.symlink = originalSymlink
    },
  }
}

if (process.env[ENABLE_ENV] === "1") {
  const result = installWindowsSymlinkFallback()
  if (result.installed && process.env.PRESENTON_LINK_FALLBACK_DEBUG === "1") {
    process.stderr.write(`[standalone-link-fallback] ${result.reason}\n`)
  }
}

module.exports = {
  ENABLE_ENV,
  ROOT_ENV,
  TRACE_ROOT_ENV,
  installWindowsSymlinkFallback,
  isWithin,
}
