import { createHash } from "node:crypto";
import { execFileSync } from "node:child_process";
import { mkdir, readdir, readFile, stat, writeFile } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

const repositoryRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const sourceRoot = path.join(repositoryRoot, "templates", "editorial");
const manifestRoot = path.join(repositoryRoot, "templates", "manifests");
const goldenRoot = path.join(manifestRoot, "editorial.browser-golden");
const captureEvidencePath = path.join(
  manifestRoot,
  "editorial.browser-golden.capture.v1.json",
);
const entrypointPath = path.join(sourceRoot, "template.json");
const checkOnly = process.argv.includes("--check");
const enginePath = path.join(
  repositoryRoot,
  "contracts",
  "template_package",
  "engines",
  "template-v2-json.v1.json",
);
const rendererPath = path.join(
  repositoryRoot,
  "servers",
  "nextjs",
  "lib",
  "template-v2-json-to-html.ts",
);
const upstreamCommit = "e271359ed91bfa48ba18c5717f8e19a7b69326ef";
const googleFontsCommit = "8b0a1d0f5983c89bc2b93f1b5fb55f9e252744b5";
const playfairOflSha256 = "566be814f8e96e93dfa16101331557eb6b5467e9e03f627c0910fe93ca12300e";
const dmSansOflSha256 = "9af36190332437f5ecd09974de43c1f7c77a310a996cdd8ceb25628b458840e1";

function sha256(value) {
  return createHash("sha256").update(value).digest("hex");
}

function gitBlobSha1(value) {
  return createHash("sha1")
    .update(Buffer.from(`blob ${value.length}\0`))
    .update(value)
    .digest("hex");
}

function upstreamTree() {
  const output = execFileSync(
    "git",
    ["ls-tree", "-r", "-z", upstreamCommit, "templates/editorial"],
    { cwd: repositoryRoot, encoding: "buffer", maxBuffer: 16 * 1024 * 1024 },
  );
  return new Map(
    output
      .toString("utf8")
      .split("\0")
      .filter(Boolean)
      .map((line) => {
        const match = /^\d+ blob ([0-9a-f]{40})\t(.+)$/.exec(line);
        if (!match) throw new Error(`unexpected upstream tree record: ${line}`);
        return [match[2].replace(/^templates\/editorial\//, ""), match[1]];
      }),
  );
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
const upstreamAssets = upstreamTree();
const assets = [];
let totalSizeBytes = 0;
for (const relative of assetFiles) {
  const absolute = path.join(sourceRoot, relative);
  const metadata = await stat(absolute);
  const bytes = await readFile(absolute);
  const type = mediaType(relative, bytes);
  const upstreamBlobSha1 = upstreamAssets.get(relative);
  if (!upstreamBlobSha1 || upstreamBlobSha1 !== gitBlobSha1(bytes)) {
    throw new Error(`editorial asset diverged from upstream ${upstreamCommit}: ${relative}`);
  }
  totalSizeBytes += metadata.size;
  assets.push({
    path: relative,
    sha256: sha256(bytes),
    sizeBytes: metadata.size,
    mediaType: type,
    role: relative === "static/thumbnail.png" ? "thumbnail" : type.startsWith("font/") ? "font" : "template-asset",
    licenseRef: type.startsWith("font/") ? "OFL-1.1" : "Apache-2.0",
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
const upstreamLicenseBytes = execFileSync(
  "git",
  ["show", `${upstreamCommit}:LICENSE`],
  { cwd: repositoryRoot, encoding: "buffer", maxBuffer: 1024 * 1024 },
);
const upstreamLicenseBlobSha1 = execFileSync(
  "git",
  ["rev-parse", `${upstreamCommit}:LICENSE`],
  { cwd: repositoryRoot, encoding: "utf8", maxBuffer: 1024 * 1024 },
).trim();
const rightsEvidenceId = "presenton-editorial-assets-apache-2.0";
const provenance = {
  schemaVersion: "presenton.template-package-provenance/v1",
  templateId: "editorial",
  upstream: {
    repository: "https://github.com/presenton/presenton",
    commitSha: upstreamCommit,
    sourcePath: "templates/editorial",
    commitUrl: `https://github.com/presenton/presenton/commit/${upstreamCommit}`,
    pullRequestUrl: "https://github.com/presenton/presenton/pull/813",
    license: {
      expression: "Apache-2.0",
      path: "LICENSE",
      sha256: sha256(upstreamLicenseBytes),
      upstreamGitBlobSha1: upstreamLicenseBlobSha1,
      url: `https://github.com/presenton/presenton/blob/${upstreamCommit}/LICENSE`,
    },
  },
  evidenceSources: [
    {
      id: "upstream-commit",
      type: "git-commit",
      url: `https://github.com/presenton/presenton/commit/${upstreamCommit}`,
      commitSha: upstreamCommit,
    },
    {
      id: "upstream-license",
      type: "repository-license",
      licenseExpression: "Apache-2.0",
      url: `https://github.com/presenton/presenton/blob/${upstreamCommit}/LICENSE`,
      sha256: sha256(upstreamLicenseBytes),
      upstreamGitBlobSha1: upstreamLicenseBlobSha1,
    },
    {
      id: "upstream-pr",
      type: "pull-request",
      url: "https://github.com/presenton/presenton/pull/813",
      assertion: "The Editorial template was merged with bundled fonts and assets.",
    },
    {
      id: "google-fonts-playfair-ofl",
      type: "font-license",
      licenseExpression: "OFL-1.1",
      url: `https://github.com/google/fonts/blob/${googleFontsCommit}/ofl/playfairdisplay/OFL.txt`,
      commitSha: googleFontsCommit,
      sha256: playfairOflSha256,
    },
    {
      id: "google-fonts-dm-sans-ofl",
      type: "font-license",
      licenseExpression: "OFL-1.1",
      url: `https://github.com/google/fonts/blob/${googleFontsCommit}/ofl/dmsans/OFL.txt`,
      commitSha: googleFontsCommit,
      sha256: dmSansOflSha256,
    },
  ],
  claims: [
    {
      id: rightsEvidenceId,
      status: "verified",
      licenseExpression: "Apache-2.0",
      evidenceSourceIds: ["upstream-commit", "upstream-license", "upstream-pr"],
      coveredAssets: assets
        .filter((asset) => ["image/png", "image/svg+xml"].includes(asset.mediaType))
        .map((asset) => ({
          path: asset.path,
          sha256: asset.sha256,
          upstreamGitBlobSha1: upstreamAssets.get(asset.path),
        })),
    },
    {
      id: "presenton-editorial-fonts-ofl-1.1",
      status: "verified",
      licenseExpression: "OFL-1.1",
      evidenceSourceIds: [
        "upstream-commit",
        "google-fonts-playfair-ofl",
        "google-fonts-dm-sans-ofl",
      ],
      coveredAssets: assets
        .filter((asset) => asset.mediaType === "font/ttf")
        .map((asset) => ({
          path: asset.path,
          sha256: asset.sha256,
          upstreamGitBlobSha1: upstreamAssets.get(asset.path),
          evidenceSourceIds: [
            "upstream-commit",
            asset.path.startsWith("static/DM Sans")
              ? "google-fonts-dm-sans-ofl"
              : "google-fonts-playfair-ofl",
          ],
        })),
    },
  ],
};
const provenanceBytes = Buffer.from(`${JSON.stringify(provenance, null, 2)}\n`);
const rendererBytes = await readFile(rendererPath);
const goldenNames = (await readdir(goldenRoot))
  .filter((name) => name.endsWith(".png"))
  .sort((left, right) => left.localeCompare(right));
const expectedGoldenNames = layoutIds.map(
  (layoutId, index) => `${String(index + 1).padStart(2, "0")}-${layoutId}.png`,
);
if (JSON.stringify(goldenNames) !== JSON.stringify(expectedGoldenNames)) {
  throw new Error("editorial browser golden screenshot set is incomplete or stale");
}
const screenshots = [];
for (const [index, name] of goldenNames.entries()) {
  const bytes = await readFile(path.join(goldenRoot, name));
  if (!bytes.subarray(0, 8).equals(Buffer.from("89504e470d0a1a0a", "hex"))) {
    throw new Error(`editorial browser golden is not PNG: ${name}`);
  }
  screenshots.push({
    layoutId: layoutIds[index],
    path: `templates/manifests/editorial.browser-golden/${name}`,
    sha256: sha256(bytes),
    sizeBytes: bytes.length,
    width: 1280,
    height: 720,
  });
}
const captureEvidenceBytes = await readFile(captureEvidencePath);
const captureEvidence = JSON.parse(captureEvidenceBytes.toString("utf8"));
const captureChecks = captureEvidence.checks;
if (
  captureEvidence.schemaVersion !== "presenton.template-browser-capture-evidence/v1" ||
  captureEvidence.templateId !== "editorial" ||
  captureEvidence.rendererModuleSha256 !== sha256(rendererBytes) ||
  captureEvidence.entrypointSha256 !== entrypointSha256 ||
  JSON.stringify(captureEvidence.viewport) !==
    JSON.stringify({ width: 1280, height: 720, deviceScaleFactor: 1 }) ||
  captureEvidence.status !== "passed-browser-capture" ||
  !Array.isArray(captureEvidence.screenshots) ||
  captureEvidence.screenshots.length !== screenshots.length ||
  !captureEvidence.screenshots.every(
    (item, index) =>
      item.layoutId === screenshots[index].layoutId &&
      item.goldenSha256 === screenshots[index].sha256 &&
      item.exact === true &&
      item.badPixelRatio === 0 &&
      item.meanAbsoluteError === 0,
  ) ||
  !captureChecks ||
  captureChecks.renderedLayoutCount !== layoutIds.length ||
  captureChecks.nonBlankLayoutCount !== layoutIds.length ||
  captureChecks.missingImages !== 0 ||
  captureChecks.localFontsLoaded !== true ||
  JSON.stringify(captureChecks.externalRequests) !== "[]" ||
  JSON.stringify(captureChecks.consoleErrors) !== "[]" ||
  captureChecks.paintReadinessContract !== "passed"
) {
  throw new Error("editorial browser capture evidence is missing, stale, or failed");
}
const browserGolden = {
  schemaVersion: "presenton.template-browser-golden/v1",
  templateId: "editorial",
  engineId: engine.engineId,
  engineContractSha256: sha256(engineBytes),
  entrypointSha256,
  renderer: {
    modulePath: "servers/nextjs/lib/template-v2-json-to-html.ts",
    moduleSha256: sha256(rendererBytes),
    captureAdapter: "chromium-headless-cli/readiness-contract",
    productionCaptureContract: "scripts/sync-presentation-export.cjs readiness overlay",
  },
  browser: { family: "chromium", ...captureEvidence.browser },
  capturedAt: captureEvidence.capturedAt,
  captureEvidence: {
    path: "templates/manifests/editorial.browser-golden.capture.v1.json",
    sha256: sha256(captureEvidenceBytes),
    adapter: captureEvidence.captureAdapter,
  },
  networkPolicy: "deny",
  viewport: { width: 1280, height: 720 },
  layoutIds,
  screenshots,
  checks: captureChecks,
  status: "passed-browser-golden",
};
const browserGoldenBytes = Buffer.from(`${JSON.stringify(browserGolden, null, 2)}\n`);
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
    browserGoldenReceiptVerified: true,
  },
  status: "passed-static-browser-golden",
};
const smokeBytes = Buffer.from(`${JSON.stringify(smoke, null, 2)}\n`);
const thumbnail = assets.find((asset) => asset.path === "static/thumbnail.png");
if (!thumbnail) throw new Error("editorial thumbnail is missing");

const manifest = {
  schemaVersion: "presenton.template-package/v1",
  packageVersion: "1.1.0",
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
      "Playfair Display": "static/Playfair Display Bold Italic.ttf",
      "Playfair Display Bold": "static/Playfair Display Bold Italic.ttf",
      "Playfair Display Bold Italics": "static/Playfair Display Bold Italic.ttf",
      "Playfair Display Semi-Bold Italics": "static/Playfair Display Bold Italic.ttf",
    },
    unresolvedAliases: [],
  },
  license: {
    status: "approved",
    expression: "Apache-2.0 AND OFL-1.1",
    rightsEvidenceId,
  },
  provenance: {
    repository: "https://github.com/presenton/presenton",
    commitSha: "e271359ed91bfa48ba18c5717f8e19a7b69326ef",
    sourcePath: "templates/editorial",
    sourceFileCount: sourceFiles.length,
    sourceInventorySha256: inventorySha256,
    evidencePath: "templates/manifests/editorial.provenance.v1.json",
    evidenceSha256: sha256(provenanceBytes),
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
  browserGolden: {
    receiptPath: "templates/manifests/editorial.browser-golden.v1.json",
    receiptSha256: sha256(browserGoldenBytes),
  },
  publication: {
    status: "ready",
    default: false,
    blockers: [],
  },
};

const outputs = new Map([
  ["editorial.assets.v1.json", inventoryBytes],
  ["editorial.provenance.v1.json", provenanceBytes],
  ["editorial.renderer-smoke.v1.json", smokeBytes],
  ["editorial.browser-golden.v1.json", browserGoldenBytes],
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
