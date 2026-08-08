import { runAuthoredHybridChrome } from "../authored-hybrid/chrome-runner.ts";

/** Render a self-contained Template V2 export surface with the existing fixed-canvas Chrome runner. */
export async function renderTemplateV2SourceHtml(html, options = {}) {
  const result = await runAuthoredHybridChrome({
    html,
    dumpDom: false,
    screenshot: true,
    chromeExecutable: options.chromeExecutable,
    // Hosted macOS ARM runners can spend more than 30 seconds in the first
    // Chromium cold start. Keep the fidelity gate strict, but allow the capture
    // to finish instead of reporting an infrastructure timeout as a regression.
    timeoutMs: options.timeoutMs ?? 60_000,
    windowSizePx: { width: 1280, height: 720 },
  });
  if (!result.screenshotPng) {
    throw new Error("Chrome completed without a Template V2 source screenshot.");
  }
  return result.screenshotPng;
}
