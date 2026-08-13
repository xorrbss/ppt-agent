import assert from "node:assert/strict";
import { mkdtemp } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { test } from "node:test";
import { fileURLToPath, pathToFileURL } from "node:url";

import { build } from "esbuild";

const nextjsRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../..");
const OFFICE_REL_NS =
  "http://schemas.openxmlformats.org/officeDocument/2006/relationships";
const PACKAGE_REL_NS =
  "http://schemas.openxmlformats.org/package/2006/relationships";
let modulePromise;

function loadInventory() {
  modulePromise ??= (async () => {
    const directory = await mkdtemp(path.join(os.tmpdir(), "ooxml-vnext-inventory-"));
    const outfile = path.join(directory, "master-inventory.mjs");
    await build({
      absWorkingDir: nextjsRoot,
      bundle: true,
      entryPoints: ["lib/ooxml-vnext/master-inventory.ts"],
      format: "esm",
      outfile,
      platform: "node",
      tsconfig: path.join(nextjsRoot, "tsconfig.json"),
    });
    return import(pathToFileURL(outfile).href);
  })();
  return modulePromise;
}

function relationships(items) {
  return Buffer.from(
    `<?xml version="1.0" encoding="UTF-8"?><Relationships xmlns="${PACKAGE_REL_NS}">${items
      .map(
        ({ id, type, target, mode }) =>
          `<Relationship Id="${id}" Type="${OFFICE_REL_NS}/${type}" Target="${target}"${
            mode ? ` TargetMode="${mode}"` : ""
          }/>`,
      )
      .join("")}</Relationships>`,
  );
}

function masterXml(name, layoutIds) {
  return Buffer.from(
    `<p:sldMaster xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main" xmlns:r="${OFFICE_REL_NS}"><p:cSld name="${name}"/><p:sldLayoutIdLst>${layoutIds
      .map(
        ({ id, rel }) => `<p:sldLayoutId id="${id}" r:id="${rel}"/>`,
      )
      .join("")}</p:sldLayoutIdLst></p:sldMaster>`,
  );
}

function layoutXml(name) {
  return Buffer.from(
    `<p:sldLayout xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"><p:cSld name="${name}"/></p:sldLayout>`,
  );
}

function syntheticMultiMasterEntries() {
  const overrides = [
    ["/ppt/presentation.xml", "application/vnd.openxmlformats-officedocument.presentationml.presentation.main+xml"],
    ["/ppt/slideMasters/slideMaster1.xml", "application/vnd.openxmlformats-officedocument.presentationml.slideMaster+xml"],
    ["/ppt/slideMasters/slideMaster2.xml", "application/vnd.openxmlformats-officedocument.presentationml.slideMaster+xml"],
    ["/ppt/slideLayouts/slideLayout1.xml", "application/vnd.openxmlformats-officedocument.presentationml.slideLayout+xml"],
    ["/ppt/slideLayouts/slideLayout2.xml", "application/vnd.openxmlformats-officedocument.presentationml.slideLayout+xml"],
    ["/ppt/slideLayouts/slideLayout3.xml", "application/vnd.openxmlformats-officedocument.presentationml.slideLayout+xml"],
    ["/ppt/slides/slide1.xml", "application/vnd.openxmlformats-officedocument.presentationml.slide+xml"],
    ["/ppt/theme/theme1.xml", "application/vnd.openxmlformats-officedocument.theme+xml"],
    ["/ppt/theme/theme2.xml", "application/vnd.openxmlformats-officedocument.theme+xml"],
  ];
  return new Map([
    [
      "[Content_Types].xml",
      Buffer.from(
        `<?xml version="1.0" encoding="UTF-8"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Default Extension="png" ContentType="image/png"/>${overrides
          .map(([partName, contentType]) => `<Override PartName="${partName}" ContentType="${contentType}"/>`)
          .join("")}</Types>`,
      ),
    ],
    [
      "_rels/.rels",
      relationships([{ id: "rId1", type: "officeDocument", target: "ppt/presentation.xml" }]),
    ],
    [
      "ppt/presentation.xml",
      Buffer.from(
        `<p:presentation xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main" xmlns:r="${OFFICE_REL_NS}"><p:sldMasterIdLst><p:sldMasterId id="2147483648" r:id="rIdM1"/><p:sldMasterId id="2147483649" r:id="rIdM2"/></p:sldMasterIdLst></p:presentation>`,
      ),
    ],
    [
      "ppt/_rels/presentation.xml.rels",
      relationships([
        { id: "rIdM1", type: "slideMaster", target: "slideMasters/slideMaster1.xml" },
        { id: "rIdM2", type: "slideMaster", target: "slideMasters/slideMaster2.xml" },
        { id: "rIdS1", type: "slide", target: "slides/slide1.xml" },
      ]),
    ],
    [
      "ppt/slideMasters/slideMaster1.xml",
      masterXml("Corporate Blue", [
        { id: "1", rel: "rIdL1" },
        { id: "2", rel: "rIdL2" },
      ]),
    ],
    [
      "ppt/slideMasters/_rels/slideMaster1.xml.rels",
      relationships([
        { id: "rIdL1", type: "slideLayout", target: "../slideLayouts/slideLayout1.xml" },
        { id: "rIdL2", type: "slideLayout", target: "../slideLayouts/slideLayout2.xml" },
        { id: "rIdT1", type: "theme", target: "../theme/theme1.xml" },
        { id: "rIdI1", type: "image", target: "../media/logo.png" },
        {
          id: "rIdExternal",
          type: "hyperlink",
          target: "https://example.invalid/corporate",
          mode: "External",
        },
      ]),
    ],
    ["ppt/slideMasters/slideMaster2.xml", masterXml("Secondary", [{ id: "3", rel: "rIdL3" }])],
    [
      "ppt/slideMasters/_rels/slideMaster2.xml.rels",
      relationships([
        { id: "rIdL3", type: "slideLayout", target: "../slideLayouts/slideLayout3.xml" },
        { id: "rIdT2", type: "theme", target: "../theme/theme2.xml" },
      ]),
    ],
    ["ppt/slideLayouts/slideLayout1.xml", layoutXml("Title and Content")],
    [
      "ppt/slideLayouts/_rels/slideLayout1.xml.rels",
      relationships([{ id: "rId1", type: "slideMaster", target: "../slideMasters/slideMaster1.xml" }]),
    ],
    ["ppt/slideLayouts/slideLayout2.xml", layoutXml("Section Header")],
    [
      "ppt/slideLayouts/_rels/slideLayout2.xml.rels",
      relationships([{ id: "rId1", type: "slideMaster", target: "../slideMasters/slideMaster1.xml" }]),
    ],
    ["ppt/slideLayouts/slideLayout3.xml", layoutXml("Blank")],
    [
      "ppt/slideLayouts/_rels/slideLayout3.xml.rels",
      relationships([{ id: "rId1", type: "slideMaster", target: "../slideMasters/slideMaster2.xml" }]),
    ],
    ["ppt/theme/theme1.xml", Buffer.from("<a:theme xmlns:a=\"http://schemas.openxmlformats.org/drawingml/2006/main\" name=\"Blue\"/>")],
    ["ppt/theme/theme2.xml", Buffer.from("<a:theme xmlns:a=\"http://schemas.openxmlformats.org/drawingml/2006/main\" name=\"Gray\"/>")],
    ["ppt/media/logo.png", Buffer.from([137, 80, 78, 71, 13, 10, 26, 10, 1, 2, 3])],
    ["ppt/slides/slide1.xml", Buffer.from("<p:sld xmlns:p=\"http://schemas.openxmlformats.org/presentationml/2006/main\"/>")],
    [
      "ppt/slides/_rels/slide1.xml.rels",
      relationships([{ id: "rId1", type: "slideLayout", target: "../slideLayouts/slideLayout1.xml" }]),
    ],
  ]);
}

test("inventories every imported master/layout edge with stable PowerPoint ids and backlinks", async () => {
  const { inventoryMasterLayouts } = await loadInventory();
  const inventory = inventoryMasterLayouts(syntheticMultiMasterEntries());

  assert.equal(inventory.version, "ooxml-master-layout-inventory/v1");
  assert.equal(inventory.masters.length, 2);
  assert.equal(inventory.layouts.length, 3);
  assert.deepEqual(inventory.issueCodes, []);
  assert.deepEqual(
    inventory.masters.map(({ partName, name }) => [partName, name]),
    [
      ["ppt/slideMasters/slideMaster1.xml", "Corporate Blue"],
      ["ppt/slideMasters/slideMaster2.xml", "Secondary"],
    ],
  );
  assert.deepEqual(inventory.masters[0].presentationReferences, [
    { relationshipId: "rIdM1", presentationMasterId: "2147483648", order: 0 },
  ]);
  assert.deepEqual(
    inventory.masters[0].layouts.map((layout) => [
      layout.layoutPartName,
      layout.masterLayoutId,
      layout.relationshipId,
      layout.order,
    ]),
    [
      ["ppt/slideLayouts/slideLayout1.xml", "1", "rIdL1", 0],
      ["ppt/slideLayouts/slideLayout2.xml", "2", "rIdL2", 1],
    ],
  );
  assert.deepEqual(inventory.layouts[2].parentMasterParts, [
    "ppt/slideMasters/slideMaster2.xml",
  ]);
  assert.equal(inventory.structuralRelationships.length, 9);
  assert.ok(
    inventory.structuralRelationships.some(
      (relationship) =>
        relationship.source === "ppt/slides/slide1.xml" &&
        relationship.normalizedTarget === "ppt/slideLayouts/slideLayout1.xml",
    ),
  );
});

test("captures one corporate master as an immutable byte-exact relationship closure", async () => {
  const { captureCorporateMaster, materializeCorporateMasterBundle } = await loadInventory();
  const entries = syntheticMultiMasterEntries();
  const originalMaster = Buffer.from(entries.get("ppt/slideMasters/slideMaster1.xml"));
  const bundle = captureCorporateMaster(entries, { presentationMasterId: "2147483648" });

  assert.equal(bundle.version, "ooxml-corporate-master-opaque/v1");
  assert.equal(bundle.masterPartName, "ppt/slideMasters/slideMaster1.xml");
  assert.deepEqual(bundle.layoutPartNames, [
    "ppt/slideLayouts/slideLayout1.xml",
    "ppt/slideLayouts/slideLayout2.xml",
  ]);
  assert.equal(bundle.externalRelationshipCount, 1);
  assert.equal(bundle.publicationPolicy.mutationClassification, "PRESERVE_EXACT");
  assert.equal(bundle.publicationPolicy.partNames, "IDENTITY_ONLY");
  assert.ok(Object.isFrozen(bundle));
  assert.ok(Object.isFrozen(bundle.members));
  assert.ok(Object.isFrozen(bundle.members[0]));
  assert.ok(bundle.closurePartNames.includes("ppt/media/logo.png"));
  assert.ok(!bundle.closurePartNames.includes("ppt/slideMasters/slideMaster2.xml"));
  assert.ok(!bundle.closurePartNames.includes("ppt/theme/theme2.xml"));
  assert.ok(!bundle.closurePartNames.includes("ppt/slides/slide1.xml"));

  entries.get("ppt/slideMasters/slideMaster1.xml").fill(0);
  const first = materializeCorporateMasterBundle(bundle);
  assert.deepEqual(first.get(bundle.masterPartName), originalMaster);
  first.get(bundle.masterPartName).fill(1);
  const second = materializeCorporateMasterBundle(bundle);
  assert.deepEqual(second.get(bundle.masterPartName), originalMaster);
});

test("corporate bundle publication rejects digest tampering and non-identical collisions", async () => {
  const {
    captureCorporateMaster,
    materializeCorporateMasterBundle,
    MasterInventoryError,
  } = await loadInventory();
  const bundle = captureCorporateMaster(syntheticMultiMasterEntries(), {
    presentationRelationshipId: "rIdM1",
  });
  const alteredMember = {
    ...bundle.members[0],
    payloadBase64: Buffer.from("tampered").toString("base64"),
  };
  assert.throws(
    () => materializeCorporateMasterBundle({ ...bundle, members: [alteredMember, ...bundle.members.slice(1)] }),
    (error) => error instanceof MasterInventoryError && error.code === "BUNDLE_MEMBER_DIGEST_MISMATCH",
  );
  const alteredRelationship = {
    ...bundle.relationships[0],
    target: "../theme/substituted.xml",
  };
  const relationshipTamperedBundle = {
    ...bundle,
    relationships: [alteredRelationship, ...bundle.relationships.slice(1)],
  };
  assert.throws(
    () => materializeCorporateMasterBundle(relationshipTamperedBundle),
    (error) =>
      error instanceof MasterInventoryError &&
      error.code === "BUNDLE_RELATIONSHIP_INVENTORY_MISMATCH",
  );
  assert.throws(
    () => materializeCorporateMasterBundle(bundle, new Map([[bundle.masterPartName, Buffer.from("collision")]])),
    (error) => error instanceof MasterInventoryError && error.code === "PART_COLLISION",
  );
  const exactDestination = materializeCorporateMasterBundle(bundle);
  assert.doesNotThrow(() => materializeCorporateMasterBundle(bundle, exactDestination));
});

test("corporate capture fails closed when a layout backlink is not consistent", async () => {
  const { captureCorporateMaster, inventoryMasterLayouts, MasterInventoryError } =
    await loadInventory();
  const entries = syntheticMultiMasterEntries();
  entries.set("ppt/slideLayouts/_rels/slideLayout2.xml.rels", relationships([]));
  const inventory = inventoryMasterLayouts(entries);
  assert.deepEqual(inventory.layouts[1].issueCodes, [
    "LAYOUT_MASTER_BACKLINK_MISSING_OR_INCONSISTENT",
  ]);
  assert.throws(
    () => captureCorporateMaster(entries, { partName: "ppt/slideMasters/slideMaster1.xml" }),
    (error) => error instanceof MasterInventoryError && error.code === "UNSAFE_LAYOUT_TOPOLOGY",
  );
});
