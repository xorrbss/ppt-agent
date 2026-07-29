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
})
