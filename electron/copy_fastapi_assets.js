const fs = require("fs");
const path = require("path");
const { writeResourceProvenance } = require("./scripts/resource-provenance.cjs");
const {
  atomicReplaceDirectory,
  copyTreeSafe,
} = require("./scripts/standalone-copy.cjs");

const fastapiDir = path.join(__dirname, "..", "servers", "fastapi");
const resourcesFastapiDir = path.join(__dirname, "resources", "fastapi");
const ooxmlContractRelative = path.join(
  "authored_vnext",
  "ooxml",
  "v1",
  "generated",
  "ooxml_package.py"
);
const ooxmlContractSource = path.join(__dirname, "..", "contracts", ooxmlContractRelative);
const ooxmlContractDestination = path.join(
  __dirname,
  "resources",
  "contracts",
  ooxmlContractRelative
);
// Both readiness modules resolve their release profile from the runtime's
// sibling contracts tree. Keep this bounded to the authored semantic subtree:
// it contains the v2 and v3 authority profiles without copying unrelated
// authored contract families into the packaged application.
const authoredSemanticContractRelative = path.join("authored_vnext", "semantic");
const authoredSemanticContractSource = path.join(
  __dirname,
  "..",
  "contracts",
  authoredSemanticContractRelative
);
const authoredSemanticContractDestination = path.join(
  __dirname,
  "resources",
  "contracts",
  authoredSemanticContractRelative
);
const templateEngineRelative = path.join(
  "template_package",
  "engines",
  "template-v2-json.v1.json"
);
const templateEngineSource = path.join(__dirname, "..", "contracts", templateEngineRelative);
const templateEngineDestination = path.join(
  __dirname,
  "resources",
  "contracts",
  templateEngineRelative
);
const templatePackagesSource = path.join(__dirname, "..", "templates");
const templatePackagesDestination = path.join(__dirname, "resources", "templates");
const templateRendererRelative = path.join(
  "servers",
  "nextjs",
  "lib",
  "template-v2-json-to-html.ts"
);
const templateRendererSource = path.join(__dirname, "..", templateRendererRelative);
const templateRendererDestination = path.join(
  __dirname,
  "resources",
  templateRendererRelative
);

const sources = [
  { name: "static", src: path.join(fastapiDir, "static"), dest: path.join(resourcesFastapiDir, "static") },
  { name: "assets", src: path.join(fastapiDir, "assets"), dest: path.join(resourcesFastapiDir, "assets") },
];

function copyDir(src, dest) {
  if (!fs.existsSync(src)) {
    throw new Error(`Source directory not found: ${src}`);
  }
  fs.mkdirSync(dest, { recursive: true });
  fs.cpSync(src, dest, { recursive: true, force: true });
}

function copyFile(src, dest) {
  if (!fs.statSync(src, { throwIfNoEntry: false })?.isFile()) {
    throw new Error(`Source file not found: ${src}`);
  }
  fs.mkdirSync(path.dirname(dest), { recursive: true });
  fs.copyFileSync(src, dest);
}

function copyDirExact(src, dest) {
  if (!fs.statSync(src, { throwIfNoEntry: false })?.isDirectory()) {
    throw new Error(`Source directory not found: ${src}`);
  }
  fs.mkdirSync(path.dirname(dest), { recursive: true });
  const temporary = fs.mkdtempSync(
    path.join(path.dirname(dest), `.${path.basename(dest)}-copy-`)
  );
  try {
    copyTreeSafe(src, temporary, { materializeLinks: true });
    atomicReplaceDirectory(temporary, dest);
  } catch (error) {
    if (error.atomicReplacementRecoveryRequired) {
      console.error(
        `[fastapi-assets] Preserving staged resource directories after failed rollback: ${error.atomicReplacementTemp} ${error.atomicReplacementBackup}`
      );
    } else {
      fs.rmSync(temporary, {
        recursive: true,
        force: true,
        maxRetries: process.platform === "win32" ? 12 : 3,
        retryDelay: process.platform === "win32" ? 250 : 100,
      });
    }
    throw error;
  }
}

function main() {
  fs.mkdirSync(resourcesFastapiDir, { recursive: true });

  for (const { name, src, dest } of sources) {
    console.log(`[fastapi-assets] Copying ${name} -> ${dest}`);
    copyDir(src, dest);
  }

  console.log(`[fastapi-assets] Copying OOXML contract -> ${ooxmlContractDestination}`);
  copyFile(ooxmlContractSource, ooxmlContractDestination);
  console.log(
    `[fastapi-assets] Copying authored semantic contract tree -> ${authoredSemanticContractDestination}`
  );
  copyDirExact(authoredSemanticContractSource, authoredSemanticContractDestination);
  console.log(`[fastapi-assets] Copying template engine -> ${templateEngineDestination}`);
  copyFile(templateEngineSource, templateEngineDestination);
  console.log(`[fastapi-assets] Copying template packages -> ${templatePackagesDestination}`);
  copyDirExact(templatePackagesSource, templatePackagesDestination);
  console.log(`[fastapi-assets] Copying template renderer evidence -> ${templateRendererDestination}`);
  copyFile(templateRendererSource, templateRendererDestination);
  writeResourceProvenance({
    kind: "template-packages",
    sourceRoot: templatePackagesSource,
    resourceRoot: templatePackagesDestination,
    workspaceRoot: path.join(__dirname, ".."),
  });

  // OCR language data (eng/kor) for offline LiteParse/tesseract.js. Placed next
  // to the runner so LiteParseService resolves <runner_dir>/tessdata at runtime.
  // Optional: without it OCR falls back to downloading models from a CDN.
  const tessdataSrc = path.join(__dirname, "..", "tessdata");
  const tessdataDest = path.join(__dirname, "resources", "document-extraction", "tessdata");
  if (fs.existsSync(tessdataSrc)) {
    console.log(`[fastapi-assets] Copying tessdata -> ${tessdataDest}`);
    copyDir(tessdataSrc, tessdataDest);
  } else {
    console.warn(`[fastapi-assets] tessdata not found at ${tessdataSrc}; OCR will fall back to CDN`);
  }

  writeResourceProvenance({
    kind: "fastapi",
    sourceRoot: fastapiDir,
    resourceRoot: resourcesFastapiDir,
    workspaceRoot: path.join(__dirname, ".."),
  });
}

try {
  main();
} catch (error) {
  console.error(`[fastapi-assets] ${error.message}`);
  process.exit(1);
}
