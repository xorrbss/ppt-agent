const fs = require("node:fs");
const path = require("node:path");

const rendererReadinessNeedle =
  "await BXr(s),p&&await QXr(s),await s.screenshot";
const rendererReadinessLegacyReplacement =
  'await BXr(s),p&&await QXr(s),await s.waitForFunction(()=>!document.querySelector("script[data-presenton-template-v2-readiness]")||document.documentElement.dataset.templateV2RenderReady==="true",{timeout:15e3}),await s.screenshot';
const rendererReadinessReplacement =
  'await BXr(s),p&&await QXr(s),await s.waitForFunction(()=>!document.querySelector("script[data-presenton-template-v2-readiness]")||document.documentElement.dataset.templateV2RenderReady==="true"||document.documentElement.dataset.templateV2ResourceError==="true",{timeout:15e3}),await s.evaluate(()=>{if(document.documentElement.dataset.templateV2ResourceError==="true")throw new Error(`Template V2 required resource failed: ${document.documentElement.dataset.templateV2ResourceErrorCode||"unknown"}`)}),await s.screenshot';

/** Apply the fork's fail-closed readiness contract to the pinned bundle. */
function ensureRendererReadinessOverlay(runtimeRoot, { repair = true } = {}) {
  const indexJs = path.join(runtimeRoot, "index.js");
  if (!fs.existsSync(indexJs)) {
    return { ok: false, reason: `Missing runtime bundle: ${indexJs}` };
  }
  const source = fs.readFileSync(indexJs, "utf8");
  const sourceCount = source.split(rendererReadinessNeedle).length - 1;
  const legacyCount = source.split(rendererReadinessLegacyReplacement).length - 1;
  const patchedCount = source.split(rendererReadinessReplacement).length - 1;
  if (sourceCount === 0 && legacyCount === 0 && patchedCount === 1) return { ok: true };
  if (!repair) {
    return {
      ok: false,
      reason: `Runtime browser capture readiness overlay is missing or ambiguous: ${indexJs}`,
    };
  }
  const repairSource =
    sourceCount === 1 && legacyCount === 0 && patchedCount === 0
      ? rendererReadinessNeedle
      : sourceCount === 0 && legacyCount === 1 && patchedCount === 0
        ? rendererReadinessLegacyReplacement
        : null;
  if (repairSource === null) {
    return {
      ok: false,
      reason: `Runtime browser capture readiness overlay cannot be applied safely: ${indexJs}`,
    };
  }
  fs.writeFileSync(
    indexJs,
    source.replace(repairSource, rendererReadinessReplacement),
    "utf8"
  );
  return { ok: true };
}

function ensureCommonJsEntrypoint(runtimeRoot, { repair = true } = {}) {
  const indexJs = path.join(runtimeRoot, "index.js");
  const indexCjs = path.join(runtimeRoot, "index.cjs");
  if (!fs.existsSync(indexJs)) {
    return { ok: false, reason: `Missing runtime bundle: ${indexJs}` };
  }
  if (!repair) {
    if (!fs.existsSync(indexCjs)) {
      return { ok: false, reason: `Missing CommonJS runtime bundle: ${indexCjs}` };
    }
    try {
      if (!fs.readFileSync(indexJs).equals(fs.readFileSync(indexCjs))) {
        return {
          ok: false,
          reason: `CommonJS runtime bundle does not match ${indexJs}: ${indexCjs}`,
        };
      }
      return { ok: true, entrypointPath: indexCjs };
    } catch (error) {
      return {
        ok: false,
        reason: `Failed to verify CommonJS entrypoint ${indexCjs}: ${error.message}`,
      };
    }
  }
  try {
    fs.copyFileSync(indexJs, indexCjs);
    return { ok: true, entrypointPath: indexCjs };
  } catch (error) {
    return {
      ok: false,
      reason: `Failed to create CommonJS entrypoint ${indexCjs}: ${error.message}`,
    };
  }
}

module.exports = { ensureCommonJsEntrypoint, ensureRendererReadinessOverlay };
