import assert from "node:assert/strict";
import test from "node:test";

import { writePptxArchive } from "../authored-hybrid/pptx-archive.ts";
import {
  assertPackageGraphPrecondition,
  buildPackageGraph,
  normalizeRelationshipTarget,
} from "./package-graph.ts";
import {
  inspectPptxPackageSecurity,
  OoxmlSecurityError,
} from "./security.ts";
import { planExternalRelationshipRemoval } from "./external-relationship-policy.ts";

const REL_NS =
  "http://schemas.openxmlformats.org/package/2006/relationships";
const OFFICE_REL_NS =
  "http://schemas.openxmlformats.org/officeDocument/2006/relationships";

function typesXml({ macro = false } = {}) {
  return `<?xml version="1.0" encoding="UTF-8"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
  <Default Extension="xml" ContentType="application/xml"/>
  <Default Extension="png" ContentType="image/png"/>
  <Default Extension="bin" ContentType="${
    macro ? "application/vnd.ms-office.vbaProject" : "application/octet-stream"
  }"/>
  <Override PartName="/ppt/presentation.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.presentation.${
    macro ? "macroEnabled" : "main"
  }+xml"/>
  <Override PartName="/ppt/slides/slide1.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.slide+xml"/>
</Types>`;
}

function relationshipXml(items) {
  return `<?xml version="1.0" encoding="UTF-8"?>
<Relationships xmlns="${REL_NS}">${items
    .map(
      ({ id, type, target, mode }) =>
        `<Relationship Id="${id}" Type="${type}" Target="${target}"${
          mode ? ` TargetMode="${mode}"` : ""
        }/>`
    )
    .join("")}</Relationships>`;
}

function safeEntries({ macro = false, externalMode = "External" } = {}) {
  const entries = new Map([
    ["[Content_Types].xml", Buffer.from(typesXml({ macro }))],
    [
      "_rels/.rels",
      Buffer.from(
        relationshipXml([
          {
            id: "rId1",
            type: `${OFFICE_REL_NS}/officeDocument`,
            target: "ppt/presentation.xml",
          },
        ])
      ),
    ],
    [
      "ppt/presentation.xml",
      Buffer.from(
        '<p:presentation xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"/>'
      ),
    ],
    [
      "ppt/_rels/presentation.xml.rels",
      Buffer.from(
        relationshipXml([
          {
            id: "rId1",
            type: `${OFFICE_REL_NS}/slide`,
            target: "slides/slide1.xml",
          },
          {
            id: "rId2",
            type: `${OFFICE_REL_NS}/hyperlink`,
            target: "https://example.invalid/a?x=1&amp;y=2#frag",
            mode: externalMode,
          },
          ...(macro
            ? [
                {
                  id: "rId3",
                  type: `${OFFICE_REL_NS}/vbaProject`,
                  target: "vbaProject.bin",
                },
              ]
            : []),
        ])
      ),
    ],
    [
      "ppt/slides/slide1.xml",
      Buffer.from(
        '<p:sld xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"/>'
      ),
    ],
    [
      "ppt/slides/_rels/slide1.xml.rels",
      Buffer.from(
        relationshipXml([
          {
            id: "rId1",
            type: `${OFFICE_REL_NS}/image`,
            target: "../media/image1.png",
          },
        ])
      ),
    ],
    ["ppt/media/image1.png", Buffer.from([137, 80, 78, 71, 1, 2, 3])],
  ]);
  if (macro) entries.set("ppt/vbaProject.bin", Buffer.from("macro-is-inert"));
  return entries;
}

function inspect(entries, override) {
  return inspectPptxPackageSecurity(writePptxArchive(entries), override);
}

test("bounded import classifies parts and preserves external relationships as inert evidence", () => {
  const inspection = inspect(safeEntries());
  const graph = buildPackageGraph(inspection.entries, {
    policy: inspection.policy,
    patchSliceParts: ["ppt/slides/slide1.xml"],
    generatedParts: ["ppt/media/image1.png"],
  });

  assert.equal(graph.classification, "PRESERVE_EXACT");
  assert.equal(graph.macroDetected, false);
  assert.equal(graph.externalRelationships.length, 1);
  assert.equal(
    graph.externalRelationships[0].target,
    "https://example.invalid/a?x=1&y=2#frag"
  );
  assert.equal(graph.externalRelationships[0].targetModeRaw, "External");
  assert.equal(graph.externalRelationships[0].normalizedTarget, null);
  assert.equal(graph.externalRelationshipPolicy.decisions[0].risk, "HYPERLINK");
  assert.equal(graph.externalRelationshipPolicy.decisions[0].disposition, "WARN_PRESERVE");
  assert.equal(graph.externalRelationshipPolicy.warnings.length, 1);
  assert.equal(graph.externalRelationshipPolicy.quarantineReasons.length, 0);
  assert.equal(
    graph.parts.find((part) => part.name === "ppt/slides/slide1.xml")
      ?.classification,
    "PATCH_SLICE"
  );
  assert.equal(
    graph.parts.find((part) => part.name === "ppt/media/image1.png")
      ?.classification,
    "GENERATED"
  );
  assert.ok(graph.reachableParts.includes("ppt/media/image1.png"));
});

test("HTTP, file and UNC resources, and template links quarantine until explicit removal", () => {
  for (const [type, target, expectedRisk] of [
    [`${OFFICE_REL_NS}/image`, "https://example.invalid/tracker.png", "HTTP_RESOURCE"],
    [`${OFFICE_REL_NS}/image`, "file:///C:/private/logo.png", "FILE_RESOURCE"],
    [`${OFFICE_REL_NS}/image`, "\\\\fileserver\\share\\logo.png", "UNC_RESOURCE"],
    [`${OFFICE_REL_NS}/attachedTemplate`, "https://example.invalid/template.potx", "TEMPLATE_LINK"],
  ]) {
    const entries = safeEntries();
    entries.set(
      "ppt/_rels/presentation.xml.rels",
      Buffer.from(
        relationshipXml([
          { id: "rId1", type: `${OFFICE_REL_NS}/slide`, target: "slides/slide1.xml" },
          { id: "rId9", type, target, mode: "External" },
        ]),
      ),
    );
    const graph = buildPackageGraph(inspect(entries).entries);
    assert.equal(graph.classification, "QUARANTINED");
    assert.equal(graph.macroDetected, false);
    assert.equal(graph.externalRelationshipPolicy.decisions[0].risk, expectedRisk);
    assert.equal(graph.externalRelationshipPolicy.decisions[0].disposition, "QUARANTINE");
    const removal = planExternalRelationshipRemoval(graph.externalRelationshipPolicy);
    assert.equal(removal.length, 1);
    assert.equal(removal[0].disposition, "REMOVE_APPROVED");
  }
});

test("mailto is warning-preserved while unknown active content quarantines", () => {
  const mailEntries = safeEntries();
  mailEntries.set(
    "ppt/_rels/presentation.xml.rels",
    Buffer.from(
      relationshipXml([
        { id: "rId1", type: `${OFFICE_REL_NS}/slide`, target: "slides/slide1.xml" },
        { id: "rId9", type: `${OFFICE_REL_NS}/unknownMail`, target: "mailto:test@example.invalid", mode: "External" },
      ]),
    ),
  );
  const mailGraph = buildPackageGraph(inspect(mailEntries).entries);
  assert.equal(mailGraph.classification, "PRESERVE_EXACT");
  assert.equal(mailGraph.externalRelationshipPolicy.decisions[0].risk, "HYPERLINK");
  assert.equal(mailGraph.externalRelationshipPolicy.decisions[0].disposition, "WARN_PRESERVE");

  const activeEntries = safeEntries();
  activeEntries.set("ppt/activeX/activeX1.xml", Buffer.from('<ax:ocx xmlns:ax="urn:active"/>'));
  activeEntries.set(
    "[Content_Types].xml",
    Buffer.from(
      activeEntries.get("[Content_Types].xml").toString("utf8").replace(
        "</Types>",
        '<Override PartName="/ppt/activeX/activeX1.xml" ContentType="application/vnd.ms-office.activeX+xml"/></Types>',
      ),
    ),
  );
  const activeGraph = buildPackageGraph(inspect(activeEntries).entries);
  assert.equal(activeGraph.classification, "QUARANTINED");
  assert.equal(activeGraph.macroDetected, false);
  assert.ok(activeGraph.quarantineReasons.some((reason) => reason.startsWith("active-part:")));
});

test("relationship digest binds exact external target and TargetMode spelling", () => {
  const baseline = buildPackageGraph(inspect(safeEntries()).entries);
  const changedMode = buildPackageGraph(
    inspect(safeEntries({ externalMode: "external" })).entries
  );
  const changedTargetEntries = safeEntries();
  changedTargetEntries.set(
    "ppt/_rels/presentation.xml.rels",
    Buffer.from(
      changedTargetEntries
        .get("ppt/_rels/presentation.xml.rels")
        .toString("utf8")
        .replace("example.invalid/a", "example.invalid/b")
    )
  );
  const changedTarget = buildPackageGraph(inspect(changedTargetEntries).entries);

  assert.notEqual(baseline.relationshipDigest, changedMode.relationshipDigest);
  assert.notEqual(baseline.relationshipDigest, changedTarget.relationshipDigest);
  assert.notEqual(baseline.closureDigest, changedMode.closureDigest);
});

test("part, package, relationship, and closure digest preconditions fail closed", () => {
  const graph = buildPackageGraph(inspect(safeEntries()).entries);
  assert.doesNotThrow(() =>
    assertPackageGraphPrecondition(graph, {
      packageDigest: graph.packageDigest,
      relationshipDigest: graph.relationshipDigest,
      closureDigest: graph.closureDigest,
      partDigests: {
        "ppt/slides/slide1.xml": graph.partDigests["ppt/slides/slide1.xml"],
      },
    })
  );
  assert.throws(
    () =>
      assertPackageGraphPrecondition(graph, {
        packageDigest: "",
        relationshipDigest: "0".repeat(64),
        closureDigest: "1".repeat(64),
        partDigests: {
          "ppt/slides/slide1.xml": "2".repeat(64),
          "ppt/slides/missing.xml": "3".repeat(64),
        },
      }),
    (error) => {
      assert.equal(error.code, "PRECONDITION_FAILED");
      assert.deepEqual(error.failures, [
        "packageDigest",
        "relationshipDigest",
        "closureDigest",
        "partDigest:ppt/slides/slide1.xml",
        "partMissing:ppt/slides/missing.xml",
      ]);
      return true;
    }
  );
});

test("macro evidence quarantines the whole package", () => {
  const graph = buildPackageGraph(inspect(safeEntries({ macro: true })).entries);
  assert.equal(graph.macroDetected, true);
  assert.equal(graph.classification, "QUARANTINED");
  assert.ok(graph.quarantineReasons.some((reason) => reason.startsWith("part:")));
  assert.ok(
    graph.parts.length > 0 &&
      graph.parts.every((part) => part.classification === "QUARANTINED")
  );
});

test("security inspection enforces compression, XML, and member limits", () => {
  assert.throws(
    () => inspect(safeEntries(), { maxMembers: 2 }),
    (error) => error instanceof OoxmlSecurityError && error.code === "MEMBER_COUNT_LIMIT"
  );
  assert.throws(
    () => inspect(safeEntries(), { maxCompressionRatio: 1 }),
    (error) =>
      error instanceof OoxmlSecurityError && error.code === "COMPRESSION_RATIO_LIMIT"
  );

  const activeXml = safeEntries();
  activeXml.set(
    "ppt/slides/slide1.xml",
    Buffer.from('<!DOCTYPE x [<!ENTITY e "boom">]><x>&e;</x>')
  );
  assert.throws(
    () => inspect(activeXml),
    (error) => error instanceof OoxmlSecurityError && error.code === "ACTIVE_XML"
  );
});

test("internal targets normalize against their source but cannot escape the package", () => {
  assert.equal(
    normalizeRelationshipTarget(
      "ppt/slides/slide1.xml",
      "../slideLayouts/slideLayout1.xml"
    ),
    "ppt/slideLayouts/slideLayout1.xml"
  );
  assert.throws(
    () => normalizeRelationshipTarget("ppt/slides/slide1.xml", "../../../escape.xml"),
    /Unsafe internal relationship target/
  );
  assert.throws(
    () => normalizeRelationshipTarget("ppt/slides/slide1.xml", "https://example.invalid/x"),
    /Unsafe internal relationship target/
  );
});

test("missing internal targets and duplicate relationship ids are rejected", () => {
  const missing = safeEntries();
  missing.delete("ppt/media/image1.png");
  assert.throws(
    () => buildPackageGraph(inspect(missing).entries),
    (error) => error.code === "MISSING_RELATIONSHIP_TARGET"
  );

  const duplicate = safeEntries();
  duplicate.set(
    "ppt/_rels/presentation.xml.rels",
    Buffer.from(
      relationshipXml([
        {
          id: "rId1",
          type: `${OFFICE_REL_NS}/slide`,
          target: "slides/slide1.xml",
        },
        {
          id: "rId1",
          type: `${OFFICE_REL_NS}/hyperlink`,
          target: "https://example.invalid",
          mode: "External",
        },
      ])
    )
  );
  assert.throws(
    () => buildPackageGraph(inspect(duplicate).entries),
    (error) => error.code === "DUPLICATE_RELATIONSHIP_ID"
  );
});

test("PPTX graph requires root relationships, one office document, and content types for every part", () => {
  const missingRoot = safeEntries();
  missingRoot.delete("_rels/.rels");
  assert.throws(
    () => buildPackageGraph(inspect(missingRoot).entries),
    (error) => error.code === "MISSING_ROOT_RELATIONSHIPS"
  );

  const noOfficeDocument = safeEntries();
  noOfficeDocument.set("_rels/.rels", Buffer.from(relationshipXml([])));
  assert.throws(
    () => buildPackageGraph(inspect(noOfficeDocument).entries),
    (error) => error.code === "INVALID_OFFICE_DOCUMENT_RELATIONSHIP"
  );

  const untypedPart = safeEntries();
  untypedPart.set("ppt/media/orphan.unknown", Buffer.from("opaque"));
  assert.throws(
    () => buildPackageGraph(inspect(untypedPart).entries),
    (error) => error.code === "MISSING_PART_CONTENT_TYPE"
  );
});
