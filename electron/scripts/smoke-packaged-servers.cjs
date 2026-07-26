const fs = require("node:fs")
const net = require("node:net")
const os = require("node:os")
const path = require("node:path")
const { spawn } = require("node:child_process")

const {
  resolveNextStandalone,
  validatePackagingInputs,
} = require("./verify-packaging-inputs.cjs")

const electronRoot = path.resolve(__dirname, "..")
const resourcesRoot = path.join(electronRoot, "resources")
const startupTimeoutMs = Number(
  process.env.PRESENTON_SMOKE_STARTUP_TIMEOUT_MS || 120000,
)

function allocatePort() {
  return new Promise((resolve, reject) => {
    const server = net.createServer()
    server.unref()
    server.once("error", reject)
    server.listen(0, "127.0.0.1", () => {
      const address = server.address()
      const port = typeof address === "object" && address ? address.port : null
      server.close((error) => {
        if (error) {
          reject(error)
        } else if (!port) {
          reject(new Error("Failed to allocate a smoke-test port"))
        } else {
          resolve(port)
        }
      })
    })
  })
}

function startProcess(command, args, options) {
  const child = spawn(command, args, {
    ...options,
    stdio: ["ignore", "pipe", "pipe"],
    windowsHide: true,
  })
  let output = ""
  const capture = (data) => {
    output += data.toString()
    if (output.length > 20000) output = output.slice(-20000)
  }
  child.stdout.on("data", capture)
  child.stderr.on("data", capture)
  return { child, output: () => output }
}

async function waitForResponse(url, options = {}) {
  const deadline = Date.now() + startupTimeoutMs
  let lastError
  while (Date.now() < deadline) {
    try {
      const response = await fetch(url, options)
      if (response.status >= 200 && response.status < 500) return response
      lastError = new Error(`Unexpected HTTP ${response.status} from ${url}`)
    } catch (error) {
      lastError = error
    }
    await new Promise((resolve) => setTimeout(resolve, 250))
  }
  throw new Error(
    `Timed out waiting for ${url}: ${lastError?.message || "no response"}`,
  )
}

async function stopProcess(processInfo) {
  const child = processInfo?.child
  if (!child || child.exitCode !== null || child.signalCode !== null) return
  child.kill()
  await Promise.race([
    new Promise((resolve) => child.once("exit", resolve)),
    new Promise((resolve) => setTimeout(resolve, 5000)),
  ])
  if (child.exitCode === null && child.signalCode === null) child.kill("SIGKILL")
}

async function main() {
  const validation = validatePackagingInputs()
  const appDataDirectory = fs.mkdtempSync(
    path.join(os.tmpdir(), "presenton-packaged-smoke-"),
  )
  const fastapiRoot = path.join(resourcesRoot, "fastapi")
  const fastapiBinary = path.join(
    fastapiRoot,
    process.platform === "win32" ? "fastapi.exe" : "fastapi",
  )
  const nextRoot = path.join(resourcesRoot, "nextjs")
  const nextStandalone = resolveNextStandalone(nextRoot)
  const [fastapiPort, nextPort] = await Promise.all([
    allocatePort(),
    allocatePort(),
  ])
  let fastapi
  let nextjs

  try {
    fastapi = startProcess(
      fastapiBinary,
      ["--port", fastapiPort.toString()],
      {
        cwd: fastapiRoot,
        env: {
          ...process.env,
          APP_DATA_DIRECTORY: appDataDirectory,
          DISABLE_AUTH: "true",
          MIGRATE_DATABASE_ON_STARTUP: "True",
        },
      },
    )
    const docs = await waitForResponse(
      `http://127.0.0.1:${fastapiPort}/docs`,
    )
    if (docs.status !== 200) {
      throw new Error(`Packaged FastAPI /docs returned HTTP ${docs.status}`)
    }

    nextjs = startProcess(
      process.execPath,
      [nextStandalone.serverScript],
      {
        cwd: nextStandalone.serverRoot,
        env: {
          ...process.env,
          APP_DATA_DIRECTORY: appDataDirectory,
          DISABLE_AUTH: "true",
          FAST_API_INTERNAL_URL: `http://127.0.0.1:${fastapiPort}`,
          HOSTNAME: "127.0.0.1",
          PORT: nextPort.toString(),
        },
      },
    )
    const home = await waitForResponse(`http://127.0.0.1:${nextPort}/`)
    if (home.status !== 200) {
      throw new Error(`Packaged Next.js root returned HTTP ${home.status}`)
    }

    const tailwind = await waitForResponse(
      `http://127.0.0.1:${nextPort}/api/runtime-tailwind-css`,
      {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({
          sources: ['<div class="p-4 text-red-500">smoke</div>'],
        }),
      },
    )
    const tailwindBody = await tailwind.json()
    const tailwindCss =
      tailwindBody && typeof tailwindBody.css === "string"
        ? tailwindBody.css
        : ""
    if (tailwind.status !== 200 || !tailwindCss.includes(".text-red-500")) {
      throw new Error(
        `Packaged runtime Tailwind failed: HTTP ${tailwind.status}, ` +
          `${tailwindCss.length} bytes`,
      )
    }

    console.log("[packaged-servers-smoke] OK")
    console.log(`  - preflight: ${validation.nextLayout}, ${validation.fileCount} files`)
    console.log(`  - FastAPI: /docs HTTP ${docs.status}`)
    console.log(`  - Next.js: / HTTP ${home.status}`)
    console.log(
      `  - runtime Tailwind: HTTP ${tailwind.status}, ${tailwindCss.length} bytes`,
    )
  } catch (error) {
    const logs = [
      fastapi?.output() && `FastAPI output:\n${fastapi.output()}`,
      nextjs?.output() && `Next.js output:\n${nextjs.output()}`,
    ].filter(Boolean)
    throw new Error(`${error.message}${logs.length ? `\n${logs.join("\n")}` : ""}`)
  } finally {
    await Promise.all([stopProcess(nextjs), stopProcess(fastapi)])
    fs.rmSync(appDataDirectory, { recursive: true, force: true })
  }
}

if (require.main === module) {
  main().catch((error) => {
    console.error(`[packaged-servers-smoke] ${error.message}`)
    process.exitCode = 1
  })
}
