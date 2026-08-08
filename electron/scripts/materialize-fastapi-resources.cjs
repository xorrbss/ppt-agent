const path = require("node:path")

const { materializeTreeInPlace } = require("./standalone-copy.cjs")

const fastapiRoot = path.join(__dirname, "..", "resources", "fastapi")
const materializedLinks = materializeTreeInPlace(fastapiRoot)

if (materializedLinks === 0) {
  console.log("FastAPI resources are already link-free")
} else {
  console.log(
    `Materialized ${materializedLinks} FastAPI resource link(s) for electron-builder`
  )
}
