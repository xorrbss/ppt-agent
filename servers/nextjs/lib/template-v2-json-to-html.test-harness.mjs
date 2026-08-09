// Shared build harness for the `template-v2-json-to-html` characterization
// suites. The renderer is TypeScript with `@/` path aliases, so it is bundled
// once per test process with esbuild and cached for every test in the file.
import assert from "node:assert/strict";
import { mkdtemp } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

import { build } from "esbuild";

const projectRoot = path.resolve(
  path.dirname(fileURLToPath(import.meta.url)),
  "..",
);

let cachedModule = null;

export function loadTemplateV2HtmlRenderer() {
  cachedModule ??= (async () => {
    const tempDirectory = await mkdtemp(
      path.join(os.tmpdir(), "template-v2-html-golden-"),
    );
    const outfile = path.join(tempDirectory, "template-v2-json-to-html.mjs");
    await build({
      absWorkingDir: projectRoot,
      bundle: true,
      entryPoints: ["lib/template-v2-json-to-html.ts"],
      format: "esm",
      outfile,
      platform: "node",
      tsconfig: path.join(projectRoot, "tsconfig.json"),
    });
    return import(pathToFileURL(outfile).href);
  })();
  return cachedModule;
}

// Golden slide-root wrapper emitted by `renderSlideRoot` at the default size.
export const SLIDE_ROOT_OPEN_TAG =
  '<div class="relative overflow-hidden" data-template-v2-html-slide="true"' +
  ' style="box-sizing:border-box;position:relative;width:1280px;height:720px;' +
  'overflow:hidden;background:#FFFFFF;font-family:Arial,Helvetica,sans-serif">';

/**
 * Strips the (separately pinned) slide-root wrapper so element goldens stay
 * focused on the element markup itself.
 */
export function stripSlideRoot(fragment) {
  assert.equal(typeof fragment, "string");
  assert.ok(
    fragment.startsWith(SLIDE_ROOT_OPEN_TAG),
    `fragment did not start with the golden slide root: ${fragment.slice(0, 240)}`,
  );
  assert.ok(fragment.endsWith("</div>"));
  return fragment.slice(SLIDE_ROOT_OPEN_TAG.length, -"</div>".length);
}

/** Renders a single element and returns only that element's markup. */
export async function renderElementHtml(element, options = {}) {
  const { templateV2UiToHtmlFragment } = await loadTemplateV2HtmlRenderer();
  return stripSlideRoot(
    templateV2UiToHtmlFragment({ elements: [element] }, options),
  );
}

const HTML_ENTITIES = {
  "&amp;": "&",
  "&lt;": "<",
  "&gt;": ">",
  "&quot;": '"',
  "&#39;": "'",
};

export function decodeHtmlEntities(value) {
  return value.replace(
    /&(?:amp|lt|gt|quot|#39);/g,
    (entity) => HTML_ENTITIES[entity],
  );
}

/** Reads the Chart.js configuration the renderer embeds on the canvas. */
export function readChartConfig(html) {
  const match = html.match(/data-chart-config="([^"]*)"/);
  assert.ok(match, `no chart config attribute found in: ${html.slice(0, 240)}`);
  return JSON.parse(decodeHtmlEntities(match[1]));
}
