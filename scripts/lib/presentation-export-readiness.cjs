const fs = require("node:fs");
const path = require("node:path");

const rendererReadinessNeedle =
  "await BXr(s),p&&await QXr(s),await s.screenshot";
const rendererReadinessLegacyReplacement =
  'await BXr(s),p&&await QXr(s),await s.waitForFunction(()=>!document.querySelector("script[data-presenton-template-v2-readiness]")||document.documentElement.dataset.templateV2RenderReady==="true",{timeout:15e3}),await s.screenshot';
const rendererReadinessReplacement =
  'await BXr(s),p&&await QXr(s),await s.waitForFunction(()=>!document.querySelector("script[data-presenton-template-v2-readiness]")||document.documentElement.dataset.templateV2RenderReady==="true"||document.documentElement.dataset.templateV2ResourceError==="true",{timeout:15e3}),await s.evaluate(()=>{if(document.documentElement.dataset.templateV2ResourceError==="true")throw new Error(`Template V2 required resource failed: ${document.documentElement.dataset.templateV2ResourceErrorCode||"unknown"}`)}),await s.screenshot';
const renderArtifactNeedle =
  'try{await a.waitForFunction(()=>document.readyState==="complete")}catch{}try{await Att(a)}catch{}';
const renderArtifactReplacement =
  'try{await a.waitForFunction(()=>document.readyState==="complete")}catch{}await a.waitForFunction(()=>{let c=new URL(location.href).searchParams,l=c.get("render_contract");if(!l)return!0;let p=document.querySelector("[data-render-artifact-contract]");if(!p||p.getAttribute("data-render-artifact-contract")!==l)return!1;let C=c.get("format"),b=c.get("source_sha256"),k=p.getAttribute("data-render-artifact-version")||"";return(!C||p.getAttribute("data-render-artifact-target")===C)&&k.includes("/revision-")&&(!b||k.endsWith(`/${b}`))},{timeout:15e3});try{await Att(a)}catch{}';
const rendererV048Needle =
  'try{await i.waitForFunction(()=>document.readyState==="complete")}catch{}try{await nnt(i)}catch{}';
const rendererV048Replacement =
  'try{await i.waitForFunction(()=>document.readyState==="complete")}catch{}await i.waitForFunction(()=>!document.querySelector("script[data-presenton-template-v2-readiness]")||document.documentElement.dataset.templateV2RenderReady==="true"||document.documentElement.dataset.templateV2ResourceError==="true",{timeout:15e3}),await i.evaluate(()=>{if(document.documentElement.dataset.templateV2ResourceError==="true")throw new Error(`Template V2 required resource failed: ${document.documentElement.dataset.templateV2ResourceErrorCode||"unknown"}`)}),await i.waitForFunction(()=>{let c=new URL(location.href).searchParams,l=c.get("render_contract");if(!l)return!0;let p=document.querySelector("[data-render-artifact-contract]");if(!p||p.getAttribute("data-render-artifact-contract")!==l)return!1;let C=c.get("format"),b=c.get("source_sha256"),k=p.getAttribute("data-render-artifact-version")||"";return(!C||p.getAttribute("data-render-artifact-target")===C)&&k.includes("/revision-")&&(!b||k.endsWith(`/${b}`))},{timeout:15e3});try{await nnt(i)}catch{}';

function count(source, needle) {
  return source.split(needle).length - 1;
}

/** Apply the fork's fail-closed readiness contract to the pinned bundle. */
function ensureRendererReadinessOverlay(runtimeRoot, { repair = true } = {}) {
  const indexJs = path.join(runtimeRoot, "index.js");
  if (!fs.existsSync(indexJs)) {
    return { ok: false, reason: `Missing runtime bundle: ${indexJs}` };
  }
  const source = fs.readFileSync(indexJs, "utf8");
  const v048SourceCount = count(source, rendererV048Needle);
  const v048PatchedCount = count(source, rendererV048Replacement);
  if (v048SourceCount === 0 && v048PatchedCount === 1) {
    return { ok: true };
  }
  if (v048SourceCount === 1 && v048PatchedCount === 0) {
    if (!repair) {
      return {
        ok: false,
        reason: `Runtime browser capture readiness overlay is missing or ambiguous: ${indexJs}`,
      };
    }
    fs.writeFileSync(
      indexJs,
      source.replace(rendererV048Needle, rendererV048Replacement),
      "utf8"
    );
    return { ok: true };
  }
  if (v048SourceCount !== 0 || v048PatchedCount !== 0) {
    return {
      ok: false,
      reason: `Runtime browser capture readiness overlay cannot be applied safely: ${indexJs}`,
    };
  }
  const sourceCount = count(source, rendererReadinessNeedle);
  const legacyCount = count(source, rendererReadinessLegacyReplacement);
  const patchedCount = count(source, rendererReadinessReplacement);
  const artifactSourceCount = count(source, renderArtifactNeedle);
  const artifactPatchedCount = count(source, renderArtifactReplacement);
  if (
    sourceCount === 0 &&
    legacyCount === 0 &&
    patchedCount === 1 &&
    artifactSourceCount === 0 &&
    artifactPatchedCount === 1
  ) {
    return { ok: true };
  }
  if (!repair) {
    return {
      ok: false,
      reason: `Runtime browser capture readiness overlay is missing or ambiguous: ${indexJs}`,
    };
  }
  const rendererAlreadyPatched =
    sourceCount === 0 && legacyCount === 0 && patchedCount === 1;
  const rendererRepairSource = rendererAlreadyPatched
    ? undefined
    : sourceCount === 1 && legacyCount === 0 && patchedCount === 0
      ? rendererReadinessNeedle
      : sourceCount === 0 && legacyCount === 1 && patchedCount === 0
        ? rendererReadinessLegacyReplacement
        : null;
  const artifactRepairable =
    (artifactSourceCount === 1 && artifactPatchedCount === 0) ||
    (artifactSourceCount === 0 && artifactPatchedCount === 1);
  if (rendererRepairSource === null || !artifactRepairable) {
    return {
      ok: false,
      reason: `Runtime browser capture readiness overlay cannot be applied safely: ${indexJs}`,
    };
  }
  const rendererPatched = rendererRepairSource
    ? source.replace(rendererRepairSource, rendererReadinessReplacement)
    : source;
  const fullyPatched =
    artifactPatchedCount === 1
      ? rendererPatched
      : rendererPatched.replace(renderArtifactNeedle, renderArtifactReplacement);
  fs.writeFileSync(indexJs, fullyPatched, "utf8");
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
