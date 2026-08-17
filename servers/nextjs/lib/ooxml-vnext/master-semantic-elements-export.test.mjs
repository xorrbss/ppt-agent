import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { mkdtemp } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { test } from "node:test";
import { fileURLToPath, pathToFileURL } from "node:url";

import { build } from "esbuild";

import { DEFAULT_MASTER_CONTRACT } from "../authored-vnext/master/fixtures.ts";

const nextjsRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../..");
let modulePromise;

function loadCompiler() {
  modulePromise ??= (async () => {
    const directory = await mkdtemp(path.join(os.tmpdir(), "master-semantic-export-"));
    const outfile = path.join(directory, "master-compiler.mjs");
    await build({
      absWorkingDir: nextjsRoot,
      bundle: true,
      entryPoints: ["lib/ooxml-vnext/master-compiler.ts"],
      format: "esm",
      outfile,
      platform: "node",
      tsconfig: path.join(nextjsRoot, "tsconfig.json"),
    });
    return import(pathToFileURL(outfile).href);
  })();
  return modulePromise;
}

test("exports semantic SVG crop metadata and a native slide-number field", async () => {
  const { compileAuthoredDeckMasterParts } = await loadCompiler();
  const svg = Buffer.from(
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 40 20"><rect width="40" height="20" fill="#1463ff"/></svg>',
    "utf8",
  );
  const sha256 = createHash("sha256").update(svg).digest("hex");
  const contract = structuredClone(DEFAULT_MASTER_CONTRACT);
  contract.semanticElements = [
    {
      id: "element:ibiz-mark",
      type: "image",
      scope: { kind: "common" },
      bounds: { x: 84, y: 48, width: 180, height: 90 },
      rotation: 0,
      zIndex: 40,
      visibility: "always",
      editableByUser: true,
      editableByAI: false,
      locked: false,
      style: { opacity: 0.9 },
      content: {
        assetId: "asset:ibiz-mark",
        digest: `sha256:${sha256}`,
        mediaType: "image/svg+xml",
        altText: "IBIZ mark",
        fit: "cover",
        crop: { x: 0.1, y: 0.2, width: 0.7, height: 0.6 },
        role: "brand-logo",
      },
    },
    {
      id: "element:slide-number",
      type: "pageNumber",
      scope: { kind: "common" },
      bounds: { x: 1160, y: 665, width: 72, height: 24 },
      rotation: 0,
      zIndex: 50,
      visibility: "exceptCover",
      editableByUser: true,
      editableByAI: false,
      locked: false,
      style: { fontFamily: "Noto Sans KR", fontSize: 10, color: "#475569" },
      // A stale preview literal must never replace the native PowerPoint field.
      content: { text: "999", role: "slide-number", placeholderIndex: 90 },
    },
    {
      id: "element:rfp-number",
      type: "text",
      scope: { kind: "common" },
      bounds: { x: 860, y: 42, width: 300, height: 36 },
      rotation: 0,
      zIndex: 51,
      visibility: "always",
      editableByUser: true,
      editableByAI: true,
      locked: false,
      style: { fontFamily: "Noto Sans KR", fontSize: 12, color: "#475569" },
      content: {
        text: "RFP-2026-014",
        role: "ai-text-zone",
        aiBinding: {
          instruction: "Use the RFP number supported by this document.",
          exampleText: "RFP-0000-000",
          emptyBehavior: "blank",
          maxChars: 32,
        },
      },
    },
  ];

  const compiled = compileAuthoredDeckMasterParts(contract, [
    {
      assetId: "asset:ibiz-mark",
      mediaType: "image/svg+xml",
      sha256,
      bytesBase64: svg.toString("base64"),
    },
  ]);

  const masterPart = "ppt/slideMasters/slideMaster1.xml";
  const masterRelsPart = "ppt/slideMasters/_rels/slideMaster1.xml.rels";
  const master = compiled.parts.get(masterPart).toString("utf8");
  const relationships = compiled.parts.get(masterRelsPart).toString("utf8");
  const standardLayoutPart = compiled.semanticMetadata.layouts.find(
    (layout) => layout.id === contract.layouts.standard.id,
  ).partName;
  const standardLayout = compiled.parts.get(standardLayoutPart).toString("utf8");
  const imageMapping = compiled.semanticMetadata.semanticElements.find(
    (element) => element.id === "element:ibiz-mark",
  );
  const pageNumberMapping = compiled.semanticMetadata.semanticElements.find(
    (element) => element.id === "element:slide-number",
  );

  assert.deepEqual(imageMapping.crop, { x: 0.1, y: 0.2, width: 0.7, height: 0.6 });
  assert.equal(imageMapping.fit, "cover");
  assert.equal(imageMapping.assetId, "asset:ibiz-mark");
  assert.equal(pageNumberMapping.type, "pageNumber");
  assert.equal(pageNumberMapping.visibility, "exceptCover");

  assert.match(master, /name="element:ibiz-mark"/);
  assert.match(master, /name="element:rfp-number"/);
  assert.match(master, /<a:t>RFP-2026-014<\/a:t>/);
  assert.match(master, /<a:srcRect l="10000" t="20000" r="20000" b="20000"\/>/);
  assert.match(standardLayout, /<p:ph type="sldNum" idx="90"\/>/);
  assert.match(standardLayout, /<a:fld\b[^>]*type="slidenum"/);
  assert.doesNotMatch(standardLayout, />999<\/a:t>/);

  assert.match(relationships, /Id="rIdSemantic\d+"[^>]+Target="\.\.\/media\/.+\.svg"/);
  assert.ok(imageMapping.assetPartName);
  assert.deepEqual(compiled.parts.get(imageMapping.assetPartName), svg);
  assert.ok(
    compiled.contentTypeOverrides.some(
      (entry) => entry.partName === `/${imageMapping.assetPartName}` && entry.contentType === "image/svg+xml",
    ),
  );
});

test("exports shared layout logos through one digest-pinned media part", async () => {
  const { compileAuthoredDeckMasterParts } = await loadCompiler();
  const svg = Buffer.from(
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 60 24"><rect width="60" height="24" fill="#0f4c81"/></svg>',
    "utf8",
  );
  const sha256 = createHash("sha256").update(svg).digest("hex");
  const assetId = "asset:shared-company-logo";
  const sharedGroupId = "company-logo";
  const contract = structuredClone(DEFAULT_MASTER_CONTRACT);
  const sharedContent = {
    assetId,
    digest: `sha256:${sha256}`,
    mediaType: "image/svg+xml",
    altText: "Company logo",
    fit: "contain",
    role: "brand-logo",
    sharedGroupId,
  };
  contract.semanticElements = [
    {
      id: "element:shared-logo-standard",
      type: "image",
      scope: { kind: "layout", layoutId: contract.layouts.standard.id },
      bounds: { x: 980, y: 596, width: 180, height: 72 },
      rotation: 0,
      zIndex: 40,
      visibility: "layoutOnly",
      editableByUser: true,
      editableByAI: false,
      locked: false,
      style: { opacity: 0.42 },
      content: { ...sharedContent, sharedOverride: false },
    },
    {
      id: "element:shared-logo-long-title",
      type: "image",
      scope: { kind: "layout", layoutId: contract.layouts.longTitle.id },
      bounds: { x: 100, y: 596, width: 180, height: 72 },
      rotation: 0,
      zIndex: 41,
      visibility: "layoutOnly",
      editableByUser: true,
      editableByAI: false,
      locked: false,
      style: { opacity: 0.42 },
      content: { ...sharedContent, sharedOverride: true },
    },
  ];

  const compiled = compileAuthoredDeckMasterParts(contract, [
    {
      assetId,
      mediaType: "image/svg+xml",
      sha256,
      bytesBase64: svg.toString("base64"),
    },
  ]);
  const mappings = compiled.semanticMetadata.semanticElements.filter(
    (element) => element.sharedGroupId === sharedGroupId,
  );

  assert.equal(mappings.length, 2);
  assert.equal(mappings[0].assetId, assetId);
  assert.equal(mappings[1].assetId, assetId);
  assert.equal(mappings[0].assetPartName, mappings[1].assetPartName);
  assert.equal(mappings[0].sharedOverride, false);
  assert.equal(mappings[1].sharedOverride, true);
  assert.ok(mappings[0].assetPartName);
  assert.deepEqual(compiled.parts.get(mappings[0].assetPartName), svg);

  const matchingMediaParts = [...compiled.parts.entries()].filter(
    ([partName, bytes]) => partName.startsWith("ppt/media/") && bytes.equals(svg),
  );
  assert.equal(matchingMediaParts.length, 1);

  for (const mapping of mappings) {
    const layoutPart = compiled.semanticMetadata.layouts.find(
      (layout) => layout.id === mapping.layoutId,
    ).partName;
    const layoutXml = compiled.parts.get(layoutPart).toString("utf8");
    const relsPart = `${path.posix.dirname(layoutPart)}/_rels/${path.posix.basename(layoutPart)}.rels`;
    const relationships = compiled.parts.get(relsPart).toString("utf8");
    const mediaFile = path.posix.basename(mapping.assetPartName);

    assert.match(layoutXml, new RegExp(`name="${mapping.id}"`));
    assert.match(layoutXml, /<a:alphaModFix amt="42000"\/>/);
    assert.ok(relationships.includes(`Target="../media/${mediaFile}"`));
  }
});
