#!/usr/bin/env node
/**
 * CLI bridge for Python: by default, raw extracted text on stdout (--python-bridge plain);
 * or one JSON line (--python-bridge json) for backward compatibility.
 *
 * OCR follows LlamaIndex LiteParse guidance (built-in Tesseract by default):
 * https://developers.llamaindex.ai/liteparse/guides/ocr/
 *
 * - ISO 639-3 for Tesseract (eng, fra, deu, jpn, …); multi-lang as "deu+eng" or "deu,eng".
 * - Parallel workers ≈ CPU cores − 1 (override --num-workers).
 * - Optional HTTP OCR: --ocr-server-url or LITEPARSE_OCR_SERVER_URL.
 * - Optional local models: --tessdata-path or LITEPARSE_TESSDATA_PATH (else TESSDATA_PREFIX / CDN).
 */

import fs from "node:fs";
import os from "node:os";
import path from "node:path";

// LiteParse converts every image to PDF through ImageMagick before OCR. Raster
// images can be decoded by sharp and recognized by the same OCR backends
// directly, so keep them off that conversion path. SVG deliberately stays on
// the document conversion path because it is active markup rather than a
// bounded raster input.
const DIRECT_OCR_IMAGE_EXTENSIONS = new Set([
  ".png",
  ".jpg",
  ".jpeg",
  ".webp",
  ".gif",
  ".bmp",
  ".tif",
  ".tiff",
]);

function readArg(name) {
  const idx = process.argv.indexOf(name);
  if (idx === -1) return null;
  return process.argv[idx + 1] ?? null;
}

function parseBool(value, fallback) {
  if (value == null || value === "") return fallback;
  const s = String(value).trim().toLowerCase();
  if (["1", "true", "yes", "on"].includes(s)) return true;
  if (["0", "false", "no", "off"].includes(s)) return false;
  return fallback;
}

function toNumber(value, fallback, min, max) {
  if (value == null || value === "") return fallback;
  const parsed = Number(value);
  if (Number.isNaN(parsed)) return fallback;
  return Math.min(Math.max(parsed, min), max);
}

/** Tesseract accepts "deu+eng"; allow comma-separated CLI/env for convenience. */
function normalizeOcrLanguage(raw) {
  const s = String(raw ?? "").trim();
  if (!s) return "eng";
  if (s.includes(",")) {
    return s
      .split(",")
      .map((p) => p.trim())
      .filter(Boolean)
      .join("+");
  }
  return s;
}

function emit(result, exitCode = 0) {
  process.stdout.write(`${JSON.stringify(result)}\n`);
  process.exit(exitCode);
}

/** "plain" = success: UTF-8 text on stdout only. "json" = one JSON line (legacy, huge payloads can break). */
const pyBridgeArg = readArg("--python-bridge");
const pyBridge =
  pyBridgeArg == null || pyBridgeArg === ""
    ? "json"
    : String(pyBridgeArg).trim().toLowerCase() === "plain"
      ? "plain"
      : "json";

function bridgeError(message, exitCode) {
  if (pyBridge === "plain") {
    process.stderr.write(`${message}\n`);
    process.exit(exitCode);
  }
  emit({ ok: false, error: message }, exitCode);
}

async function normalizeRasterImage(filePath) {
  const { default: sharp } = await import("sharp");
  return sharp(filePath, {
    animated: false,
    failOn: "error",
    pages: 1,
  })
    .rotate()
    .png()
    .toBuffer();
}

async function recognizeWithHttpOcr(image, serverUrl, language) {
  const formData = new FormData();
  formData.append("file", new Blob([image], { type: "image/png" }), "image.png");
  formData.append("language", language);

  const response = await fetch(serverUrl, {
    method: "POST",
    body: formData,
    signal: AbortSignal.timeout(60_000),
  });
  if (!response.ok) {
    throw new Error(`OCR server request failed with HTTP ${response.status}`);
  }

  const payload = await response.json();
  if (!Array.isArray(payload?.results)) {
    throw new Error("OCR server response is missing a results array");
  }

  return payload.results
    .map((item) => String(item?.text ?? "").trim())
    .filter(Boolean)
    .join(" ");
}

async function recognizeWithTesseract(image, language, localTessdataPath) {
  const { createWorker } = await import("tesseract.js");
  const workerOptions = {
    // Prevent worker-thread failures from writing unstructured output to
    // stdout. createWorker still rejects and the bridge reports the failure.
    errorHandler: () => {},
  };
  if (localTessdataPath) {
    workerOptions.langPath = localTessdataPath;
    workerOptions.cachePath = localTessdataPath;
    workerOptions.gzip = false;
  }

  const worker = await createWorker(language, 1, workerOptions);
  try {
    const result = await worker.recognize(image, { rotateAuto: true });
    return result?.data?.text ?? "";
  } finally {
    await worker.terminate();
  }
}

async function parseRasterImageDirectly(filePath, options) {
  if (!options.ocrEnabled) {
    return { text: "", pages: [{ page: 1 }] };
  }

  const image = await normalizeRasterImage(filePath);
  const text = options.ocrServerUrl
    ? await recognizeWithHttpOcr(image, options.ocrServerUrl, options.ocrLanguage)
    : await recognizeWithTesseract(image, options.ocrLanguage, options.tessdataPath);
  return { text, pages: [{ page: 1 }] };
}

const filePath = readArg("--file");
if (!filePath) {
  bridgeError("Missing required --file argument", 2);
}

const resolvedPath = path.resolve(filePath);
if (!fs.existsSync(resolvedPath)) {
  bridgeError(`File not found: ${resolvedPath}`, 2);
}

const ocrEnabled = parseBool(readArg("--ocr-enabled"), true);
const dpi = toNumber(readArg("--dpi"), 150, 72, 600);
const numWorkers = toNumber(
  readArg("--num-workers"),
  Math.max(os.cpus().length - 2, 1),
  1,
  64
);

const cliOcrLanguage = readArg("--ocr-language");
const ocrLanguageRaw =
  (process.env.LITEPARSE_OCR_LANGUAGE && String(process.env.LITEPARSE_OCR_LANGUAGE).trim()) ||
  (cliOcrLanguage && String(cliOcrLanguage).trim()) ||
  "";
const ocrLanguage = normalizeOcrLanguage(ocrLanguageRaw || "eng");

const outputFormatRaw = (readArg("--output-format") || "text").trim().toLowerCase();
const outputFormat = outputFormatRaw === "json" ? "json" : "text";

const ocrServerUrlArg = readArg("--ocr-server-url");
const ocrServerUrl =
  (ocrServerUrlArg && String(ocrServerUrlArg).trim()) ||
  (process.env.LITEPARSE_OCR_SERVER_URL && String(process.env.LITEPARSE_OCR_SERVER_URL).trim()) ||
  undefined;

const tessdataArg = readArg("--tessdata-path");
const tessdataPath =
  (tessdataArg && String(tessdataArg).trim()) ||
  (process.env.LITEPARSE_TESSDATA_PATH && String(process.env.LITEPARSE_TESSDATA_PATH).trim()) ||
  (process.env.TESSDATA_PREFIX && String(process.env.TESSDATA_PREFIX).trim()) ||
  undefined;

try {
  const config = {
    ocrEnabled,
    ocrLanguage,
    outputFormat,
    dpi,
    numWorkers,
  };
  if (ocrServerUrl) {
    config.ocrServerUrl = ocrServerUrl;
  }
  if (tessdataPath) {
    config.tessdataPath = tessdataPath;
  }

  const extension = path.extname(resolvedPath).toLowerCase();
  let result;
  if (DIRECT_OCR_IMAGE_EXTENSIONS.has(extension)) {
    result = await parseRasterImageDirectly(resolvedPath, config);
  } else {
    // Delay loading LiteParse until a document actually needs its PDF/Office
    // pipeline. Direct image OCR is therefore independent of ImageMagick.
    const { LiteParse } = await import("@llamaindex/liteparse");
    const parser = new LiteParse(config);
    result = await parser.parse(resolvedPath, true);
  }
  const text = result?.text ?? "";
  if (pyBridge === "plain") {
    process.stdout.write(text);
    process.exit(0);
  }
  emit({
    ok: true,
    filePath: resolvedPath,
    text,
    pageCount: Array.isArray(result?.pages) ? result.pages.length : 0,
    ocr: {
      engine: ocrServerUrl ? "http" : "tesseract",
      ocrLanguage,
      ocrEnabled,
      dpi,
      numWorkers,
    },
  });
} catch (error) {
  const message = error instanceof Error ? error.message : String(error);
  const stack = error instanceof Error ? error.stack : undefined;
  if (pyBridge === "plain") {
    if (stack) {
      process.stderr.write(`${stack}\n`);
    }
    process.stderr.write(`${message}\n`);
    process.exit(1);
  }
  if (stack) {
    process.stderr.write(`${stack}\n`);
  }
  emit(
    {
      ok: false,
      filePath: resolvedPath,
      error: message,
    },
    1
  );
}
