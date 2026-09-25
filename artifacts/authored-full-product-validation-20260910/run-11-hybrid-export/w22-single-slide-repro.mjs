import { readFile, writeFile } from "node:fs/promises";
import path from "node:path";
import { pathToFileURL } from "node:url";

const ROOT = path.resolve(import.meta.dirname);
const NEXTJS = path.resolve(ROOT, "..", "..", "..", "servers", "nextjs");
const JOB_PATH = path.join(ROOT, "w14-direct-repro", "job-deck.json");
const SLIDE_INDEX = 19;

const { installNextjsModuleAliasHooks } = await import(
  pathToFileURL(path.join(NEXTJS, "scripts", "lib", "nextjs-module-alias-hooks.mjs")).href
);
installNextjsModuleAliasHooks();

const libRoot = pathToFileURL(path.join(NEXTJS, "lib", path.sep)).href;
const [index, slideLayer, assembler, nativePlan, fontEmbedding, fontPolicy] = await Promise.all([
  import(`${libRoot}authored-hybrid/index.ts`),
  import(`${libRoot}authored-hybrid/slide-layer.ts`),
  import(`${libRoot}authored-hybrid/pptx-assembler.ts`),
  import(`${libRoot}authored-hybrid/native-plan.ts`),
  import(`${libRoot}authored-hybrid/export-font-embedding.ts`),
  import(`${libRoot}authored-hybrid/font-embedding-policy.ts`),
]);

const job = JSON.parse(await readFile(JOB_PATH, "utf8"));
process.env.PRESENTON_APP_ROOT ??= path.resolve(NEXTJS, "..", "..");
process.env.APP_DATA_DIRECTORY = job.scratchDirectory;
process.env.DISABLE_GOOGLE_FONTS_COLLECTION = "true";
process.env.AUTHORED_VNEXT_RENDERER_ENABLED = "true";
process.env.AUTHORED_RENDERER_KILL_SWITCH = "false";
process.env.NODE_ENV = "development";
if (job.fontAllowlistDirectory) {
  process.env.PRESENTON_FONT_EMBEDDING_DIR = job.fontAllowlistDirectory;
}

const slide = job.slides[SLIDE_INDEX];
const declared = job.semanticSlides[SLIDE_INDEX];
const html = await readFile(slide.htmlPath, "utf8");
const basePptx = await readFile(job.basePptxPath);
const fontPlan = await fontPolicy.resolveFontEmbeddingPlan({
  requested: true,
  families: [
    ...new Set([
      ...(/(?:noto\s+sans\s+(?:kr|cjk)|pretendard|malgun|맑은\s*고딕)/iu.test(html)
        ? ["Noto Sans KR"]
        : []),
      ...fontEmbedding.bundledFamiliesReferencedByHtml([html]),
    ]),
  ],
  strategy: "full",
  allowVariableFonts: false,
});
let embedding;
try {
  embedding = await fontEmbedding.prepareFontEmbedding(basePptx, fontPlan);
} catch (error) {
  embedding = {
    pptx: basePptx,
    embeddedTypefaceFamilies: [],
    embeddedTypefaceFaces: [],
    error: error instanceof Error ? error.message : String(error),
  };
}

const chromeExecutable = await index.resolveAuthoredHybridChromeExecutable();
const observed = (await index.extractDeclaredAuthoredSlideDom(
  html,
  declared.declaredElementIds,
  {
    chromeExecutable,
    timeoutMs: 45_000,
    declaredElementIds: declared.declaredElementIds,
  },
)).slide;
const outcome = await slideLayer.prepareSlideLayer(
  html,
  SLIDE_INDEX + 1,
  chromeExecutable,
  embedding.embeddedTypefaceFamilies,
  embedding.embeddedTypefaceFaces,
  {
    exportAttemptId: job.exportAttemptId,
    inputBundleDigest: declared.bundleDigest,
    rendererSourceDigest: job.rendererSourceDigest,
    fontSetDigest: declared.fontSetDigest,
    declaredElementIds: declared.declaredElementIds,
    declaredElements: declared.declaredElements,
  },
  fontEmbedding.embeddedGlyphAvailable(fontPlan),
);

function findBySourceId(elements, id) {
  for (const element of elements ?? []) {
    if (element?.source?.id === id) return element;
    const nested = findBySourceId(element?.children, id);
    if (nested) return nested;
  }
  return null;
}

const observedTarget = observed.elements.find((element) => element.id === "q-phone-step-1") ?? null;
const target = findBySourceId(outcome.layer?.elements, "q-phone-step-1");
let assemblerResult = { ok: false, error: "layer_missing" };
if (outcome.layer && observedTarget) {
  try {
    const preparedTarget = await nativePlan.prepareNativeElements([{
      ...observedTarget,
      classification: { mode: "native", kind: "text", confidence: "safe" },
    }]);
    const bytes = await assembler.assembleAuthoredHybridPptxAsync(
      embedding.pptx,
      [{
        slideNumber: SLIDE_INDEX + 1,
        backplatePng: outcome.layer.backplatePng,
        elements: preparedTarget,
      }],
      { embeddedTypefaceFamilies: embedding.embeddedTypefaceFamilies },
    );
    assemblerResult = { ok: true, bytes: bytes.length };
  } catch (error) {
    assemblerResult = {
      ok: false,
      name: error instanceof Error ? error.name : null,
      code: error?.code ?? null,
      slideNumber: error?.slideNumber ?? null,
      message: error instanceof Error ? error.message : String(error),
    };
  }
}

const result = {
  schemaVersion: "w22-hybrid-text-break-repro/v1",
  slideNumber: SLIDE_INDEX + 1,
  target: observedTarget
    ? {
        id: observedTarget.id,
        selectedForNativeLayer: Boolean(target),
        classification: observedTarget.classification,
        plainText: observedTarget.text?.plainText,
        paragraphs: observedTarget.text?.paragraphs,
        runs: observedTarget.text?.runs,
        paragraphDerivedBreaks: Math.max(0, (observedTarget.text?.paragraphs.length ?? 1) - 1),
        measuredHardBreaks: observedTarget.text?.runs.reduce(
          (count, run) => count + (
            run.breakKind === "line" || run.breakKind === "paragraph"
              ? (run.text.match(/\n/g) ?? []).length
              : 0
          ),
          0,
        ),
      }
    : null,
  quality: outcome.quality,
  assembler: assemblerResult,
};
await writeFile(
  path.join(ROOT, "w22-root-cause-repro.json"),
  JSON.stringify(result, null, 2),
  "utf8",
);
console.log(JSON.stringify(result, null, 2));
