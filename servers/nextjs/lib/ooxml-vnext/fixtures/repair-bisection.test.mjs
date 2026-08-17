import assert from "node:assert/strict";
import { mkdtemp, readFile, rm } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import test from "node:test";

import { readPptxArchive } from "../../authored-hybrid/pptx-archive.ts";
import { buildPowerPointRepairBisection } from "./build-powerpoint-repair-bisection.mjs";

test("three-slide repair bisection isolates synthetic custom XML, external, and OLE additions", async (t) => {
  const output = await mkdtemp(path.join(os.tmpdir(), "ooxml-repair-bisection-test-"));
  t.after(() => rm(output, { recursive: true, force: true }));
  const built = await buildPowerPointRepairBisection(output);
  assert.equal(built.variants.length, 6);
  const byId = new Map(built.variants.map((variant) => [variant.id, variant]));
  const archive = async (id) => readPptxArchive(await readFile(byId.get(id).path));

  const clean = await archive("clean");
  assert.equal(clean.has("customXml/item1.xml"), false);
  assert.equal(clean.has("ppt/embeddings/oleObject1.bin"), false);
  assert.doesNotMatch(
    clean.get("ppt/slides/_rels/slide1.xml.rels").toString("utf8"),
    /TargetMode="External"|\/oleObject"/,
  );

  const customXml = await archive("custom-xml-only");
  assert.equal(customXml.has("customXml/item1.xml"), true);
  assert.equal(customXml.has("ppt/embeddings/oleObject1.bin"), false);

  const external = await archive("external-hyperlink-rel-only");
  assert.match(
    external.get("ppt/slides/_rels/slide1.xml.rels").toString("utf8"),
    /TargetMode="External"/,
  );
  assert.equal(external.has("customXml/item1.xml"), false);
  assert.equal(external.has("ppt/embeddings/oleObject1.bin"), false);

  const olePart = await archive("synthetic-ole-part-only");
  assert.equal(olePart.has("ppt/embeddings/oleObject1.bin"), true);
  assert.doesNotMatch(
    olePart.get("ppt/slides/_rels/slide1.xml.rels").toString("utf8"),
    /\/oleObject"/,
  );

  const orphanOle = await archive("orphan-ole-relationship");
  assert.match(
    orphanOle.get("ppt/slides/_rels/slide1.xml.rels").toString("utf8"),
    /\/oleObject"/,
  );
  assert.doesNotMatch(orphanOle.get("ppt/slides/slide1.xml").toString("utf8"), /<p:oleObj\b/);

  const combined = await archive("combined-existing-90-extras");
  assert.equal(combined.has("customXml/item1.xml"), true);
  assert.equal(combined.has("ppt/embeddings/oleObject1.bin"), true);
  const combinedRels = combined.get("ppt/slides/_rels/slide1.xml.rels").toString("utf8");
  assert.match(combinedRels, /\/oleObject"/);
  assert.match(combinedRels, /TargetMode="External"/);

  const manifest = JSON.parse(await readFile(built.manifestPath, "utf8"));
  assert.equal(manifest.policy.cleanProductWitness, "01-clean-3.pptx");
  assert.match(manifest.policy.opaqueImportClaim, /PRESERVE_EXACT/);
  assert.match(byId.get("orphan-ole-relationship").syntheticValidity, /invalid/);
});
