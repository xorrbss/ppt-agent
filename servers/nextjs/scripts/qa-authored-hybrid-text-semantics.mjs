import { spawn } from "node:child_process";
import fs from "node:fs/promises";
import path from "node:path";

import {
  extractAuthoredSlideDom,
  resolveAuthoredHybridChromeExecutable,
} from "../lib/authored-hybrid/index.ts";
import { collectGoogleFontsForAuthoredHtml } from "../lib/authored-hybrid/google-font-collector.ts";
import {
  prepareNativeElements,
  serializePreparedNativeElement,
} from "../lib/authored-hybrid/native-plan.ts";
import { preflightAuthoredHtmlForHybrid } from "../lib/authored-hybrid/security.ts";

function usage() {
  throw new Error(
    "Usage: qa-authored-hybrid-text-semantics.mjs <python> <db> " +
      "<presentation-id> [output.json]"
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

function isBold(style) {
  return style.bold === true || style.fontWeight >= 600;
}

function nonEmptyRuns(element) {
  return element.text.runs.filter(
    (run) => run.text.replace(/\s+/g, "").length > 0
  );
}

function auditText(item, slideNumber, itemIndex) {
  const element = item.source;
  const xml = serializePreparedNativeElement(item, itemIndex + 2);
  const paragraphs = xml.match(/<a:p>.*?<\/a:p>/g) ?? [];
  const actualAlignments = paragraphs.map(
    (paragraph) => paragraph.match(/<a:pPr\b[^>]*\balgn="([^"]+)"/)?.[1]
  );
  const expectedAlignment = {
    left: "l",
    center: "ctr",
    right: "r",
    justify: "just",
  }[element.text.style.horizontalAlignment];
  const centeredMisclassified = actualAlignments.some(
    (alignment) =>
      (expectedAlignment === "ctr" && alignment !== "ctr") ||
      (expectedAlignment !== "ctr" && alignment === "ctr")
  );

  const runs = nonEmptyRuns(element);
  const expectedBoldStates = new Set(runs.map((run) => isBold(run.style)));
  const mixedBoldTarget =
    expectedBoldStates.has(true) && expectedBoldStates.has(false);
  const serializedBoldRuns = (xml.match(/\sb="1"/g) ?? []).length;
  const serializedRegularRuns = (xml.match(/\sb="0"/g) ?? []).length;
  const hasSerializedBold = serializedBoldRuns > 0;
  const hasSerializedRegular = serializedRegularRuns > 0;
  const partialBoldLost =
    mixedBoldTarget && (!hasSerializedBold || !hasSerializedRegular);

  return {
    slideNumber,
    id: element.id,
    expectedAlignment,
    paragraphCount: paragraphs.length,
    centeredMisclassified,
    mixedBoldTarget,
    partialBoldLost,
    textRunsMatch:
      element.text.runs.map((run) => run.text).join("") ===
      element.text.plainText,
    softStrippedRunsMatch:
      element.text.runs
        .map((run) =>
          run.breakKind === "soft" ? run.text.replace(/\n/g, "") : run.text
        )
        .join("") === element.text.plainText,
    boldRuns: runs.filter((run) => isBold(run.style)).length,
    regularRuns: runs.filter((run) => !isBold(run.style)).length,
    serializedBoldRuns,
    serializedRegularRuns,
    plainText: element.text.plainText,
    joinedRunText: element.text.runs.map((run) => run.text).join(""),
    runStyles: runs.map((run) => ({
      text: run.text,
      bold: isBold(run.style),
      breakKind: run.breakKind,
    })),
    allRuns: element.text.runs.map((run) => ({
      text: run.text,
      bold: isBold(run.style),
      breakKind: run.breakKind,
    })),
  };
}

const argv = process.argv.slice(2);
if (argv.length < 3 || argv.length > 4) usage();
const [python, database, presentationId, outputPath] = argv;
const requestedSlide = Number.parseInt(
  process.env.AUTHORED_HYBRID_QA_SLIDE ?? "",
  10
);
const [slides, chromeExecutable] = await Promise.all([
  readSlides(python, database, presentationId),
  resolveAuthoredHybridChromeExecutable(),
]);
if (!chromeExecutable) throw new Error("Chrome/Chromium is unavailable.");

const details = [];
const slideSummaries = [];
for (const [slideIndex, slide] of slides.entries()) {
  if (Number.isFinite(requestedSlide) && requestedSlide !== slideIndex + 1) {
    continue;
  }
  const collection = await collectGoogleFontsForAuthoredHtml(slide.html);
  const preflight = preflightAuthoredHtmlForHybrid(collection.html);
  if (!preflight.ok) {
    throw new Error(
      `Slide ${slideIndex + 1} preflight failed: ${preflight.reason}`
    );
  }
  const contract = await extractAuthoredSlideDom(collection.html, {
    chromeExecutable,
    timeoutMs: 40_000,
  });
  const prepared = await prepareNativeElements(contract.elements, {
    includeRasterText: true,
  });
  const textItems = prepared.filter((item) => item.kind === "text");
  const audited = textItems.map((item, itemIndex) =>
    auditText(item, slideIndex + 1, itemIndex)
  );
  details.push(...audited);
  slideSummaries.push({
    slideNumber: slideIndex + 1,
    nativeText: audited.length,
    centeredTargets: audited.filter(
      (entry) => entry.expectedAlignment === "ctr"
    ).length,
    centeredMisclassifications: audited.filter(
      (entry) => entry.centeredMisclassified
    ).length,
    partialBoldTargets: audited.filter((entry) => entry.mixedBoldTarget).length,
    partialBoldLosses: audited.filter((entry) => entry.partialBoldLost).length,
  });
}

const report = {
  schema: "presenton.authored-hybrid-text-semantics/v1",
  generatedAt: new Date().toISOString(),
  presentationId,
  database: path.resolve(database),
  slideCount: slides.length,
  summary: {
    nativeText: details.length,
    centeredTargets: details.filter(
      (entry) => entry.expectedAlignment === "ctr"
    ).length,
    centeredMisclassifications: details.filter(
      (entry) => entry.centeredMisclassified
    ).length,
    partialBoldTargets: details.filter((entry) => entry.mixedBoldTarget).length,
    partialBoldLosses: details.filter((entry) => entry.partialBoldLost).length,
  },
  slides: slideSummaries,
  violations: details.filter(
    (entry) => entry.centeredMisclassified || entry.partialBoldLost
  ),
};

if (outputPath) {
  await fs.mkdir(path.dirname(outputPath), { recursive: true });
  await fs.writeFile(outputPath, `${JSON.stringify(report, null, 2)}\n`);
}
console.log(JSON.stringify(report, null, 2));
if (
  report.summary.centeredMisclassifications > 0 ||
  report.summary.partialBoldLosses > 0
) {
  process.exitCode = 1;
}
