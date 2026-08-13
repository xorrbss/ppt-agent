const path = require("node:path");
const { writeResourceProvenance } = require("./resource-provenance.cjs");
const { writePolicyProvenance } = require("./authored-renderer-policy-provenance.cjs");

const electronRoot = path.resolve(__dirname, "..");
const workspaceRoot = path.resolve(electronRoot, "..");
const kind = process.argv[2];
const definitions = {
  electron: {
    sourceRoot: path.join(electronRoot, "app"),
    resourceRoot: path.join(electronRoot, "app_dist"),
  },
  fastapi: {
    sourceRoot: path.join(workspaceRoot, "servers", "fastapi"),
    resourceRoot: path.join(electronRoot, "resources", "fastapi"),
  },
  nextjs: {
    sourceRoot: path.join(workspaceRoot, "servers", "nextjs"),
    resourceRoot: path.join(electronRoot, "resources", "nextjs"),
  },
};

if (!Object.hasOwn(definitions, kind)) {
  console.error("Usage: write-resource-provenance.cjs <electron|fastapi|nextjs>");
  process.exitCode = 2;
} else {
  const manifest = writeResourceProvenance({
    kind,
    workspaceRoot,
    ...definitions[kind],
  });
  if (kind === "electron") writePolicyProvenance(electronRoot);
  console.log(`[resource-provenance] ${kind} ${manifest.source.sha256}`);
}
