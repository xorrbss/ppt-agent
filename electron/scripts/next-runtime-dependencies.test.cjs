const assert = require("node:assert/strict")
const fs = require("node:fs")
const os = require("node:os")
const path = require("node:path")
const test = require("node:test")
const {
  materializeSharpNativeRuntime,
  materializeTailwindRuntimeClosure,
  resolveProductionClosure,
  verifyTailwindRuntimeClosure,
} = require("./next-runtime-dependencies.cjs")

function writePackage(root, name, version, dependencies = {}) {
  fs.mkdirSync(root, { recursive: true })
  fs.writeFileSync(
    path.join(root, "package.json"),
    JSON.stringify({ name, version, dependencies }),
    "utf8"
  )
  fs.writeFileSync(
    path.join(root, "index.js"),
    `module.exports = ${JSON.stringify(`${name}@${version}`)}\n`,
    "utf8"
  )
}

function fixture() {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "presenton-next-runtime-"))
  const nextjsRoot = path.join(root, "servers", "nextjs")
  const serverRoot = path.join(root, "resources", "nextjs", "servers", "nextjs")
  return {
    root,
    nextjsRoot,
    serverRoot,
    cleanup() {
      fs.rmSync(root, { recursive: true, force: true })
    },
  }
}

test("materializes conflicting transitive versions at resolver-correct paths", () => {
  const current = fixture()
  try {
    const modules = path.join(current.nextjsRoot, "node_modules")
    writePackage(path.join(modules, "tailwindcss"), "tailwindcss", "3.4.19", {
      "glob-parent": "6.0.2",
      parent: "1.0.0",
    })
    writePackage(path.join(modules, "glob-parent"), "glob-parent", "6.0.2")
    const parentRoot = path.join(modules, "parent")
    writePackage(parentRoot, "parent", "1.0.0", { "glob-parent": "5.1.2" })
    writePackage(
      path.join(parentRoot, "node_modules", "glob-parent"),
      "glob-parent",
      "5.1.2"
    )

    const receipt = materializeTailwindRuntimeClosure(current)
    assert.equal(receipt.packageInstanceCount, 3)
    assert.equal(receipt.uniquePackageCount, 3)
    assert.deepEqual(
      receipt.packages
        .filter((entry) => entry.name === "glob-parent")
        .map(({ path: relative, version }) => ({ relative, version })),
      [
        { relative: "glob-parent", version: "6.0.2" },
        {
          relative: "parent/node_modules/glob-parent",
          version: "5.1.2",
        },
      ]
    )
    assert.equal(
      fs.readFileSync(
        path.join(current.serverRoot, "node_modules", "glob-parent", "index.js"),
        "utf8"
      ).trim(),
      'module.exports = "glob-parent@6.0.2"'
    )
    assert.equal(
      fs.readFileSync(
        path.join(
          current.serverRoot,
          "node_modules",
          "parent",
          "node_modules",
          "glob-parent",
          "index.js"
        ),
        "utf8"
      ).trim(),
      'module.exports = "glob-parent@5.1.2"'
    )
    assert.deepEqual(verifyTailwindRuntimeClosure(current), receipt)
  } finally {
    current.cleanup()
  }
})

test("rejects an unsafe production dependency package name", () => {
  const current = fixture()
  try {
    writePackage(
      path.join(current.nextjsRoot, "node_modules", "tailwindcss"),
      "tailwindcss",
      "3.4.19",
      { "../escape": "1.0.0" }
    )
    assert.throws(
      () => resolveProductionClosure(current.nextjsRoot),
      /Unsafe runtime dependency package name: \.\.\/escape/
    )
  } finally {
    current.cleanup()
  }
})

test("materializes the Sharp native package resolved from the installed Sharp version", () => {
  const current = fixture()
  try {
    const modules = path.join(current.nextjsRoot, "node_modules")
    const sharpRoot = path.join(modules, "sharp")
    writePackage(sharpRoot, "sharp", "0.34.5")
    const nativeName = "@img/sharp-win32-x64"
    const compatibleNative = path.join(sharpRoot, "node_modules", ...nativeName.split("/"))
    writePackage(compatibleNative, nativeName, "0.34.5")
    fs.writeFileSync(path.join(compatibleNative, "sharp.node"), "compatible", "utf8")

    const mismatchedNative = path.join(modules, ...nativeName.split("/"))
    writePackage(mismatchedNative, nativeName, "0.35.3")
    fs.mkdirSync(path.join(current.serverRoot, "node_modules", "@img"), {
      recursive: true,
    })
    writePackage(
      path.join(current.serverRoot, "node_modules", ...nativeName.split("/")),
      nativeName,
      "0.35.3"
    )

    const result = materializeSharpNativeRuntime({
      ...current,
      runtimePlatform: "win32-x64",
    })

    assert.deepEqual(result, {
      sharpVersion: "0.34.5",
      nativePackage: nativeName,
      nativeVersion: "0.34.5",
      runtimePlatform: "win32-x64",
    })
    const packagedNative = path.join(
      current.serverRoot,
      "node_modules",
      ...nativeName.split("/")
    )
    assert.equal(
      JSON.parse(
        fs.readFileSync(path.join(packagedNative, "package.json"), "utf8")
      ).version,
      "0.34.5"
    )
    assert.equal(
      fs.readFileSync(path.join(packagedNative, "sharp.node"), "utf8"),
      "compatible"
    )
  } finally {
    current.cleanup()
  }
})
