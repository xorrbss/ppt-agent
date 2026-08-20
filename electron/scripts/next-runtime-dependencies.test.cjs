const assert = require("node:assert/strict")
const fs = require("node:fs")
const os = require("node:os")
const path = require("node:path")
const test = require("node:test")
const {
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
