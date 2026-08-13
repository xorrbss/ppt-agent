import assert from "node:assert/strict";
import { existsSync } from "node:fs";
import { mkdtemp, readFile, rm } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import test from "node:test";

import { readPptxArchive, writePptxArchive } from "../authored-hybrid/pptx-archive.ts";
import { createOoxmlMasterCompilerPort } from "./adapter.ts";
import { buildExisting90Fixture } from "./fixtures/build-existing-90.mjs";
import { DEMO_DECK_MASTER_CONTRACT } from "./fixtures/demo-contract.ts";

function next(port, document) {
  return port.readBack(document).precondition;
}

function materialize(port, document, index, layoutId = "layout:standard") {
  const valuesByLayout = {
    "layout:cover": {
      "cover-title": `표지 제목 ${index}`,
      "cover-body": `표지 본문 ${index}`,
    },
    "layout:section": {
      "section-title": `섹션 제목 ${index}`,
      "section-body": `섹션 본문 ${index}`,
    },
    "layout:standard": {
      "standard-title": `제목 ${index}`,
      "standard-body": `본문 ${index}`,
    },
    "layout:long-title": {
      "long-title": `검증 가능한 긴 제목 ${index}`,
      "long-body": `긴 제목 본문 ${index}`,
    },
  };
  return port.materializeSlide(
    document,
    {
      slideId: `slide:${index}`,
      layoutId,
      values: valuesByLayout[layoutId],
    },
    next(port, document),
  );
}

test("generated master/layout CRUD, semantic read-back, and targeted slice edit", async () => {
  const port = createOoxmlMasterCompilerPort();
  const created = port.createGenerated(DEMO_DECK_MASTER_CONTRACT);
  assert.equal(created.masterCount, 1);
  assert.equal(created.layoutCount, 4);
  let readBack = port.readBack(created.document);
  assert.deepEqual(
    readBack.master.semanticMetadata.layouts.map(({ id }) => id),
    ["layout:cover", "layout:section", "layout:standard", "layout:long-title"],
  );

  const deleted = port.deleteMaster(created.document, readBack.precondition);
  assert.equal(deleted.package.masterCount, 0);
  assert.equal(deleted.package.layoutCount, 0);
  const compiled = port.compileMaster(
    created.document,
    DEMO_DECK_MASTER_CONTRACT,
    next(port, created.document),
  );
  assert.equal(compiled.package.masterCount, 1);
  assert.equal(compiled.package.layoutCount, 4);

  const expanded = structuredClone(DEMO_DECK_MASTER_CONTRACT);
  const appendix = structuredClone(expanded.layouts.find(({ kind }) => kind === "standard"));
  appendix.id = "layout:appendix";
  appendix.name = "Appendix";
  for (const placeholder of appendix.placeholders) {
    placeholder.id = placeholder.id.replace(
      "placeholder:standard-",
      "placeholder:appendix-",
    );
  }
  expanded.layouts.push(appendix);
  assert.equal(
    port.compileMaster(created.document, expanded, next(port, created.document)).package.layoutCount,
    5,
  );
  assert.equal(
    port.compileMaster(
      created.document,
      DEMO_DECK_MASTER_CONTRACT,
      next(port, created.document),
    ).package.layoutCount,
    4,
  );

  materialize(port, created.document, 1);
  readBack = port.readBack(created.document);
  const packageBytesBeforeEdit = await (async () => {
    const temp = await mkdtemp(path.join(os.tmpdir(), "ooxml-vnext-list-"));
    const output = path.join(temp, "list-check.pptx");
    await port.saveBuffered(created.document, output, readBack.precondition, null);
    const bytes = await readFile(output);
    await rm(temp, { recursive: true, force: true });
    return bytes;
  })();
  const presentationXml = readPptxArchive(packageBytesBeforeEdit)
    .get("ppt/presentation.xml")
    .toString("utf8");
  assert.equal(
    readPptxArchive(packageBytesBeforeEdit).has("ppt/slideLayouts/slideLayout5.xml"),
    false,
  );
  assert.equal([...presentationXml.matchAll(/<p:sldIdLst\b/g)].length, 1);
  assert.equal([...presentationXml.matchAll(/<p:sldMasterIdLst\b/g)].length, 1);
  const edited = port.targetedTextOperation(
    created.document,
    {
      partName: "ppt/slides/slide1.xml",
      selector: { kind: "placeholder", idx: 0, role: "standard-title" },
      expected: { text: "제목 1" },
      replacement: "승인된 제목 1",
    },
    readBack.precondition,
  );
  assert.deepEqual(edited.changedParts, ["ppt/slides/slide1.xml"]);
  assert.equal(edited.textPatch.outsideSliceDiffBytes, 0);
  assert.equal(edited.package.partClassifications.PATCH_SLICE, 0);
  assert.equal(edited.package.partClassifications.GENERATED > 0, true);
  assert.equal(port.readBack(created.document).preservation.silentLossCount, 0);

  assert.throws(
    () =>
      port.targetedTextOperation(
        created.document,
        {
          partName: "ppt/slides/slide1.xml",
          selector: { kind: "placeholder", idx: 0, role: "standard-title" },
          expected: { text: "승인된 제목 1" },
          replacement: " 줄바꿈 없는 위험 여백 ",
        },
        next(port, created.document),
      ),
    /rejected/,
  );
});

test("synthetic 90-slide generated save is buffered, reopened, and stable on no-op import", async (t) => {
  const temp = await mkdtemp(path.join(os.tmpdir(), "ooxml-vnext-90-"));
  t.after(() => rm(temp, { recursive: true, force: true }));
  const generatedPath = path.join(temp, "generated-90.pptx");
  const noopPath = path.join(temp, "generated-90-noop.pptx");
  const port = createOoxmlMasterCompilerPort();
  const created = port.createGenerated(DEMO_DECK_MASTER_CONTRACT);
  const layoutIds = ["layout:cover", "layout:section", "layout:standard", "layout:long-title"];
  for (let index = 1; index <= 90; index += 1) {
    materialize(
      port,
      created.document,
      index,
      layoutIds[(index - 1) % layoutIds.length],
    );
  }
  const saved = await port.saveBuffered(
    created.document,
    generatedPath,
    next(port, created.document),
    null,
  );
  assert.equal(saved.readBack.package.slideCount, 90);
  assert.equal(saved.readBack.preservation.silentLossCount, 0);
  const generatedBytes = await readFile(generatedPath);
  const imported = port.importPackage(generatedBytes);
  assert.equal(imported.originalPackage.archiveSha256.length, 64);
  assert.equal(imported.originalPackage.parts.length, imported.partCount);
  assert.equal(imported.originalPreservationManifest.parts.length, imported.partCount);
  assert.equal(imported.originalPreservationManifest.classification, "PRESERVE_EXACT");
  const detachedOriginal = port.readOriginalPackage(imported.document);
  assert.deepEqual(Buffer.from(detachedOriginal), generatedBytes);
  detachedOriginal[0] ^= 0xff;
  assert.deepEqual(Buffer.from(port.readOriginalPackage(imported.document)), generatedBytes);
  const importedBefore = port.readBack(imported.document);
  assert.equal(importedBefore.preservation.schemaVersion, 1);
  assert.equal(importedBefore.preservation.parts.length, imported.partCount);
  assert.equal(
    importedBefore.preservation.parts.every(({ status }) => status === "PRESERVED_EXACT"),
    true,
  );
  const noop = await port.saveBuffered(
    imported.document,
    noopPath,
    importedBefore.precondition,
    null,
  );
  assert.equal(noop.readBack.package.packageDigest, importedBefore.package.packageDigest);
  assert.equal(noop.readBack.package.relationshipDigest, importedBefore.package.relationshipDigest);
  assert.equal(noop.readBack.package.closureDigest, importedBefore.package.closureDigest);
  assert.equal(noop.readBack.preservation.silentLossCount, 0);
  assert.deepEqual(await readFile(noopPath), generatedBytes);
  const beforeEntries = readPptxArchive(generatedBytes);
  const afterEntries = readPptxArchive(await readFile(noopPath));
  assert.deepEqual([...afterEntries.keys()], [...beforeEntries.keys()]);
  for (const [name, bytes] of beforeEntries) assert.deepEqual(afterEntries.get(name), bytes, name);
});

test("existing 90-slide corpus no-op preserves every uncompressed package part", async (t) => {
  const corpusPath = path.resolve(
    process.cwd(),
    "../../docs/experiments/authored-vnext/03-ooxml-roundtrip/artifacts/existing-90-noop.pptx",
  );
  if (!existsSync(corpusPath)) {
    t.skip("03 existing-90 local corpus is unavailable");
    return;
  }
  const temp = await mkdtemp(path.join(os.tmpdir(), "ooxml-vnext-existing-90-"));
  t.after(() => rm(temp, { recursive: true, force: true }));
  const output = path.join(temp, "existing-90-product-noop.pptx");
  const source = await readFile(corpusPath);
  const port = createOoxmlMasterCompilerPort();
  const imported = port.importPackage(source);
  assert.equal(imported.slideCount, 90);
  const before = port.readBack(imported.document);
  const saved = await port.saveBuffered(
    imported.document,
    output,
    before.precondition,
    null,
  );
  assert.equal(saved.readBack.preservation.silentLossCount, 0);
  assert.equal(saved.readBack.package.packageDigest, before.package.packageDigest);
  assert.equal(saved.readBack.package.relationshipDigest, before.package.relationshipDigest);
  assert.equal(saved.readBack.package.closureDigest, before.package.closureDigest);
  assert.deepEqual(await readFile(output), source);
  const sourceEntries = readPptxArchive(source);
  const outputEntries = readPptxArchive(await readFile(output));
  const sourceParts = [...sourceEntries.keys()].filter((name) => !name.endsWith("/"));
  assert.deepEqual(
    [...outputEntries.keys()].filter((name) => !name.endsWith("/")),
    sourceParts,
  );
  for (const name of sourceParts) {
    assert.deepEqual(outputEntries.get(name), sourceEntries.get(name), name);
  }
});

test("checked-in existing-style 90 recipe is reproducible and preserves opaque parts", async (t) => {
  const temp = await mkdtemp(path.join(os.tmpdir(), "ooxml-vnext-existing-recipe-"));
  t.after(() => rm(temp, { recursive: true, force: true }));
  const sourcePath = path.join(temp, "existing-style-90.pptx");
  const outputPath = path.join(temp, "existing-style-90-noop.pptx");
  const recipe = JSON.parse(
    await readFile(new URL("./fixtures/existing-90.recipe.json", import.meta.url), "utf8"),
  );
  const built = await buildExisting90Fixture(sourcePath);
  assert.equal(built.archiveSha256, recipe.expectedArchiveSha256);
  assert.equal(built.packageDigest, recipe.expectedPackageDigest);
  assert.equal(built.slideCount, 90);
  assert.equal(built.quarantined, false);
  assert.equal(built.warnings.length, 1);

  const source = await readFile(sourcePath);
  const port = createOoxmlMasterCompilerPort();
  const imported = port.importPackage(source);
  const saved = await port.saveBuffered(
    imported.document,
    outputPath,
    port.readBack(imported.document).precondition,
    null,
  );
  assert.equal(saved.readBack.preservation.silentLossCount, 0);
  assert.deepEqual(await readFile(outputPath), source);
  const reopened = readPptxArchive(await readFile(outputPath));
  assert.equal(built.layoutCount, 4);
  assert.deepEqual(built.layoutIds, recipe.layoutCycle);
  assert.equal(reopened.has(recipe.embeddedAssets[0].partName), true);
  assert.deepEqual(
    reopened.get(recipe.embeddedAssets[0].partName),
    await readFile(new URL("../../public/Logo.png", import.meta.url)),
  );
  assert.match(reopened.get("[Content_Types].xml").toString("utf8"), /ContentType="image\/png"/);
  assert.match(
    reopened.get("ppt/slideMasters/_rels/slideMaster1.xml.rels").toString("utf8"),
    /Type="[^"]+\/image"/,
  );
  const layoutTargets = [...Array(90)].map((_, index) =>
    reopened.get(`ppt/slides/_rels/slide${index + 1}.xml.rels`).toString("utf8")
      .match(/Target="\.\.\/slideLayouts\/(slideLayout\d+\.xml)"/)[1],
  );
  assert.deepEqual(new Set(layoutTargets), new Set([
    "slideLayout1.xml", "slideLayout2.xml", "slideLayout3.xml", "slideLayout4.xml",
  ]));
  const corpusText = [...Array(90)].map((_, index) =>
    reopened.get(`ppt/slides/slide${index + 1}.xml`).toString("utf8"),
  ).join("");
  for (const sample of ["기업 AI 전환", "日本語", "简体中文", "👩🏽‍💻", "é", "𠀀"]) {
    assert.equal(corpusText.includes(sample), true, sample);
  }
  assert.equal(corpusText.includes("세 번째 줄의 emoji"), true);
  assert.equal(reopened.get("customXml/item1.xml").toString("utf8").includes("opaque-custom-xml"), true);
  assert.deepEqual(
    reopened.get("ppt/embeddings/oleObject1.bin"),
    Buffer.from("PRESENTON-OPAQUE-OLE-FIXTURE\0", "utf8"),
  );
});

test("real corporate master import inventories and captures an exact opaque closure", async (t) => {
  const corpusPath = path.resolve(
    process.cwd(),
    "../../docs/artifacts/ppt-master-catalog/ppt-master-ai-technology-proposal.pptx",
  );
  assert.equal(existsSync(corpusPath), true, `required corporate fixture missing: ${corpusPath}`);
  const source = await readFile(corpusPath);
  const port = createOoxmlMasterCompilerPort();
  const imported = port.importPackage(source);
  const inventory = port.inventoryMasters(imported.document);
  assert.equal(inventory.masters.length, imported.masterCount);
  assert.equal(inventory.layouts.length, imported.layoutCount);
  assert.equal(inventory.issueCodes.length, 0);
  const selected = inventory.masters[0];
  assert.ok(selected.presentationReferences.length > 0);
  assert.ok(selected.layouts.length > 0);
  const bundle = port.captureCorporateMaster(imported.document, {
    partName: selected.partName,
  });
  assert.equal(bundle.masterPartName, selected.partName);
  assert.deepEqual(bundle.layoutPartNames, selected.layouts.map(({ layoutPartName }) => layoutPartName));
  assert.equal(bundle.publicationPolicy.mutationClassification, "PRESERVE_EXACT");
  const entries = readPptxArchive(source);
  for (const member of bundle.members) {
    assert.deepEqual(Buffer.from(member.payloadBase64, "base64"), entries.get(member.partName));
  }
  assert.deepEqual(Buffer.from(port.readOriginalPackage(imported.document)), source);

  const generated = port.createGenerated(DEMO_DECK_MASTER_CONTRACT);
  port.deleteMaster(generated.document, next(port, generated.document));
  const attached = port.importCorporateMaster(
    generated.document,
    bundle,
    next(port, generated.document),
  );
  assert.equal(attached.operation, "IMPORT_CORPORATE_MASTER");
  assert.equal(attached.package.masterCount, 1);
  assert.equal(attached.package.layoutCount, bundle.layoutPartNames.length);
  const attachedInventory = port.inventoryMasters(generated.document);
  assert.equal(attachedInventory.masters[0].partName, bundle.masterPartName);
  assert.deepEqual(
    attachedInventory.layouts.map(({ partName }) => partName),
    inventory.layouts.map(({ partName }) => partName),
  );
  const temp = await mkdtemp(path.join(os.tmpdir(), "ooxml-corporate-import-"));
  t.after(() => rm(temp, { recursive: true, force: true }));
  const output = path.join(temp, "corporate-master-import.pptx");
  const saved = await port.saveBuffered(
    generated.document,
    output,
    next(port, generated.document),
    null,
  );
  assert.equal(saved.readBack.preservation.silentLossCount, 0);
  const reopened = port.importPackage(await readFile(output));
  assert.equal(port.inventoryMasters(reopened.document).layouts.length, bundle.layoutPartNames.length);
  const deleted = port.deleteMaster(generated.document, next(port, generated.document));
  assert.equal(deleted.package.masterCount, 0);
  assert.equal(deleted.package.layoutCount, 0);
  const reattached = port.importCorporateMaster(
    generated.document,
    bundle,
    next(port, generated.document),
  );
  assert.equal(reattached.package.layoutCount, bundle.layoutPartNames.length);
});

test("imported unsupported/external parts remain exact and macros are quarantined", async (t) => {
  const temp = await mkdtemp(path.join(os.tmpdir(), "ooxml-vnext-features-"));
  t.after(() => rm(temp, { recursive: true, force: true }));
  const basePath = path.join(temp, "base.pptx");
  const port = createOoxmlMasterCompilerPort();
  const created = port.createGenerated(DEMO_DECK_MASTER_CONTRACT);
  materialize(port, created.document, 1);
  await port.saveBuffered(created.document, basePath, next(port, created.document), null);
  const entries = readPptxArchive(await readFile(basePath));
  entries.set(
    "[Content_Types].xml",
    Buffer.from(
      entries
        .get("[Content_Types].xml")
        .toString("utf8")
        .replace(
          "</Types>",
          '<Default Extension="bin" ContentType="application/octet-stream"/></Types>',
        ),
    ),
  );
  const sentinelParts = new Map([
    ["ppt/diagrams/data1.xml", Buffer.from("<dgm:dataModel xmlns:dgm=\"urn:test\"/>")],
    ["ppt/embeddings/oleObject1.bin", Buffer.from([1, 2, 3, 4, 5, 6])],
    ["ppt/comments/comment1.xml", Buffer.from("<p188:cmLst xmlns:p188=\"urn:test:modern-comments\"/>")],
    ["customXml/item1.xml", Buffer.from("<custom><opaque/></custom>")],
  ]);
  for (const [name, bytes] of sentinelParts) entries.set(name, bytes);
  entries.set(
    "ppt/slides/slide1.xml",
    Buffer.from(
      entries
        .get("ppt/slides/slide1.xml")
        .toString("utf8")
        .replace(
          "</p:sld>",
          '<p:timing><p:tnLst><p:par><p:cTn id="1" dur="indefinite"/></p:par></p:tnLst></p:timing></p:sld>',
        ),
    ),
  );
  const slideRelsName = "ppt/slides/_rels/slide1.xml.rels";
  const slideRels = entries.get(slideRelsName).toString("utf8").replace(
    "</Relationships>",
    '<Relationship Id="rId95" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/diagramData" Target="../diagrams/data1.xml"/><Relationship Id="rId96" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/oleObject" Target="../embeddings/oleObject1.bin"/><Relationship Id="rId97" Type="http://schemas.microsoft.com/office/2018/10/relationships/comments" Target="../comments/comment1.xml"/><Relationship Id="rId98" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/customXml" Target="../../customXml/item1.xml"/><Relationship Id="rId99" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/hyperlink" Target="https://example.invalid/not-fetched" TargetMode="External"/></Relationships>',
  );
  entries.set(slideRelsName, Buffer.from(slideRels));
  const featureBytes = writePptxArchive(entries);
  const imported = port.importPackage(featureBytes);
  assert.equal(imported.externalRelationshipCount, 1);
  assert.equal(imported.partClassifications.PRESERVE_EXACT, imported.partCount);
  const patched = port.targetedTextOperation(
    imported.document,
    {
      partName: "ppt/slides/slide1.xml",
      selector: { kind: "placeholder", idx: 0, role: "standard-title" },
      expected: { text: "제목 1" },
      replacement: "수정 제목 1",
    },
    next(port, imported.document),
  );
  assert.equal(patched.package.partClassifications.PATCH_SLICE, 1);
  assert.deepEqual(patched.changedParts, ["ppt/slides/slide1.xml"]);
  assert.equal(patched.textPatch.outsideSliceDiffBytes, 0);
  assert.throws(
    () =>
      port.targetedTextOperation(
        imported.document,
        {
          partName: "ppt/slides/slide1.xml",
          selector: { kind: "placeholder", idx: 0, role: "standard-title" },
          expected: { text: "수정 제목 1" },
          replacement: "두 번째 수정",
        },
        next(port, imported.document),
      ),
    /only one approved PATCH_SLICE/,
  );
  const patchManifest = port.readBack(imported.document).preservation;
  assert.equal(
    patchManifest.parts.find(({ name }) => name === "ppt/slides/slide1.xml")?.status,
    "PATCHED_SLICE",
  );
  assert.equal(patchManifest.unexpectedAddedParts.length, 0);
  const output = path.join(temp, "features-targeted.pptx");
  const saved = await port.saveBuffered(imported.document, output, next(port, imported.document), null);
  assert.equal(saved.readBack.preservation.silentLossCount, 0);
  const outputEntries = readPptxArchive(await readFile(output));
  for (const [name, bytes] of sentinelParts) assert.deepEqual(outputEntries.get(name), bytes);
  assert.match(outputEntries.get("ppt/slides/slide1.xml").toString("utf8"), /<p:timing>/);

  const macroEntries = readPptxArchive(featureBytes);
  macroEntries.set("ppt/vbaProject.bin", Buffer.from([0x56, 0x42, 0x41]));
  const macro = port.importPackage(writePptxArchive(macroEntries));
  assert.equal(macro.classification, "QUARANTINED");
  await assert.rejects(
    () =>
      port.saveBuffered(
        macro.document,
        path.join(temp, "must-not-publish.pptx"),
        next(port, macro.document),
        null,
      ),
    /Quarantined/,
  );
});
