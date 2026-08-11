import { createHash } from "node:crypto";
import { execFileSync } from "node:child_process";
import { mkdir, readdir, readFile, stat, writeFile } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

const repositoryRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const manifestRoot = path.join(repositoryRoot, "templates", "manifests");
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
const checkOnly = process.argv.includes("--check");
const upstreamRepository = "https://github.com/presenton/presenton";
const upstreamHead = "b940f7b4a51d473af830d96d441276af3540dae6";
const editorialCommit = "e271359ed91bfa48ba18c5717f8e19a7b69326ef";
const googleFontsRepository = "https://github.com/google/fonts";
const googleFontsCommit = "8b0a1d0f5983c89bc2b93f1b5fb55f9e252744b5";
const packageIds = [
  "dynamic",
  "editorial",
  "executive",
  "general",
  "modern",
  "momentum",
  "standard",
  "swift",
];

const fontLicenses = {
  "albert sans": {
    familyPath: "albertsans",
    sha256: "5c856c086e8743b84932aae46ced424729a703932b601e9eb8aaeac15a617ec6",
  },
  anton: {
    familyPath: "anton",
    sha256: "ee67e6ee22790b7929f1a3769ca2801d565c64b5a9096942c1adf5596de9c9e4",
  },
  "dm sans": {
    familyPath: "dmsans",
    sourceId: "dm-sans",
    sha256: "9af36190332437f5ecd09974de43c1f7c77a310a996cdd8ceb25628b458840e1",
  },
  lato: {
    familyPath: "lato",
    sha256: "74ba064d03f1f1c4a952da936c3eb71866c34404916734de3cae73b34357e59e",
  },
  montserrat: {
    familyPath: "montserrat",
    sha256: "8b7141c03fa4f8d44e6345d5d4931709290f0f67875e452e95ac1fd3a027802e",
  },
  playfair: {
    familyPath: "playfairdisplay",
    sourceId: "playfair",
    sha256: "566be814f8e96e93dfa16101331557eb6b5467e9e03f627c0910fe93ca12300e",
  },
  poppins: {
    familyPath: "poppins",
    sha256: "6be04893d770899a015649c7aa3b582f871b272f8747a92b78b17c3e5c8b2573",
  },
};

const addedFontSources = {
  "general/static/Poppins Regular.ttf": {
    sourcePath: "ofl/poppins/Poppins-Regular.ttf",
    sourceSha256: "7e65201e9b79159e2300267cc885e16c8dcef2424cdfa09a29bfb0980a94a7ba",
    sourceGitBlobSha1: "0bda228ade88b0bb5aac7da2c881d0c3f64d0817",
  },
  "modern/static/Montserrat Regular.ttf": {
    sourcePath: "ofl/montserrat/Montserrat[wght].ttf",
    sourceSha256: "0f7b311b2f3279e4eef9b2f968bcdbab6e28f4daeb1f049f4f278a902bcd82f7",
    sourceGitBlobSha1: "c97aca18592834d8706549c279cb8d5ac5d85f69",
  },
  "standard/static/Playfair Display Regular.ttf": {
    sourcePath: "ofl/playfairdisplay/PlayfairDisplay[wght].ttf",
    sourceSha256: "c40f2293766a503bc70cce9e512ef844a4ccb7cbcde792fe2ea31d191917d8d6",
    sourceGitBlobSha1: "7a09eb71f10622fd083134ac9ed46f59b21bee18",
  },
  "swift/static/Albert Sans Regular.ttf": {
    sourcePath: "ofl/albertsans/AlbertSans[wght].ttf",
    sourceSha256: "8fe5d4cf5822d7096d4d17ad781c90f97c745ac13a22be619db74966fba45fda",
    sourceGitBlobSha1: "aa28110751a034a03ba84a7de12d069c3f652fe7",
  },
};

function sha256(value) {
  return createHash("sha256").update(value).digest("hex");
}

function gitBlobSha1(value) {
  return createHash("sha1")
    .update(Buffer.from(`blob ${value.length}\0`))
    .update(value)
    .digest("hex");
}

function git(args, encoding = "buffer") {
  return execFileSync("git", args, {
    cwd: repositoryRoot,
    encoding,
    maxBuffer: 64 * 1024 * 1024,
  });
}

function upstreamTree(templateId, commit) {
  const prefix = `templates/${templateId}/`;
  const output = git(
    ["ls-tree", "-r", "-z", commit, `templates/${templateId}`],
    "utf8",
  );
  return new Map(
    output
      .split("\0")
      .filter(Boolean)
      .map((line) => {
        const match = /^\d+ blob ([0-9a-f]{40})\t(.+)$/.exec(line);
        if (!match) throw new Error(`unexpected upstream tree record: ${line}`);
        return [match[2].replace(prefix, ""), match[1]];
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
  if ([".jpg", ".jpeg"].includes(extension) && bytes.subarray(0, 3).equals(Buffer.from("ffd8ff", "hex"))) {
    return "image/jpeg";
  }
  if (extension === ".svg" && /<svg(?:\s|>)/i.test(bytes.toString("utf8", 0, 4096))) {
    return "image/svg+xml";
  }
  if (extension === ".ttf" && ["00010000", "4f54544f", "74727565"].includes(bytes.subarray(0, 4).toString("hex"))) {
    return "font/ttf";
  }
  throw new Error(`template package MIME/magic mismatch: ${relative}`);
}

function fontFamilyForPath(relative) {
  const name = path.posix.basename(relative).toLowerCase();
  for (const family of Object.keys(fontLicenses).sort((left, right) => right.length - left.length)) {
    if (name.startsWith(family)) return family;
  }
  if (name.startsWith("playfair display")) return "playfair";
  throw new Error(`font package has no pinned OFL evidence: ${relative}`);
}

function fontAliases(template, fontAssets) {
  const aliases = {};
  const localPaths = fontAssets.map((asset) => asset.path);
  for (const [family, value] of Object.entries(template.fonts ?? {})) {
    if (typeof value !== "string") continue;
    if (!/^(?:https?:)?\/\//i.test(value)) {
      aliases[family] = value;
      continue;
    }
    const normalizedFamily = family.toLowerCase().replace(/[^a-z0-9]/g, "");
    const replacement = localPaths.find((candidate) =>
      path.posix.basename(candidate).toLowerCase().replace(/[^a-z0-9]/g, "").startsWith(normalizedFamily),
    );
    if (replacement) aliases[family] = replacement;
  }
  return aliases;
}

function packageExtensions(templateId, commit, upstreamAssets, entrypointBytes, assets) {
  const extensions = [];
  for (const asset of assets) {
    const source = addedFontSources[`${templateId}/${asset.path}`];
    if (!source) continue;
    if (asset.sha256 !== source.sourceSha256 || !/^[0-9a-f]{40}$/.test(source.sourceGitBlobSha1)) {
      throw new Error(`${templateId} vendored font source identity changed: ${asset.path}`);
    }
    extensions.push({
      path: asset.path,
      kind: "added",
      reason: "Pinned local font replacement for the upstream Google Fonts stylesheet alias.",
      sourceRepository: googleFontsRepository,
      sourceCommitSha: googleFontsCommit,
      sourcePath: source.sourcePath,
      sourceSha256: source.sourceSha256,
      sourceGitBlobSha1: source.sourceGitBlobSha1,
    });
  }
  const entrypointBlob = upstreamAssets.get("template.json");
  const entrypointSource = git(["show", `${commit}:templates/${templateId}/template.json`]);
  if (gitBlobSha1(entrypointBytes) !== entrypointBlob) {
    extensions.push({
      path: "template.json",
      kind: "modified",
      reason: "Preserved the fork's verified offline font aliases while merging the upstream template payload.",
      sourceRepository: upstreamRepository,
      sourceCommitSha: commit,
      sourcePath: `templates/${templateId}/template.json`,
      sourceSha256: sha256(entrypointSource),
      sourceGitBlobSha1: entrypointBlob,
    });
  }
  return extensions.sort((left, right) => left.path.localeCompare(right.path));
}

async function generatePackage(templateId, engine, engineBytes, rendererBytes) {
  const commit = templateId === "editorial" ? editorialCommit : upstreamHead;
  const sourceRoot = path.join(repositoryRoot, "templates", templateId);
  const entrypointPath = path.join(sourceRoot, "template.json");
  const sourceFiles = await filesBelow(sourceRoot);
  if (!sourceFiles.includes("template.json")) throw new Error(`${templateId} entrypoint is missing`);
  const entrypointBytes = await readFile(entrypointPath);
  const template = JSON.parse(entrypointBytes.toString("utf8"));
  if (template.id !== templateId || !Array.isArray(template.layouts) || template.layouts.length === 0) {
    throw new Error(`${templateId} template identity/layout contract changed`);
  }

  const upstreamAssets = upstreamTree(templateId, commit);
  const assets = [];
  let totalSizeBytes = 0;
  for (const relative of sourceFiles.filter((item) => item !== "template.json")) {
    const absolute = path.join(sourceRoot, relative);
    const metadata = await stat(absolute);
    const bytes = await readFile(absolute);
    const type = mediaType(relative, bytes);
    const upstreamBlobSha1 = upstreamAssets.get(relative);
    const addedSource = addedFontSources[`${templateId}/${relative}`];
    if (upstreamBlobSha1) {
      if (upstreamBlobSha1 !== gitBlobSha1(bytes)) {
        throw new Error(`${templateId} asset diverged from upstream ${commit}: ${relative}`);
      }
    } else if (
      !addedSource ||
      addedSource.sourceSha256 !== sha256(bytes) ||
      addedSource.sourceGitBlobSha1 !== gitBlobSha1(bytes)
    ) {
      throw new Error(`${templateId} package asset has no immutable source: ${relative}`);
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
    templateId,
    files: assets,
  };
  const inventoryBytes = Buffer.from(`${JSON.stringify(inventory, null, 2)}\n`);
  const inventorySha256 = sha256(Buffer.from(JSON.stringify({ files: assets })));
  const entrypointSha256 = sha256(entrypointBytes);
  const layoutIds = template.layouts.map((layout) => layout.id);
  if (layoutIds.some((id) => typeof id !== "string") || new Set(layoutIds).size !== layoutIds.length) {
    throw new Error(`${templateId} layout IDs are invalid`);
  }
  const fontAssets = assets.filter((asset) => asset.mediaType === "font/ttf");
  const aliases = fontAliases(template, fontAssets);
  const unresolvedAliases = Object.entries(template.fonts ?? {})
    .filter(([, value]) => typeof value === "string" && /^(?:https?:)?\/\//i.test(value))
    .map(([family]) => family)
    .filter((family) => !aliases[family]);
  if (unresolvedAliases.length) {
    throw new Error(`${templateId} has unresolved remote font aliases: ${unresolvedAliases.join(", ")}`);
  }

  const upstreamLicenseBytes = git(["show", `${commit}:LICENSE`]);
  const upstreamLicenseBlobSha1 = git(["rev-parse", `${commit}:LICENSE`], "utf8").trim();
  const extensions = packageExtensions(templateId, commit, upstreamAssets, entrypointBytes, assets);
  const extensionByPath = new Map(extensions.map((item) => [item.path, item]));
  const fontFamilies = [...new Set(fontAssets.map((asset) => fontFamilyForPath(asset.path)))].sort();
  const evidenceSources = [
    {
      id: "upstream-commit",
      type: "git-commit",
      url: `${upstreamRepository}/commit/${commit}`,
      commitSha: commit,
    },
    {
      id: "upstream-license",
      type: "repository-license",
      licenseExpression: "Apache-2.0",
      url: `${upstreamRepository}/blob/${commit}/LICENSE`,
      sha256: sha256(upstreamLicenseBytes),
      upstreamGitBlobSha1: upstreamLicenseBlobSha1,
    },
  ];
  if (templateId === "editorial") {
    evidenceSources.push({
      id: "upstream-pr",
      type: "pull-request",
      url: "https://github.com/presenton/presenton/pull/813",
      assertion: "The Editorial template was merged with bundled fonts and assets.",
    });
  }
  if (extensions.some((item) => item.sourceRepository === googleFontsRepository)) {
    evidenceSources.push({
      id: "google-fonts-commit",
      type: "git-commit",
      url: `${googleFontsRepository}/commit/${googleFontsCommit}`,
      commitSha: googleFontsCommit,
    });
  }
  for (const family of fontFamilies) {
    const license = fontLicenses[family];
    const sourceId = license.sourceId ?? license.familyPath;
    evidenceSources.push({
      id: `google-fonts-${sourceId}-ofl`,
      type: "font-license",
      licenseExpression: "OFL-1.1",
      url: `${googleFontsRepository}/blob/${googleFontsCommit}/ofl/${license.familyPath}/OFL.txt`,
      commitSha: googleFontsCommit,
      sha256: license.sha256,
    });
  }

  const rightsEvidenceId = `presenton-${templateId}-assets-apache-2.0`;
  const claims = [
    {
      id: rightsEvidenceId,
      status: "verified",
      licenseExpression: "Apache-2.0",
      evidenceSourceIds: [
        "upstream-commit",
        "upstream-license",
        ...(templateId === "editorial" ? ["upstream-pr"] : []),
      ],
      coveredAssets: assets
        .filter((asset) => !asset.mediaType.startsWith("font/"))
        .map((asset) => ({
          path: asset.path,
          sha256: asset.sha256,
          upstreamGitBlobSha1: upstreamAssets.get(asset.path),
        })),
    },
  ];
  for (const family of fontFamilies) {
    const license = fontLicenses[family];
    const sourceId = license.sourceId ?? license.familyPath;
    const coveredAssets = fontAssets
      .filter((asset) => fontFamilyForPath(asset.path) === family)
      .map((asset) => {
        const extension = extensionByPath.get(asset.path);
        return {
          path: asset.path,
          sha256: asset.sha256,
          upstreamGitBlobSha1: extension?.sourceGitBlobSha1 ?? upstreamAssets.get(asset.path),
          ...(extension ? {
            sourceCommitSha: extension.sourceCommitSha,
            sourcePath: extension.sourcePath,
          } : {}),
          evidenceSourceIds: [
            extension ? "google-fonts-commit" : "upstream-commit",
            `google-fonts-${sourceId}-ofl`,
          ],
        };
      });
    claims.push({
      id: `presenton-${templateId}-${license.familyPath}-fonts-ofl-1.1`,
      status: "verified",
      licenseExpression: "OFL-1.1",
      evidenceSourceIds: [
        ...(coveredAssets.some((asset) => asset.sourceCommitSha) ? ["google-fonts-commit"] : []),
        ...(coveredAssets.some((asset) => !asset.sourceCommitSha) ? ["upstream-commit"] : []),
        `google-fonts-${sourceId}-ofl`,
      ],
      coveredAssets,
    });
  }

  const provenance = {
    schemaVersion: "presenton.template-package-provenance/v1",
    templateId,
    upstream: {
      repository: upstreamRepository,
      commitSha: commit,
      sourcePath: `templates/${templateId}`,
      commitUrl: `${upstreamRepository}/commit/${commit}`,
      ...(templateId === "editorial" ? { pullRequestUrl: "https://github.com/presenton/presenton/pull/813" } : {}),
      license: {
        expression: "Apache-2.0",
        path: "LICENSE",
        sha256: sha256(upstreamLicenseBytes),
        upstreamGitBlobSha1: upstreamLicenseBlobSha1,
        url: `${upstreamRepository}/blob/${commit}/LICENSE`,
      },
    },
    extensions,
    evidenceSources,
    claims,
  };
  const provenanceBytes = Buffer.from(`${JSON.stringify(provenance, null, 2)}\n`);

  const goldenRoot = path.join(manifestRoot, `${templateId}.browser-golden`);
  const goldenNames = (await readdir(goldenRoot))
    .filter((name) => name.endsWith(".png"))
    .sort((left, right) => left.localeCompare(right));
  const expectedGoldenNames = layoutIds.map(
    (layoutId, index) => `${String(index + 1).padStart(2, "0")}-${layoutId}.png`,
  );
  if (JSON.stringify(goldenNames) !== JSON.stringify(expectedGoldenNames)) {
    throw new Error(`${templateId} browser golden screenshot set is incomplete or stale`);
  }
  const screenshots = [];
  for (const [index, name] of goldenNames.entries()) {
    const bytes = await readFile(path.join(goldenRoot, name));
    screenshots.push({
      layoutId: layoutIds[index],
      path: `templates/manifests/${templateId}.browser-golden/${name}`,
      sha256: sha256(bytes),
      sizeBytes: bytes.length,
      width: 1280,
      height: 720,
    });
  }
  const captureEvidencePath = path.join(manifestRoot, `${templateId}.browser-golden.capture.v1.json`);
  const captureEvidenceBytes = await readFile(captureEvidencePath);
  const captureEvidence = JSON.parse(captureEvidenceBytes.toString("utf8"));
  const captureChecks = captureEvidence.checks;
  if (
    captureEvidence.templateId !== templateId ||
    captureEvidence.rendererModuleSha256 !== sha256(rendererBytes) ||
    captureEvidence.entrypointSha256 !== entrypointSha256 ||
    captureEvidence.status !== "passed-browser-capture" ||
    !Array.isArray(captureEvidence.screenshots) ||
    captureEvidence.screenshots.length !== screenshots.length ||
    !captureEvidence.screenshots.every((item, index) =>
      item.layoutId === screenshots[index].layoutId &&
      item.goldenSha256 === screenshots[index].sha256 &&
      item.exact === true && item.badPixelRatio === 0 && item.meanAbsoluteError === 0
    ) ||
    captureChecks?.renderedLayoutCount !== layoutIds.length ||
    captureChecks?.nonBlankLayoutCount !== layoutIds.length ||
    captureChecks?.missingImages !== 0 ||
    captureChecks?.localFontsLoaded !== true ||
    JSON.stringify(captureChecks?.externalRequests) !== "[]" ||
    JSON.stringify(captureChecks?.consoleErrors) !== "[]" ||
    captureChecks?.paintReadinessContract !== "passed"
  ) {
    throw new Error(`${templateId} browser capture evidence is missing, stale, or failed`);
  }

  const browserGolden = {
    schemaVersion: "presenton.template-browser-golden/v1",
    templateId,
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
      path: `templates/manifests/${templateId}.browser-golden.capture.v1.json`,
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
    templateId,
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
  if (!thumbnail) throw new Error(`${templateId} thumbnail is missing`);

  const manifest = {
    schemaVersion: "presenton.template-package/v1",
    packageVersion: "1.2.0",
    templateId,
    sourceRoot: `templates/${templateId}`,
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
      inventoryPath: `templates/manifests/${templateId}.assets.v1.json`,
      inventorySha256,
      count: assets.length,
      totalSizeBytes,
    },
    fonts: {
      faces: fontAssets.map((asset) => asset.path),
      aliases,
      unresolvedAliases,
    },
    license: {
      status: "approved",
      expression: fontAssets.length ? "Apache-2.0 AND OFL-1.1" : "Apache-2.0",
      rightsEvidenceId,
    },
    provenance: {
      repository: upstreamRepository,
      commitSha: commit,
      sourcePath: `templates/${templateId}`,
      sourceFileCount: sourceFiles.length,
      sourceInventorySha256: inventorySha256,
      evidencePath: `templates/manifests/${templateId}.provenance.v1.json`,
      evidenceSha256: sha256(provenanceBytes),
      extensions,
    },
    thumbnail: {
      assetPath: thumbnail.path,
      sha256: thumbnail.sha256,
      width: 1280,
      height: 720,
    },
    rendererSmoke: {
      receiptPath: `templates/manifests/${templateId}.renderer-smoke.v1.json`,
      receiptSha256: sha256(smokeBytes),
    },
    browserGolden: {
      receiptPath: `templates/manifests/${templateId}.browser-golden.v1.json`,
      receiptSha256: sha256(browserGoldenBytes),
    },
    publication: { status: "ready", default: false, blockers: [] },
  };

  const outputs = new Map([
    [`${templateId}.assets.v1.json`, inventoryBytes],
    [`${templateId}.provenance.v1.json`, provenanceBytes],
    [`${templateId}.renderer-smoke.v1.json`, smokeBytes],
    [`${templateId}.browser-golden.v1.json`, browserGoldenBytes],
    [`${templateId}.package.v1.json`, Buffer.from(`${JSON.stringify(manifest, null, 2)}\n`)],
  ]);
  for (const [name, expected] of outputs) {
    const destination = path.join(manifestRoot, name);
    if (checkOnly) {
      const current = await readFile(destination).catch(() => null);
      if (!current?.equals(expected)) throw new Error(`template package metadata is stale: ${name}`);
    } else {
      await writeFile(destination, expected);
    }
  }
  return { templateId, assets: assets.length, bytes: totalSizeBytes, layouts: layoutIds.length };
}

await mkdir(manifestRoot, { recursive: true });
const engineBytes = await readFile(enginePath);
const engine = JSON.parse(engineBytes.toString("utf8"));
const rendererBytes = await readFile(rendererPath);
const receipts = [];
for (const templateId of packageIds) {
  receipts.push(await generatePackage(templateId, engine, engineBytes, rendererBytes));
}
console.log(`${checkOnly ? "Verified" : "Generated"} ${receipts.length} template packages: ${receipts.map((item) => `${item.templateId}(${item.layouts} layouts/${item.assets} assets)`).join(", ")}.`);
