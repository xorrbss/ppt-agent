import assert from "node:assert/strict";
import test from "node:test";

import { resolvePptxRenderTools } from "./pptx-render.mjs";

test("configured missing render tools do not silently fall back to a different binary", async () => {
  const previousSoffice = process.env.SOFFICE_PATH;
  const previousPdfToCairo = process.env.PDFTOCAIRO_PATH;
  process.env.SOFFICE_PATH = "C:\\not-a-real-soffice.exe";
  process.env.PDFTOCAIRO_PATH = "C:\\not-a-real-pdftocairo.exe";
  try {
    const tools = await resolvePptxRenderTools();
    assert.equal(tools.soffice, undefined);
    assert.equal(tools.pdftocairo, undefined);
  } finally {
    if (previousSoffice === undefined) delete process.env.SOFFICE_PATH;
    else process.env.SOFFICE_PATH = previousSoffice;
    if (previousPdfToCairo === undefined) delete process.env.PDFTOCAIRO_PATH;
    else process.env.PDFTOCAIRO_PATH = previousPdfToCairo;
  }
});
