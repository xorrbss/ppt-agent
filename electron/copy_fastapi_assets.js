const fs = require("fs");
const path = require("path");

const fastapiDir = path.join(__dirname, "..", "servers", "fastapi");
const resourcesFastapiDir = path.join(__dirname, "resources", "fastapi");

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

function main() {
  fs.mkdirSync(resourcesFastapiDir, { recursive: true });

  for (const { name, src, dest } of sources) {
    console.log(`[fastapi-assets] Copying ${name} -> ${dest}`);
    copyDir(src, dest);
  }

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
}

try {
  main();
} catch (error) {
  console.error(`[fastapi-assets] ${error.message}`);
  process.exit(1);
}
