import { createHash } from "node:crypto";
import { mkdir, readdir, readFile, stat, writeFile } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

const repositoryRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const sourceRoot = path.join(repositoryRoot, "templates", "editorial");
const manifestRoot = path.join(repositoryRoot, "templates", "manifests");
const entrypointPath = path.join(sourceRoot, "template.json");
const checkOnly = process.argv.includes("--check");
const enginePath = path.join(
  repositoryRoot,
  "contracts",
  "template_package",
  "engines",
  "template-v2-json.v1.json",
);

function sha256(value) {
  return createHash("sha256").update(value).digest("hex");
}

async function filesBelow(root, relative = "") {
  const entries = await readdir(path.join(root, relative), { withFileTypes: true });
  const output = [];
  for (const entry of entries.sort((left, right) => left.name.localeCompare(right.name))) {
    const child = path.posix.join(relative.replaceAll("\\", "/"), entry.name);
    if (entry.isSymbolicLink()) throw new Error(`template package symlink rejected: ${child}`);
    if (entry.isDirectory()) output.push(...(await filesBelow(root, child)));
    else if (entry.isFile()) output.push(child);
    else throw new Error(`template package special file rejected: ${child}`);
  }
  return output;
}

function mediaType(relative, bytes) {
  const extension = path.extname(relative).toLowerCase();
  if (extension === ".png" && bytes.subarray(0, 8).equals(Buffer.from("89504e470d0a1a0a", "hex"))) {
    return "image/png";
  }
  if (extension === ".svg" && /<svg(?:\s|>)/i.test(bytes.toString("utf8", 0, 4096))) {
    return "image/svg+xml";
  }
  if (extension === ".ttf" && ["00010000", "4f54544f", "74727565"].includes(bytes.subarray(0, 4).toString("hex"))) {
    return "font/ttf";
  }
  throw new Error(`template package MIME/magic mismatch: ${relative}`);
}

const sourceFiles = await filesBelow(sourceRoot);
if (sourceFiles.length !== 117 || !sourceFiles.includes("template.json")) {
  throw new Error(`unexpected editorial source inventory: ${sourceFiles.length}`);
}

const entrypointBytes = await readFile(entrypointPath);
const template = JSON.parse(entrypointBytes.toString("utf8"));
if (template.id !== "editorial" || template.layouts?.length !== 24) {
  throw new Error("editorial template identity/layout contract changed");
}

const assetFiles = sourceFiles.filter((relative) => relative !== "template.json");
const assets = [];
let totalSizeBytes = 0;
for (const relative of assetFiles) {
  const absolute = path.join(sourceRoot, relative);
  const metadata = await stat(absolute);
  const bytes = await readFile(absolute);
  const type = mediaType(relative, bytes);
  totalSizeBytes += metadata.size;
  assets.push({
    path: relative,
    sha256: sha256(bytes),
    sizeBytes: metadata.size,
    mediaType: type,
    role: relative === "static/thumbnail.png" ? "thumbnail" : type.startsWith("font/") ? "font" : "template-asset",
    licenseRef: type.startsWith("font/") ? "OFL-1.1" : "LicenseRef-Unverified-Upstream-Asset",
  });
}

const inventory = {
  schemaVersion: "presenton.template-package-assets/v1",
  templateId: "editorial",
  files: assets,
};
const inventoryBytes = Buffer.from(`${JSON.stringify(inventory, null, 2)}\n`);
const inventorySha256 = sha256(
  Buffer.from(JSON.stringify({ files: assets }), "utf8"),
);
const engineBytes = await readFile(enginePath);
const engine = JSON.parse(engineBytes.toString("utf8"));
const layoutIds = template.layouts.map((layout) => layout.id);
const entrypointSha256 = sha256(entrypointBytes);
const smoke = {
  schemaVersion: "presenton.template-renderer-smoke/v1",
  templateId: "editorial",
  engineId: engine.engineId,
  engineContractSha256: sha256(engineBytes),
  entrypointSha256,
  networkPolicy: "deny",
  scriptPolicy: "deny",
  viewport: { width: 1280, height: 720 },
  layoutCount: layoutIds.length,
  layoutIds,
  checks: {
    jsonParsed: true,
    uniqueLayoutIds: new Set(layoutIds).size === layoutIds.length,
    nonEmptyRoots: template.layouts.every((layout) => Array.isArray(layout.components) && layout.components.length > 0),
    browserGoldenRequiredForPublication: true,
  },
  status: "passed-static-publication-blocked",
};
const smokeBytes = Buffer.from(`${JSON.stringify(smoke, null, 2)}\n`);
const thumbnail = assets.find((asset) => asset.path === "static/thumbnail.png");
if (!thumbnail) throw new Error("editorial thumbnail is missing");

const manifest = {
  schemaVersion: "presenton.template-package/v1",
  packageVersion: "1.0.0",
  templateId: "editorial",
  sourceRoot: "templates/editorial",
  entrypoint: {
    path: "template.json",
    sha256: entrypointSha256,
    sizeBytes: entrypointBytes.length,
    mediaType: "application/json",
  },
  engine: {
    id: engine.engineId,
    contractVersion: engine.contractVersion,
    contractPath: "contracts/template_package/engines/template-v2-json.v1.json",
    contractSha256: sha256(engineBytes),
    requiredCapabilities: engine.capabilities,
  },
  presentation: { width: 1280, height: 720 },
  assets: {
    inventoryPath: "templates/manifests/editorial.assets.v1.json",
    inventorySha256,
    count: assets.length,
    totalSizeBytes,
  },
  fonts: {
    faces: assets.filter((asset) => asset.mediaType === "font/ttf").map((asset) => asset.path),
    aliases: {
      "DM Sans": "static/DM Sans Regular.ttf",
      "DM Sans Bold": "static/DM Sans Bold.ttf",
      "Playfair Display Bold": "static/Playfair Display Bold Italic.ttf",
    },
    unresolvedAliases: ["Playfair Display", "Playfair Display Semi-Bold Italics", "Playfair Display Bold Italics"],
  },
  license: {
    status: "review-required",
    expression: "Apache-2.0 AND OFL-1.1 AND LicenseRef-Unverified-Upstream-Asset",
    rightsEvidenceId: null,
  },
  provenance: {
    repository: "https://github.com/presenton/presenton",
    commitSha: "e271359ed91bfa48ba18c5717f8e19a7b69326ef",
    sourcePath: "templates/editorial",
    sourceFileCount: sourceFiles.length,
    sourceInventorySha256: inventorySha256,
  },
  thumbnail: {
    assetPath: thumbnail.path,
    sha256: thumbnail.sha256,
    width: 1280,
    height: 720,
  },
  rendererSmoke: {
    receiptPath: "templates/manifests/editorial.renderer-smoke.v1.json",
    receiptSha256: sha256(smokeBytes),
  },
  publication: {
    status: "blocked",
    default: false,
    blockers: [
      "remote_font_reference",
      "unresolved_font_aliases",
      "raster_rights_evidence_missing",
      "browser_golden_receipt_missing",
    ],
  },
};

const outputs = new Map([
  ["editorial.assets.v1.json", inventoryBytes],
  ["editorial.renderer-smoke.v1.json", smokeBytes],
  ["editorial.package.v1.json", Buffer.from(`${JSON.stringify(manifest, null, 2)}\n`)],
]);
await mkdir(manifestRoot, { recursive: true });
for (const [name, expected] of outputs) {
  const destination = path.join(manifestRoot, name);
  if (checkOnly) {
    let current;
    try {
      current = await readFile(destination);
    } catch {
      throw new Error(`template package metadata is missing: ${name}`);
    }
    if (!current.equals(expected)) {
      throw new Error(`template package metadata is stale: ${name}`);
    }
  } else {
    await writeFile(destination, expected);
  }
}
console.log(
  `${checkOnly ? "Verified" : "Generated"} editorial package metadata for ${assets.length} assets (${totalSizeBytes} bytes).`,
);
