const assert = require("node:assert/strict")
const fs = require("node:fs")
const path = require("node:path")
const test = require("node:test")
const vm = require("node:vm")

test("electron packaging never publishes as a build side effect", () => {
  const buildPath = path.join(__dirname, "..", "build.js")
  const source = fs.readFileSync(buildPath, "utf8")
  let receivedOptions

  const sandbox = {
    console,
    require(specifier) {
      if (specifier === "electron-builder") {
        return {
          build(options) {
            receivedOptions = options
            return Promise.resolve([])
          },
        }
      }
      return require(specifier)
    },
  }

  vm.runInNewContext(source, sandbox, { filename: buildPath })

  assert.ok(receivedOptions, "build.js should invoke electron-builder")
  assert.equal(receivedOptions.publish, "never")
  assert.deepEqual(
    Array.from(receivedOptions.config.win.target),
    ["nsis", "appx"],
  )
  assert.equal(receivedOptions.config.mac.hardenedRuntime, true)
  assert.equal(receivedOptions.config.mac.entitlements, "build/entitlements.mac.plist")
  assert.equal(receivedOptions.config.afterSign, "scripts/notarize-macos.cjs")
  assert.deepEqual(
    Array.from(receivedOptions.config.extraResources, (entry) => ({
      from: entry.from,
      to: entry.to,
    })),
    [
      { from: "../LICENSE", to: "LICENSE" },
      { from: "../NOTICE", to: "NOTICE" },
      { from: "../compliance/fonts", to: "font-licenses" },
      {
        from: "../docs/experiments/authored-vnext/03-ooxml-roundtrip/compliance/LICENSE.genoffice.txt",
        to: "third-party/genoffice/LICENSE",
      },
      {
        from: "../docs/experiments/authored-vnext/03-ooxml-roundtrip/compliance/NOTICE.genoffice.txt",
        to: "third-party/genoffice/NOTICE",
      },
      {
        from: "../compliance/release-sbom.spdx.json",
        to: "release-sbom.spdx.json",
      },
      {
        from: "../compliance/release-license-policy.json",
        to: "release-license-policy.json",
      },
      {
        from: "../compliance/copyleft-source-offer.md",
        to: "copyleft-source-offer.md",
      },
    ],
  )
})

test("packaged FastAPI keeps standard streams available for Uvicorn", () => {
  const specPath = path.join(
    __dirname,
    "..",
    "..",
    "servers",
    "fastapi",
    "server.spec",
  )
  const source = fs.readFileSync(specPath, "utf8")

  assert.match(
    source,
    /\bconsole=True\b/,
    "Electron hides the child process window; PyInstaller must still provide stdio",
  )
})
