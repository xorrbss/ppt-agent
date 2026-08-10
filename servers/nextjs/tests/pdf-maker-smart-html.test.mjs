import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const pdfMakerSourceUrl = new URL(
  "../app/(export)/pdf-maker/PdfMakerPage.tsx",
  import.meta.url,
);

test("pdf maker mounts Smart slide HTML directly without an iframe", async () => {
  const source = await readFile(pdfMakerSourceUrl, "utf8");

  assert.match(source, /const SmartHtmlPdfSlide/);
  assert.match(source, /dangerouslySetInnerHTML=\{\{ __html: html \}\}/);
  assert.match(source, /useSmartChartInjection\(\{/);
  assert.match(source, /data-screenshot="true"/);
  assert.match(source, /data-screenshot-include-children="true"/);
  assert.doesNotMatch(source, /<iframe\b/);
});

test("pdf maker preserves the DOM depth required by the PPTX extractor", async () => {
  const source = await readFile(pdfMakerSourceUrl, "utf8");

  assert.match(
    source,
    /<div className="slides-export-stack font-inter">[\s\S]*slides\.map/,
  );
  assert.match(source, /className="main-slide [^"]*"/);
  assert.match(source, /className="slide-export-inner [^"]*"/);
});
