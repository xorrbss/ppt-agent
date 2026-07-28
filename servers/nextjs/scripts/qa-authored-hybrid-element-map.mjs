import { spawn } from "node:child_process";
import fs from "node:fs/promises";
import path from "node:path";

import {
  extractAuthoredSlideDom,
  resolveAuthoredHybridChromeExecutable,
} from "../lib/authored-hybrid/index.ts";
import { collectGoogleFontsForAuthoredHtml } from "../lib/authored-hybrid/google-font-collector.ts";
import { preflightAuthoredHtmlForHybrid } from "../lib/authored-hybrid/security.ts";
import {
  resolveAuthoredHybridTextProfile,
  selectNativeTextFidelity,
} from "../lib/authored-hybrid/text-fidelity.ts";

function usage() {
  throw new Error(
    "Usage: qa-authored-hybrid-element-map.mjs <python> <db> " +
      "<presentation-id> <output.json> [--overwrite]"
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

function textObservation(element) {
  if (!element?.text) return undefined;
  const style = element.text.style ?? {};
  const fidelity = selectNativeTextFidelity(
    element.text,
    element.text.layout?.boxBounds?.px ?? element.bounds.px
  );
  return {
    role: element.text.role,
    profile: resolveAuthoredHybridTextProfile(element.text),
    calibratedTransform: fidelity.transform,
    textLength: element.text.text?.length,
    runCount: element.text.runs?.length ?? 0,
    font: {
      family: style.fontFamily,
      authoredFamilies: style.authoredFontFamilies,
      mappedFrom: style.mappedFromFontFamily,
      sizePt: style.fontSizePt,
      weight: style.fontWeight,
      letterSpacingPt: style.letterSpacingPt,
      lineHeight: style.lineHeight,
      horizontalAlignment: style.horizontalAlignment,
      verticalAlignment: style.verticalAlignment,
      inset: style.inset,
    },
    layout: element.text.layout
      ? {
          boxBounds: element.text.layout.boxBounds?.px,
          contentBounds: element.text.layout.contentBounds?.px,
          paintedTextBounds: element.text.layout.paintedTextBounds?.px,
          paddingPx: element.text.layout.paddingPx,
          borderPx: element.text.layout.borderPx,
          marginPx: element.text.layout.marginPx,
          rowGapPx: element.text.layout.rowGapPx,
          columnGapPx: element.text.layout.columnGapPx,
          display: element.text.layout.display,
          flexDirection: element.text.layout.flexDirection,
          alignItems: element.text.layout.alignItems,
          justifyContent: element.text.layout.justifyContent,
          textAlignSource: element.text.layout.textAlignSource,
          lineCount: element.text.layout.lineCount,
          singleLine: element.text.layout.singleLine,
          paragraphSpacingPx: element.text.layout.paragraphSpacingPx,
        }
      : undefined,
  };
}

function compactElement(element, powerPointElement) {
  const classification = element.classification;
  const bounds = element.bounds?.px;
  const powerPointBounds = powerPointElement?.bounds?.px;
  return {
    id: element.id,
    domPath: element.domPath,
    tagName: element.tagName,
    zOrder: element.zOrder,
    cssZIndex: element.cssZIndex,
    rotationDeg: element.rotationDeg,
    opacity: element.opacity,
    kind:
      classification.mode === "native"
        ? classification.kind
        : classification.candidateKind,
    mode: classification.mode,
    reasons: classification.mode === "raster" ? classification.reasons : [],
    bounds,
    text: textObservation(element),
    powerPoint: powerPointElement
      ? {
          bounds: powerPointBounds,
          text: textObservation(powerPointElement),
          geometryDelta: bounds &&
            powerPointBounds && {
              x: powerPointBounds.x - bounds.x,
              y: powerPointBounds.y - bounds.y,
              width: powerPointBounds.width - bounds.width,
              height: powerPointBounds.height - bounds.height,
            },
        }
      : undefined,
  };
}

function summarizeFontGeometry(elements) {
  const observations = elements
    .filter(
      (element) =>
        element.text?.font?.family &&
        element.powerPoint?.text?.font?.family &&
        element.bounds?.width > 0 &&
        element.powerPoint.bounds?.width > 0
    )
    .map((element) => ({
      authoredFamily: element.text.font.family,
      powerPointFamily: element.powerPoint.text.font.family,
      widthDelta: element.powerPoint.bounds.width - element.bounds.width,
      heightDelta: element.powerPoint.bounds.height - element.bounds.height,
      widthRatio: element.powerPoint.bounds.width / element.bounds.width,
    }));
  const mean = (values) =>
    values.length
      ? values.reduce((sum, value) => sum + value, 0) / values.length
      : 0;
  const mappings = {};
  for (const observation of observations) {
    const key = `${observation.authoredFamily} -> ${observation.powerPointFamily}`;
    const entry = (mappings[key] ??= {
      count: 0,
      widthDeltas: [],
      heightDeltas: [],
      widthRatios: [],
    });
    entry.count += 1;
    entry.widthDeltas.push(observation.widthDelta);
    entry.heightDeltas.push(observation.heightDelta);
    entry.widthRatios.push(observation.widthRatio);
  }
  return {
    count: observations.length,
    meanWidthDeltaPx: mean(observations.map((item) => item.widthDelta)),
    meanAbsoluteWidthDeltaPx: mean(
      observations.map((item) => Math.abs(item.widthDelta))
    ),
    meanHeightDeltaPx: mean(observations.map((item) => item.heightDelta)),
    meanWidthRatio: mean(observations.map((item) => item.widthRatio)),
    mappings: Object.fromEntries(
      Object.entries(mappings)
        .sort((left, right) => right[1].count - left[1].count)
        .map(([key, entry]) => [
          key,
          {
            count: entry.count,
            meanWidthDeltaPx: mean(entry.widthDeltas),
            meanAbsoluteWidthDeltaPx: mean(
              entry.widthDeltas.map((value) => Math.abs(value))
            ),
            meanHeightDeltaPx: mean(entry.heightDeltas),
            meanWidthRatio: mean(entry.widthRatios),
          },
        ])
    ),
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

const argv = process.argv.slice(2);
const overwriteIndex = argv.indexOf("--overwrite");
const overwrite = overwriteIndex >= 0;
if (overwrite) argv.splice(overwriteIndex, 1);
if (argv.length !== 4) usage();
const [python, database, presentationId, outputPath] = argv;
if (!overwrite) {
  try {
    await fs.access(outputPath);
    throw new Error(
      `Element map already exists at ${outputPath}; use a new path or --overwrite.`
    );
  } catch (error) {
    if (error?.code !== "ENOENT") throw error;
  }
}

const [slides, chromeExecutable] = await Promise.all([
  readSlides(python, database, presentationId),
  resolveAuthoredHybridChromeExecutable(),
]);
if (!chromeExecutable) throw new Error("Chrome/Chromium is unavailable.");

const mappedSlides = await mapConcurrent(slides, 2, async (slide, index) => {
  const collection = await collectGoogleFontsForAuthoredHtml(slide.html);
  const preflight = preflightAuthoredHtmlForHybrid(collection.html);
  if (!preflight.ok) {
    return {
      slideNumber: index + 1,
      sourceIndex: slide.index,
      preflight: preflight.reason ?? "html-preflight-failed",
      elements: [],
    };
  }
  const options = { chromeExecutable, timeoutMs: 20_000 };
  const [authored, powerPoint] = await Promise.all([
    extractAuthoredSlideDom(collection.html, options),
    extractAuthoredSlideDom(collection.html, {
      ...options,
      fontLayoutMode: "powerpoint",
    }),
  ]);
  const powerPointById = new Map(
    powerPoint.elements.map((element) => [element.id, element])
  );
  const elements = authored.elements.map((element) =>
    compactElement(element, powerPointById.get(element.id))
  );
  return {
    slideNumber: index + 1,
    sourceIndex: slide.index,
    canvas: authored.canvas,
    fontCollection: {
      requestedFamilies: collection.requestedFamilies,
      collectedFamilies: collection.collectedFamilies,
      unresolvedFamilies: collection.unresolvedFamilies,
    },
    fontGeometry: summarizeFontGeometry(elements),
    elements,
  };
});

const reasonFrequency = {};
const fontFrequency = {};
for (const slide of mappedSlides) {
  for (const element of slide.elements) {
    for (const reason of element.reasons) {
      reasonFrequency[reason] = (reasonFrequency[reason] ?? 0) + 1;
    }
    const family = element.text?.font?.family;
    if (family) fontFrequency[family] = (fontFrequency[family] ?? 0) + 1;
  }
}
const output = {
  schema: "presenton.authored-hybrid-element-map/v1",
  generatedAt: new Date().toISOString(),
  presentationId,
  database: path.resolve(database),
  slideCount: mappedSlides.length,
  summary: {
    elementCount: mappedSlides.reduce(
      (sum, slide) => sum + slide.elements.length,
      0
    ),
    rasterClassifiedElements: mappedSlides.reduce(
      (sum, slide) =>
        sum + slide.elements.filter((element) => element.mode === "raster").length,
      0
    ),
    reasonFrequency: Object.fromEntries(
      Object.entries(reasonFrequency).sort((left, right) => right[1] - left[1])
    ),
    fontFrequency: Object.fromEntries(
      Object.entries(fontFrequency).sort((left, right) => right[1] - left[1])
    ),
    fontGeometry: summarizeFontGeometry(
      mappedSlides.flatMap((slide) => slide.elements)
    ),
  },
  slides: mappedSlides,
};
await fs.mkdir(path.dirname(outputPath), { recursive: true });
await fs.writeFile(outputPath, `${JSON.stringify(output, null, 2)}\n`);
console.log(JSON.stringify(output.summary, null, 2));
