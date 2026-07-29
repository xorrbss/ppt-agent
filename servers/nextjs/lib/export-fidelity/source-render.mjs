import { runAuthoredHybridChrome } from "../authored-hybrid/chrome-runner.ts";

/** Render a self-contained Template V2 export surface with the existing fixed-canvas Chrome runner. */
export async function renderTemplateV2SourceHtml(html, options = {}) {
  const result = await runAuthoredHybridChrome({
    html,
    dumpDom: false,
    screenshot: true,
    chromeExecutable: options.chromeExecutable,
    timeoutMs: options.timeoutMs ?? 30_000,
    windowSizePx: { width: 1280, height: 720 },
  });
  if (!result.screenshotPng) {
    throw new Error("Chrome completed without a Template V2 source screenshot.");
  }
  return result.screenshotPng;
}
