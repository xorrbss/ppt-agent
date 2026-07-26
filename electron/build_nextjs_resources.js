const fs = require("fs")
const path = require("path")
const { spawnSync } = require("child_process")
const {
  atomicReplaceDirectory,
  copyTreeSafe,
  removeMaterializedPnpmStore,
  validateCopiedLinks,
  validateLinkFreeTree,
} = require("./scripts/standalone-copy.cjs")

const electronRoot = __dirname
const nextjsDir = path.join(electronRoot, "..", "servers", "nextjs")
const outDir = path.join(electronRoot, "resources", "nextjs")
const nextBuildDir = path.join(nextjsDir, ".next-build")
const standaloneDir = path.join(nextBuildDir, "standalone")

function rm(p) {
  fs.rmSync(p, { recursive: true, force: true })
}

function cpDir(src, dest) {
  copyTreeSafe(src, dest, { materializeLinks: true })
}

console.log("Running Next.js production build (BUILD_TARGET=electron)…")

rm(nextBuildDir)

const npmCmd =
  process.platform === "win32"
    ? process.env.ComSpec || "cmd.exe"
    : "npm"
const npmArgs =
  process.platform === "win32"
    ? ["/d", "/s", "/c", "npm.cmd run build"]
    : ["run", "build"]
const build = spawnSync(npmCmd, npmArgs, {
  cwd: nextjsDir,
  env: { ...process.env, BUILD_TARGET: "electron" },
  stdio: "inherit",
  // npm.cmd needs cmd.exe on Windows. Invoke it explicitly so Node does not
  // concatenate unescaped arguments through shell:true (DEP0190).
  shell: false,
})

if (build.error) {
  console.error(build.error)
  process.exit(1)
}
if (build.status !== 0) {
  process.exit(build.status ?? 1)
}

if (!fs.existsSync(standaloneDir)) {
  console.error("Expected standalone output at:", standaloneDir)
  process.exit(1)
}

const resourcesDir = path.dirname(outDir)
fs.mkdirSync(resourcesDir, { recursive: true })
const tempOutDir = fs.mkdtempSync(path.join(resourcesDir, ".nextjs-copy-"))

function remapNextjsExternalLink({ resolvedTarget }) {
  const relativeToNextjs = path.relative(nextjsDir, resolvedTarget)
  if (
    relativeToNextjs === ".." ||
    relativeToNextjs.startsWith(`..${path.sep}`) ||
    path.isAbsolute(relativeToNextjs)
  ) {
    return null
  }
  const bundledTarget = path.join(
    standaloneDir,
    "servers",
    "nextjs",
    relativeToNextjs
  )
  return fs.existsSync(bundledTarget) ? bundledTarget : null
}

try {
  copyTreeSafe(standaloneDir, tempOutDir, {
    materializeLinks: true,
    remapExternalLink: remapNextjsExternalLink,
  })

  // Next.js 16 standalone traces the app under servers/nextjs/. Older
  // standalone layouts put server.js at the bundle root. Assets only need to
  // live beside the selected server; copying them to both locations adds tens
  // of MiB without adding a runtime fallback.
  const nestedStandaloneDir = path.join(tempOutDir, "servers", "nextjs")
  const directServer = path.join(tempOutDir, "server.js")
  const nestedServer = path.join(nestedStandaloneDir, "server.js")
  const serverDir = fs.existsSync(directServer)
    ? tempOutDir
    : fs.existsSync(nestedServer)
      ? nestedStandaloneDir
      : null
  if (!serverDir) {
    throw new Error("Standalone bundle is missing server.js")
  }

  // copyTreeSafe materializes pnpm links for AppX compatibility. Once every
  // package is a real directory, the traced .pnpm backing store is redundant.
  if (removeMaterializedPnpmStore(serverDir)) {
    console.log("Removed materialized pnpm backing store from:", serverDir)
  }

  const staticSrc = path.join(nextBuildDir, "static")
  if (fs.existsSync(staticSrc)) {
    cpDir(staticSrc, path.join(serverDir, ".next-build", "static"))
  } else {
    throw new Error(`Expected Next.js static output at: ${staticSrc}`)
  }

  const publicDir = path.join(nextjsDir, "public")
  if (fs.existsSync(publicDir)) {
    cpDir(publicDir, path.join(serverDir, "public"))
  }

  const templatesSrc = path.join(nextjsDir, "app", "presentation-templates")
  const templatesDest = path.join(tempOutDir, "presentation-templates")
  if (fs.existsSync(templatesSrc)) {
    cpDir(templatesSrc, templatesDest)
  }

  for (const requiredDirectory of [
    path.join(serverDir, ".next-build", "static"),
    path.join(serverDir, "public"),
  ]) {
    if (!fs.existsSync(requiredDirectory)) {
      throw new Error(`Standalone bundle is missing: ${requiredDirectory}`)
    }
  }
  validateCopiedLinks(tempOutDir)
  validateLinkFreeTree(tempOutDir)
  atomicReplaceDirectory(tempOutDir, outDir)
} catch (error) {
  rm(tempOutDir)
  throw error
}

console.log("Next.js bundle copied to:", outDir)
