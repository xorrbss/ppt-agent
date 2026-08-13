import assert from "node:assert/strict";
import { mkdtemp, readFile } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { test } from "node:test";
import { fileURLToPath, pathToFileURL } from "node:url";

import { build } from "esbuild";
import {
  EXISTING_90_DECK_MASTER_CONTRACT,
  existing90LogoAsset,
} from "./fixtures/existing-90-contract.ts";

const nextjsRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../..");
let modulePromise;

function loadCompiler() {
  modulePromise ??= (async () => {
    const directory = await mkdtemp(path.join(os.tmpdir(), "ooxml-vnext-master-"));
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

function fixture() {
  return {
    contractVersion: "deckmaster-contract/v1",
    id: "master:test-vnext",
    name: "R&D <Master>",
    slideSizePx: { x: 0, y: 0, width: 1280, height: 720 },
    theme: {
      name: "Test & Navy",
      colors: {
        dk1: "#07101F",
        lt1: "#FFFFFF",
        dk2: "#22344C",
        lt2: "#D9E2EF",
        accent1: "#1463FF",
        accent2: "#19B5A5",
        accent3: "#F4B740",
        accent4: "#E85D75",
        accent5: "#7257D9",
        accent6: "#37A65A",
        hlink: "#1463FF",
        folHlink: "#7257D9",
        bg1: "#F7F9FC",
        bg2: "#EAF0F8",
        tx1: "#0B1830",
        tx2: "#486078",
      },
    },
    tokens: {
      "fit.long-title.max-lines": 3,
      "semantic.warning": "accent4",
      "source.required": true,
    },
    fontManifest: {
      primary: "Malgun Gothic",
      fallbacks: ["Noto Sans CJK KR", "Arial"],
      windowsFallback: "Malgun Gothic",
      themeScripts: {
        latin: "Aptos",
        eastAsian: "Malgun Gothic",
        complexScript: "Arial",
        Hang: "Malgun Gothic",
        Jpan: "Yu Gothic",
        Hans: "Microsoft YaHei",
        Hant: "Microsoft JhengHei",
      },
      files: [{
        style: "regular",
        path: "C:/Windows/Fonts/malgun.ttf",
        version: "6.68",
        sizeBytes: 13_457_164,
        sha256: "0".repeat(64),
      }],
      digest: `sha256:${"a".repeat(64)}`,
    },
    furniture: [
      {
        id: "furniture:logo",
        kind: "logo",
        bounds: { x: 32, y: 28, width: 28, height: 28 },
        visibility: "always",
        ownership: { locked: true, modelWritable: false },
      },
      {
        id: "furniture:top-rail",
        kind: "rect",
        bounds: { x: 0, y: 0, width: 1280, height: 10 },
        fill: "#1463FF",
        ownership: { locked: true, modelWritable: false },
      },
      {
        id: "furniture:brand",
        kind: "text",
        text: "PRESENTON · vNEXT",
        bounds: { x: 76, y: 35, width: 420, height: 28 },
        fill: "#486078",
        ownership: { locked: true, modelWritable: false },
      },
      {
        id: "furniture:footer-rule",
        kind: "line",
        bounds: { x: 76, y: 672, width: 1128, height: 1 },
        stroke: "#C9D5E6",
        ownership: { locked: true, modelWritable: false },
      },
      {
        id: "furniture:page-number",
        kind: "pageNumber",
        text: "1",
        bounds: { x: 1160, y: 680, width: 44, height: 22 },
        fill: "#486078",
        visibility: "exceptCover",
        ownership: { locked: true, modelWritable: false },
      },
    ],
    layouts: [
      {
        id: "layout:cover",
        name: "Cover",
        kind: "cover",
        placeholders: [
          placeholder("placeholder:cover-title", "title", 0, { x: 76, y: 160, width: 1128, height: 190 }, 44, 3),
          placeholder("placeholder:cover-body", "body", 1, { x: 86, y: 390, width: 1090, height: 120 }, 22, 3),
        ],
      },
      {
        id: "layout:section",
        name: "Section",
        kind: "section",
        placeholders: [
          placeholder("placeholder:section-title", "title", 0, { x: 76, y: 200, width: 1128, height: 145 }, 42, 2),
          placeholder("placeholder:section-body", "body", 1, { x: 86, y: 380, width: 1090, height: 130 }, 20, 4),
        ],
      },
      {
        id: "layout:long-title",
        name: "Long & Title",
        kind: "long-title",
        placeholders: [
          placeholder("placeholder:long-title", "title", 0, { x: 76, y: 104, width: 1140, height: 250 }, 35, 3),
          placeholder("placeholder:long-body", "body", 1, { x: 86, y: 404, width: 1090, height: 114 }, 18, 3),
        ],
      },
      {
        id: "layout:standard-title",
        name: "Standard Title",
        kind: "standard",
        placeholders: [
          placeholder("placeholder:standard-title", "title", 0, { x: 76, y: 104, width: 1128, height: 126 }, 38, 2),
          placeholder("placeholder:standard-body", "content", 1, { x: 86, y: 270, width: 1090, height: 240 }, 20, 3),
        ],
      },
    ],
    digest: `sha256:${"b".repeat(64)}`,
  };
}

function placeholder(id, type, index, bounds, fontPt, maxLines) {
  return {
    id,
    type,
    index,
    bounds,
    ownership: { locked: false, modelWritable: true },
    textStyle: {
      fontPt,
      lineHeight: 1.2,
      maxLines,
      marginsPx: { top: 4.8, right: 9.6, bottom: 4.8, left: 9.6 },
    },
  };
}

function clone(value) {
  return structuredClone(value);
}

function withContentTypes(compiled) {
  return new Map([
    ...compiled.parts,
    [
      "[Content_Types].xml",
      Buffer.from(
        `<?xml version="1.0" encoding="UTF-8"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">${compiled.contentTypeOverridesXml.toString("utf8")}</Types>`,
      ),
    ],
  ]);
}

test("compiles deterministic native master, layouts, theme, relationships and content types", async () => {
  const { compileDeckMasterParts } = await loadCompiler();
  const first = compileDeckMasterParts(fixture());
  const second = compileDeckMasterParts(fixture());
  assert.deepEqual([...first.parts.keys()], [
    "ppt/slideMasters/slideMaster1.xml",
    "ppt/slideMasters/_rels/slideMaster1.xml.rels",
    "ppt/theme/theme1.xml",
    "ppt/slideLayouts/slideLayout1.xml",
    "ppt/slideLayouts/_rels/slideLayout1.xml.rels",
    "ppt/slideLayouts/slideLayout2.xml",
    "ppt/slideLayouts/_rels/slideLayout2.xml.rels",
    "ppt/slideLayouts/slideLayout3.xml",
    "ppt/slideLayouts/_rels/slideLayout3.xml.rels",
    "ppt/slideLayouts/slideLayout4.xml",
    "ppt/slideLayouts/_rels/slideLayout4.xml.rels",
  ]);
  assert.deepEqual(
    [...first.parts].map(([name, bytes]) => [name, bytes.toString("hex")]),
    [...second.parts].map(([name, bytes]) => [name, bytes.toString("hex")]),
  );
  assert.deepEqual(first.semanticMetadata, second.semanticMetadata);

  const master = first.parts.get("ppt/slideMasters/slideMaster1.xml").toString("utf8");
  const cover = first.parts.get("ppt/slideLayouts/slideLayout1.xml").toString("utf8");
  const section = first.parts.get("ppt/slideLayouts/slideLayout2.xml").toString("utf8");
  const standard = first.parts.get("ppt/slideLayouts/slideLayout3.xml").toString("utf8");
  const longTitle = first.parts.get("ppt/slideLayouts/slideLayout4.xml").toString("utf8");
  const theme = first.parts.get("ppt/theme/theme1.xml").toString("utf8");
  assert.match(master, /<p:sldMaster\b/);
  assert.match(master, /<p:sldLayoutId id="2147483649" r:id="rId1"\/>/);
  assert.match(master, /name="furniture:top-rail"/);
  assert.match(master, /vnext:role=top-rail/);
  assert.match(master, /name="furniture:logo"/);
  assert.match(master, /name="furniture:page-number"/);
  assert.match(master, /<a:fld\b[^>]*type="slidenum"/);
  assert.match(master, /<avm:file\b[^>]*fileName="malgun\.ttf"/);
  assert.match(master, /<avm:deckMaster\b/);
  assert.doesNotMatch(master, /\{\s*"/); // semantic metadata is typed XML, not opaque JSON.
  assert.match(cover, /type="title"/);
  assert.match(section, /type="secHead"/);
  assert.match(standard, /type="obj"/);
  assert.match(standard, /name="placeholder:standard-title"/);
  assert.match(standard, /<p:ph type="title" idx="0"\/>/);
  assert.match(standard, /<v:placeholder xmlns:v="urn:presenton:ooxml-vnext:semantic-metadata:v1" role="standard-title" idx="0"\/>/);
  assert.match(standard, /<p:ph type="obj" idx="1"\/>/);
  assert.match(longTitle, /type="cust"/);
  assert.match(longTitle, /name="Long &amp; Title"/);
  assert.match(theme, /<a:latin typeface="Aptos"\/>/);
  assert.match(theme, /<a:ea typeface="Malgun Gothic"\/>/);
  assert.match(theme, /<a:cs typeface="Arial"\/>/);
  assert.match(theme, /<a:font script="Jpan" typeface="Yu Gothic"\/>/);
  assert.deepEqual(first.contentTypeOverrides.map((item) => item.partName), [
    "/ppt/slideMasters/slideMaster1.xml",
    "/ppt/slideLayouts/slideLayout1.xml",
    "/ppt/slideLayouts/slideLayout2.xml",
    "/ppt/slideLayouts/slideLayout3.xml",
    "/ppt/slideLayouts/slideLayout4.xml",
    "/ppt/theme/theme1.xml",
  ]);
});

test("reads back stable semantic roles, placeholder indexes and relationship closure", async () => {
  const { createDeckMasterPartCompiler } = await loadCompiler();
  const compiler = createDeckMasterPartCompiler();
  const contract = fixture();
  const compiled = compiler.compile(contract);
  const receipt = compiler.assertReadBack(contract, withContentTypes(compiled));
  assert.equal(receipt.contentTypesVerified, true);
  assert.equal(receipt.verifiedPartNames.length, 11);
  assert.deepEqual(receipt.semanticMetadata.layouts.map(({ kind }) => kind), [
    "cover",
    "section",
    "standard",
    "long-title",
  ]);
  const standard = receipt.semanticMetadata.layouts.find(({ kind }) => kind === "standard");
  assert.deepEqual(
    standard.placeholders.map(({ role, type, index, shapeId }) => ({ role, type, index, shapeId })),
    [
      { role: "standard-title", type: "title", index: 0, shapeId: 2 },
      { role: "standard-body", type: "content", index: 1, shapeId: 3 },
    ],
  );
  assert.deepEqual(receipt.semanticMetadata.fonts, {
    primary: "Malgun Gothic",
    latin: "Aptos",
    eastAsian: "Malgun Gothic",
    complexScript: "Arial",
  });
  assert.equal(receipt.semanticMetadata.fontFiles.length, 1);
  assert.equal(receipt.semanticMetadata.fontFiles[0].fileName, "malgun.ttf");
  assert.equal(receipt.semanticMetadata.furniture.filter(({ kind }) => kind === "logo").length, 1);
  assert.equal(receipt.semanticMetadata.furniture.filter(({ kind }) => kind === "pageNumber").length, 1);
  assert.equal(receipt.semanticMetadata.tokens["fit.long-title.max-lines"], 3);
  assert.match(receipt.semanticMetadata.contractDigest, /^sha256:[0-9a-f]{64}$/);
  assert.match(receipt.semanticMetadata.semanticDigest, /^sha256:[0-9a-f]{64}$/);
});

test("embeds a digest-bound PNG logo relationship and detects missing or changed bytes", async () => {
  const { compileDeckMasterParts, assertDeckMasterReadBack } = await loadCompiler();
  const logo = await readFile(new URL("../../public/Logo.png", import.meta.url));
  const asset = existing90LogoAsset(logo);
  assert.throws(
    () => compileDeckMasterParts(EXISTING_90_DECK_MASTER_CONTRACT),
    /require verified WorkerAsset bytes/,
  );
  const compiled = compileDeckMasterParts(EXISTING_90_DECK_MASTER_CONTRACT, [asset]);
  const mediaPart = [...compiled.parts.keys()].find((name) => name.startsWith("ppt/media/"));
  assert.equal(mediaPart, "ppt/media/logo-689976b88fc161e4.png");
  assert.deepEqual(compiled.parts.get(mediaPart), logo);
  assert.match(
    compiled.parts.get("ppt/slideMasters/slideMaster1.xml").toString("utf8"),
    /<p:pic>.*<a:blip r:embed="rIdLogo2"/,
  );
  assert.match(
    compiled.parts.get("ppt/slideMasters/_rels/slideMaster1.xml.rels").toString("utf8"),
    /Type="[^"]+\/image" Target="\.\.\/media\/logo-689976b88fc161e4\.png"/,
  );
  const receipt = assertDeckMasterReadBack(
    EXISTING_90_DECK_MASTER_CONTRACT,
    withContentTypes(compiled),
  );
  assert.ok(receipt.verifiedPartNames.includes(mediaPart));
  const tampered = new Map(compiled.parts);
  tampered.set(mediaPart, Buffer.concat([logo, Buffer.from([0])]));
  assert.throws(
    () => assertDeckMasterReadBack(EXISTING_90_DECK_MASTER_CONTRACT, tampered),
    /do not match semantic metadata/,
  );
});

test("read-back reflects an updated contract and rejects stale, deleted or tampered parts", async () => {
  const { compileDeckMasterParts, assertDeckMasterReadBack, readDeckMasterParts } = await loadCompiler();
  const original = fixture();
  const updated = clone(original);
  updated.name = "Updated Master";
  updated.theme.colors.accent1 = "#0057ff";
  updated.tokens["semantic.warning"] = "accent3";
  const compiled = compileDeckMasterParts(updated);
  const receipt = assertDeckMasterReadBack(updated, compiled.parts);
  assert.equal(receipt.contentTypesVerified, false);
  assert.equal(receipt.semanticMetadata.contractName, "Updated Master");
  assert.equal(receipt.semanticMetadata.themeColors.accent1, "#0057FF");
  assert.throws(() => assertDeckMasterReadBack(original, compiled.parts), /does not match the requested contract/);

  const deleted = new Map(compiled.parts);
  deleted.delete("ppt/slideLayouts/slideLayout4.xml");
  assert.throws(() => readDeckMasterParts(deleted), /missing ppt\/slideLayouts\/slideLayout4\.xml/);

  const tampered = new Map(compiled.parts);
  tampered.set(
    "ppt/slideLayouts/slideLayout3.xml",
    Buffer.from(tampered.get("ppt/slideLayouts/slideLayout3.xml").toString("utf8").replace('type="title" idx="0"', 'type="title" idx="77"')),
  );
  assert.throws(() => readDeckMasterParts(tampered), /does not match type\/index mapping/);
});

test("validation fails closed for unstable placeholder maps and writable furniture", async () => {
  const { compileDeckMasterParts } = await loadCompiler();
  const duplicateIndex = fixture();
  duplicateIndex.layouts[0].placeholders[1].index = 0;
  assert.throws(() => compileDeckMasterParts(duplicateIndex), /index must be unique within its layout/);

  const writableFurniture = fixture();
  writableFurniture.furniture[0].ownership.modelWritable = true;
  assert.throws(() => compileDeckMasterParts(writableFurniture), /master furniture locked and not model-writable/);

  const missingLayout = fixture();
  missingLayout.layouts = missingLayout.layouts.filter(({ kind }) => kind !== "section");
  assert.throws(() => compileDeckMasterParts(missingLayout), /requires a section layout/);
});
