import { spawn } from "node:child_process";
import fs from "node:fs/promises";
import path from "node:path";

import {
  extractAuthoredSlideDom,
  renderAuthoredBackplate,
  resolveAuthoredHybridChromeExecutable,
} from "../lib/authored-hybrid/index.ts";
import { collectGoogleFontsForAuthoredHtml } from "../lib/authored-hybrid/google-font-collector.ts";
import { mergePowerPointTextLayout } from "../lib/authored-hybrid/font-layout.ts";
import { resolveFontEmbeddingPlan } from "../lib/authored-hybrid/font-embedding-policy.ts";
import {
  prepareNativeElements,
  selectLayerSafeNativeElements,
  serializePreparedNativeElement,
} from "../lib/authored-hybrid/native-plan.ts";
import { assembleAuthoredHybridPptx } from "../lib/authored-hybrid/pptx-assembler.ts";
import { embedPowerPointFonts } from "../lib/authored-hybrid/pptx-font-embedding.ts";
import { inspectPptxEditability } from "../lib/authored-hybrid/pptx-quality-inspection.ts";
import { preflightAuthoredHtmlForHybrid } from "../lib/authored-hybrid/security.ts";
import { createPresentationExportQuality } from "../lib/presentation-export-quality.ts";

function usage() {
  throw new Error(
    "Usage: qa-authored-hybrid-export.mjs <python> <db> <presentation-id> " +
      "<source.pptx> <output.pptx> <qa-directory> [plain|embedded] " +
      "[default|powerpoint-calibrated]"
  );
}

function run(command, args) {
  return new Promise((resolve, reject) => {
    const child = spawn(command, args, {
      windowsHide: true,
      stdio: ["ignore", "pipe", "pipe"],
    });
    const stdout = [];
    const stderr = [];
    child.stdout.on("data", (chunk) => stdout.push(chunk));
    child.stderr.on("data", (chunk) => stderr.push(chunk));
    child.once("error", reject);
    child.once("close", (code) => {
      if (code === 0) {
        resolve(Buffer.concat(stdout).toString("utf8"));
        return;
      }
      reject(
        new Error(
          `${command} exited ${code}: ${Buffer.concat(stderr).toString("utf8")}`
        )
      );
    });
  });
}

async function readSlides(python, database, presentationId) {
  const script = String.raw`
import json, sqlite3, sys
database, wanted = sys.argv[1], sys.argv[2].replace("-", "").lower()
connection = sqlite3.connect(database)
connection.row_factory = sqlite3.Row
found = []
for row in connection.execute("select name from sqlite_master where type='table'"):
    table = row[0]
    columns = [value[1] for value in connection.execute('pragma table_info("' + table.replace('"', '""') + '")')]
    if "html_content" not in columns or "presentation" not in columns:
        continue
    order = '"index"' if "index" in columns else "rowid"
    query = 'select html_content, ' + order + ' as slide_index, presentation from "' + table.replace('"', '""') + '" order by ' + order
    for candidate in connection.execute(query):
        raw = candidate["presentation"]
        normalized = raw.hex() if isinstance(raw, bytes) else str(raw).replace("-", "").lower()
        if normalized == wanted:
            found.append({"index": int(candidate["slide_index"]), "html": candidate["html_content"] or ""})
if not found:
    raise SystemExit("presentation slides not found")
print(json.dumps(sorted(found, key=lambda item: item["index"]), ensure_ascii=False))
`;
  return JSON.parse(
    await run(python, ["-c", script, database, presentationId])
  );
}

function preSerializePreparedElements(
  elements,
  embeddedTypefaceFamilies = [],
  textFidelityMode
) {
  const serializable = [];
  for (const element of elements) {
    try {
      serializePreparedNativeElement(
        element,
        serializable.length + 3,
        element.kind === "image" ? "rId999" : undefined,
        { embeddedTypefaceFamilies, textFidelityMode }
      );
      serializable.push(element);
    } catch {
      // Keep the matching raster paint when native serialization is unsafe.
    }
  }
  return serializable;
}

function sameIds(elements, ids) {
  return (
    elements.length === ids.length &&
    elements.every((element, index) => element.source.id === ids[index])
  );
}

function imageFallbackSlide(slideNumber, reason) {
  return {
    slideNumber,
    editable: false,
    imageFallback: true,
    nativeTextElements: 0,
    nativeShapeElements: 0,
    nativeGroupElements: 0,
    nativeImageElements: 0,
    rasterFallbackElements: 1,
    fallbackReasons: [reason],
    fallbackElements: [
      {
        elementId: `slide:${slideNumber}`,
        candidateKind: "slide",
        reasons: [reason],
      },
    ],
  };
}

function noFontRenderingTelemetry() {
  return {
    browserFontFilesCollected: 0,
    browserCollectionFailures: 0,
  };
}

function fallbackElementForContractElement(element) {
  return {
    elementId: element.id,
    domPath: element.domPath,
    candidateKind:
      element.classification.mode === "native"
        ? element.classification.kind
        : element.classification.candidateKind,
    reasons:
      element.classification.mode === "raster"
        ? element.classification.reasons
        : ["native-preparation-or-layer-safety-rejected"],
  };
}

async function prepareSlide(
  html,
  slideNumber,
  chromeExecutable,
  embeddedTypefaceFamilies = [],
  textFidelityMode
) {
  const fontCollection = await collectGoogleFontsForAuthoredHtml(html);
  const fontRendering = {
    browserFontFilesCollected: fontCollection.collectedFontFiles,
    browserCollectionFailures: fontCollection.failures.length,
  };
  const collectedHtml = fontCollection.html;
  const preflight = preflightAuthoredHtmlForHybrid(collectedHtml);
  if (!preflight.ok) {
    return {
      layer: null,
      quality: imageFallbackSlide(
        slideNumber,
        preflight.reason ?? "html-preflight-failed"
      ),
      fontRendering,
    };
  }
  const options = { chromeExecutable, timeoutMs: 40_000 };
  const contract = await extractAuthoredSlideDom(collectedHtml, options);
  let nativeLayoutElements = contract.elements;
  try {
    const layoutContract = await extractAuthoredSlideDom(collectedHtml, {
      ...options,
      fontLayoutMode: "powerpoint",
    });
    nativeLayoutElements = mergePowerPointTextLayout(
      contract.elements,
      layoutContract.elements,
      { embeddedTypefaceFamilies }
    ).elements;
  } catch {
    // Match the application export path: mapped-font geometry is an accuracy
    // enhancement, while source geometry remains a compatible fallback.
  }
  const prepared = preSerializePreparedElements(
    await prepareNativeElements(nativeLayoutElements, {
      includeRasterText: true,
      includeRasterShapes: true,
    }),
    embeddedTypefaceFamilies,
    textFidelityMode
  );
  let selected = selectLayerSafeNativeElements(
    contract.elements,
    prepared,
    undefined,
    {
      promoteTextAboveRaster: true,
      promoteShapesAboveRaster: false,
      retainedChildPaint: "slide-root",
    }
  );
  if (!selected.length) {
    return {
      layer: null,
      quality: imageFallbackSlide(slideNumber, "no-safe-native-elements"),
      fontRendering,
    };
  }
  let backplate = await renderAuthoredBackplate(
    collectedHtml,
    contract,
    selected.map((element) => element.source.id),
    options
  );
  const finalSelected = selectLayerSafeNativeElements(
    contract.elements,
    prepared,
    new Set(backplate.appliedPromotedElementIds),
    {
      promoteTextAboveRaster: true,
      promoteShapesAboveRaster: false,
      retainedChildPaint: "slide-root",
    }
  );
  if (!finalSelected.length) {
    return {
      layer: null,
      quality: imageFallbackSlide(
        slideNumber,
        "backplate-identity-mismatch"
      ),
      fontRendering,
    };
  }
  if (!sameIds(finalSelected, backplate.appliedPromotedElementIds)) {
    selected = finalSelected;
    backplate = await renderAuthoredBackplate(
      collectedHtml,
      contract,
      selected.map((element) => element.source.id),
      options
    );
    if (!sameIds(selected, backplate.appliedPromotedElementIds)) {
      return {
        layer: null,
        quality: imageFallbackSlide(
          slideNumber,
          "backplate-identity-mismatch"
        ),
        fontRendering,
      };
    }
  } else {
    selected = finalSelected;
  }
  const selectedIds = new Set(
    selected.map((element) => element.source.id)
  );
  const fallbackElements = contract.elements
    .filter((element) => !selectedIds.has(element.id))
    .map(fallbackElementForContractElement);
  return {
    layer: {
      slideNumber,
      backplatePng: backplate.backplatePng,
      elements: selected,
    },
    quality: {
      slideNumber,
      editable: true,
      imageFallback: false,
      nativeTextElements: selected.filter((element) => element.kind === "text")
        .length,
      nativeShapeElements: selected.filter(
        (element) => element.kind === "shape"
      ).length,
      nativeGroupElements: 0,
      nativeImageElements: selected.filter(
        (element) => element.kind === "image"
      ).length,
      rasterFallbackElements: fallbackElements.length,
      fallbackReasons: fallbackElements.flatMap((element) => element.reasons),
      fallbackElements,
    },
    fontRendering,
  };
}

async function mapConcurrent(items, concurrency, callback) {
  const output = new Array(items.length);
  let next = 0;
  async function worker() {
    while (next < items.length) {
      const index = next++;
      output[index] = await callback(items[index], index);
    }
  }
  await Promise.all(
    Array.from({ length: Math.min(concurrency, items.length) }, worker)
  );
  return output;
}

const [
  python,
  database,
  presentationId,
  sourcePath,
  outputPath,
  qaDirectory,
  embeddingMode = "plain",
  textFidelityMode = "default",
] = process.argv.slice(2);
if (
  !python ||
  !database ||
  !presentationId ||
  !sourcePath ||
  !outputPath ||
  !qaDirectory
) {
  usage();
}
if (!["plain", "embedded"].includes(embeddingMode)) usage();
if (!["default", "powerpoint-calibrated"].includes(textFidelityMode)) usage();
const resolvedTextFidelityMode =
  textFidelityMode === "powerpoint-calibrated"
    ? textFidelityMode
    : undefined;

const slides = await readSlides(python, database, presentationId);
if (slides.length !== 20) {
  throw new Error(`Expected 20 slides, received ${slides.length}.`);
}
const chromeExecutable = await resolveAuthoredHybridChromeExecutable();
if (!chromeExecutable) throw new Error("Chrome/Chromium is unavailable.");
await fs.mkdir(qaDirectory, { recursive: true });

const startedAt = new Date().toISOString();
const sourceBytes = await fs.readFile(sourcePath);
const embeddingPlan = await resolveFontEmbeddingPlan({
  requested: embeddingMode === "embedded",
  families: ["Noto Sans KR"],
  strategy: "full",
  allowVariableFonts: false,
});
let basePptx = sourceBytes;
let embeddedTypefaceFamilies = [];
let fontEmbeddingStatus = {
  policy: "opt-in",
  requested: false,
  applied: false,
  embeddedFontFiles: 0,
  embeddedTypefaces: 0,
  strategy: "full",
  editLimitation: "none",
  reason: "not-requested",
};
if (embeddingMode === "embedded") {
  if (!embeddingPlan.status.eligible) {
    throw new Error(
      `Font embedding plan is not eligible: ${JSON.stringify(embeddingPlan.status.failures)}`
    );
  }
  const filesById = new Map(
    embeddingPlan.files.map((file) => [file.sourceId, file])
  );
  const byFamily = new Map();
  for (const face of embeddingPlan.faces) {
    const file = filesById.get(face.sourceId);
    if (!file) continue;
    const faces = byFamily.get(face.family) ?? {};
    faces[face.face] = {
      data: file.data,
      subset: false,
      source: file.sourceId,
    };
    byFamily.set(face.family, faces);
  }
  const packaged = embedPowerPointFonts(
    sourceBytes,
    [...byFamily.entries()].map(([typeface, faces]) => ({
      typeface,
      pitchFamily: 34,
      charset: 129,
      faces,
    }))
  );
  if (!packaged.result.applied) {
    throw new Error(
      packaged.result.reason ?? "OOXML font packaging was not applied."
    );
  }
  basePptx = packaged.pptx;
  embeddedTypefaceFamilies = packaged.result.fonts.map(
    (font) => font.typeface
  );
  const plannedFaces = new Map(
    embeddingPlan.faces.map((face) => [
      `${face.family}\0${face.sourceId}\0${face.face}`,
      face,
    ])
  );
  fontEmbeddingStatus = {
    policy: "opt-in",
    requested: true,
    applied: true,
    embeddedFontFiles: packaged.result.embeddedFontFiles,
    embeddedTypefaces: packaged.result.embeddedTypefaces,
    strategy: "full",
    editLimitation: "none",
    reason: "embedded",
    faces: packaged.result.fonts.flatMap((font) =>
      font.faces.map((face) => {
        const sourceId = face.source ?? "";
        const file = filesById.get(sourceId);
        const planned = plannedFaces.get(
          `${font.typeface}\0${sourceId}\0${face.face}`
        );
        return {
          typeface: font.typeface,
          face: face.face,
          weight: planned?.weight ?? face.weight,
          style: planned?.style ?? (face.italic ? "italic" : "normal"),
          source: file?.source ?? "server-font-allowlist",
          sourcePath: file?.sourcePath,
          sourceSha256: face.sourceSha256,
          sourceBytes: face.sourceBytes,
          embeddedBytes: face.embeddedBytes,
          fsType: face.fsType,
          licenseDecision: file?.license.decision ?? "denied-invalid",
          subset: false,
          strategy: "full",
          partName: face.partName,
          format: face.format,
          derivedFromVariable: file?.derivedFromVariable === true,
        };
      })
    ),
  };
}
const outcomes = await mapConcurrent(slides, 2, async (slide, index) => {
  const slideNumber = index + 1;
  process.stderr.write(`[qa-hybrid] preparing ${slideNumber}/20\n`);
  try {
    return await prepareSlide(
      slide.html,
      slideNumber,
      chromeExecutable,
      embeddedTypefaceFamilies,
      resolvedTextFidelityMode
    );
  } catch (error) {
    process.stderr.write(
      `[qa-hybrid] slide ${slideNumber} fallback: ${String(error)}\n`
    );
    return {
      layer: null,
      quality: imageFallbackSlide(slideNumber, "slide-processing-failed"),
      fontRendering: noFontRenderingTelemetry(),
    };
  }
});
const layers = outcomes.map((outcome) => outcome.layer).filter(Boolean);
const quality = createPresentationExportQuality(
  "hybrid",
  outcomes.map((outcome) => outcome.quality),
  {
    fontEmbeddingStatus,
    fontRendering: {
      browserFontFilesCollected: outcomes.reduce(
        (sum, outcome) =>
          sum + outcome.fontRendering.browserFontFilesCollected,
        0
      ),
      browserCollectionFailures: outcomes.reduce(
        (sum, outcome) =>
          sum + outcome.fontRendering.browserCollectionFailures,
        0
      ),
    },
  }
);
const pptxBytes = assembleAuthoredHybridPptx(basePptx, layers, {
  embeddedTypefaceFamilies,
  textFidelityMode: resolvedTextFidelityMode,
});
await fs.writeFile(outputPath, pptxBytes, { flag: "wx", mode: 0o644 });
await fs.writeFile(
  `${outputPath}.quality.json`,
  JSON.stringify(quality, null, 2),
  { flag: "wx", mode: 0o644 }
);
const structural = inspectPptxEditability(pptxBytes);
await fs.writeFile(
  path.join(qaDirectory, "structural-editability.json"),
  JSON.stringify(structural, null, 2)
);
await fs.writeFile(
  path.join(qaDirectory, "export-manifest.json"),
  JSON.stringify(
    {
      startedAt,
      completedAt: new Date().toISOString(),
      presentationId,
      sourcePath: path.resolve(sourcePath),
      outputPath: path.resolve(outputPath),
      database: path.resolve(database),
      chromeExecutable,
      embeddingMode,
      textFidelityMode,
      fontEmbeddingStatus,
      slideCount: slides.length,
      quality,
      structural,
    },
    null,
    2
  )
);
console.log(
  JSON.stringify({
    outputPath: path.resolve(outputPath),
    qualityPath: path.resolve(`${outputPath}.quality.json`),
    qaDirectory: path.resolve(qaDirectory),
    quality,
    structural,
  })
);
