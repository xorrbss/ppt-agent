import assert from "node:assert/strict";
import test from "node:test";

import { writePptxArchive } from "../authored-hybrid/pptx-archive.ts";
import {
  assertPackagePreservation,
  buildPackageGraph,
  PackagePreservationError,
} from "./package-graph.ts";
import {
  inspectPptxPackageSecurity,
  OoxmlSecurityError,
} from "./security.ts";

const REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships";
const OFFICE_REL_NS =
  "http://schemas.openxmlformats.org/officeDocument/2006/relationships";

function safeEntries() {
  return new Map([
    [
      "[Content_Types].xml",
      Buffer.from(`<?xml version="1.0" encoding="UTF-8"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
  <Default Extension="xml" ContentType="application/xml"/>
  <Default Extension="bin" ContentType="application/octet-stream"/>
  <Override PartName="/ppt/presentation.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.presentation.main+xml"/>
  <Override PartName="/ppt/slides/slide1.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.slide+xml"/>
</Types>`),
    ],
    [
      "_rels/.rels",
      Buffer.from(`<?xml version="1.0" encoding="UTF-8"?>
<Relationships xmlns="${REL_NS}"><Relationship Id="rId1" Type="${OFFICE_REL_NS}/officeDocument" Target="ppt/presentation.xml"/></Relationships>`),
    ],
    [
      "ppt/presentation.xml",
      Buffer.from(
        '<p:presentation xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"/>'
      ),
    ],
    [
      "ppt/_rels/presentation.xml.rels",
      Buffer.from(`<?xml version="1.0" encoding="UTF-8"?>
<Relationships xmlns="${REL_NS}"><Relationship Id="rId1" Type="${OFFICE_REL_NS}/slide" Target="slides/slide1.xml"/></Relationships>`),
    ],
    [
      "ppt/slides/slide1.xml",
      Buffer.from(
        '<p:sld xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"><p:cSld><a:t xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main">before</a:t></p:cSld></p:sld>'
      ),
    ],
  ]);
}

function graphFrom(entries, options) {
  const inspection = inspectPptxPackageSecurity(writePptxArchive(entries));
  return buildPackageGraph(inspection.entries, {
    policy: inspection.policy,
    ...options,
  });
}

test("package entries and inventory manifests are immutable defensive snapshots", () => {
  const source = safeEntries();
  const graph = graphFrom(source);
  const digest = graph.packageDigest;
  const originalSlide = graph.partDigests["ppt/slides/slide1.xml"];

  source.get("ppt/slides/slide1.xml")[0] ^= 0xff;
  const returned = graph.entries.get("ppt/slides/slide1.xml");
  returned[0] ^= 0xff;
  const iterated = [...graph.entries].find(([name]) => name === "ppt/slides/slide1.xml")[1];
  iterated[0] ^= 0xff;

  assert.equal(graph.packageDigest, digest);
  assert.equal(graph.inventory.packageDigest, digest);
  assert.equal(graph.inventory.partCount, graph.parts.length);
  assert.equal(graph.preservationManifest.sourcePackageDigest, digest);
  assert.equal(
    graph.entries.get("ppt/slides/slide1.xml").toString("hex"),
    safeEntries().get("ppt/slides/slide1.xml").toString("hex")
  );
  assert.equal(graph.partDigests["ppt/slides/slide1.xml"], originalSlide);
});

test("preservation enforcement fails closed outside PATCH_SLICE and GENERATED parts", () => {
  const baseline = graphFrom(safeEntries());
  const patched = safeEntries();
  patched.set(
    "ppt/slides/slide1.xml",
    Buffer.from(patched.get("ppt/slides/slide1.xml").toString().replace("before", "after"))
  );

  assert.throws(
    () => assertPackagePreservation(baseline.preservationManifest, patched),
    (error) =>
      error instanceof PackagePreservationError &&
      error.failures.includes("preserveExactChanged:ppt/slides/slide1.xml")
  );
  const patchReceipt = assertPackagePreservation(
    baseline.preservationManifest,
    patched,
    { patchSliceParts: ["ppt/slides/slide1.xml"] }
  );
  assert.deepEqual(patchReceipt.changedParts, ["ppt/slides/slide1.xml"]);
  assert.equal(patchReceipt.patchSlicePartCount, 1);

  patched.set("customXml/item2.xml", Buffer.from("<generated/>"));
  assert.throws(
    () =>
      assertPackagePreservation(baseline.preservationManifest, patched, {
        patchSliceParts: ["ppt/slides/slide1.xml"],
      }),
    (error) =>
      error instanceof PackagePreservationError &&
      error.failures.includes("unexpectedAdded:customXml/item2.xml")
  );
  const generatedReceipt = assertPackagePreservation(
    baseline.preservationManifest,
    patched,
    {
      patchSliceParts: ["ppt/slides/slide1.xml"],
      generatedParts: ["customXml/item2.xml"],
    }
  );
  assert.deepEqual(generatedReceipt.addedParts, ["customXml/item2.xml"]);
});

test("tampered and quarantined preservation manifests cannot authorize publication", () => {
  const baseline = graphFrom(safeEntries());
  const tampered = {
    ...baseline.preservationManifest,
    sourcePackageDigest: "0".repeat(64),
  };
  assert.throws(
    () => assertPackagePreservation(tampered, safeEntries()),
    (error) =>
      error instanceof PackagePreservationError &&
      error.failures.includes("manifestPackageDigest") &&
      error.failures.includes("manifestDigest")
  );

  const macro = safeEntries();
  macro.set("ppt/embeddings/vbaProject.bin", Buffer.from("inert macro bytes"));
  const macroGraph = graphFrom(macro);
  assert.equal(macroGraph.classification, "QUARANTINED");
  assert.ok(
    macroGraph.quarantineReasons.includes("part:ppt/embeddings/vbaProject.bin")
  );
  assert.throws(
    () => assertPackagePreservation(macroGraph.preservationManifest, macro),
    (error) =>
      error instanceof PackagePreservationError &&
      error.failures.includes("sourceQuarantined") &&
      error.failures.includes("candidateQuarantined")
  );
});

test("direct graph construction cannot bypass member, XML, or path limits", () => {
  assert.throws(
    () => buildPackageGraph(safeEntries(), { policy: { maxMembers: 2 } }),
    (error) =>
      error instanceof OoxmlSecurityError && error.code === "MEMBER_COUNT_LIMIT"
  );
  assert.throws(
    () => buildPackageGraph(safeEntries(), { policy: { maxXmlPartBytes: 32 } }),
    (error) => error instanceof OoxmlSecurityError && error.code === "XML_SIZE_LIMIT"
  );
  const unsafe = safeEntries();
  unsafe.set("ppt/%2e%2e/escape.xml", Buffer.from("<x/>"));
  assert.throws(
    () => buildPackageGraph(unsafe),
    (error) => error instanceof OoxmlSecurityError && error.code === "UNSAFE_PART_NAME"
  );
});

test("safe empty ZIP directory entries are accepted but omitted from package parts", () => {
  const entries = safeEntries();
  entries.set("docProps/", Buffer.alloc(0));
  const inspection = inspectPptxPackageSecurity(writePptxArchive(entries));

  assert.equal(inspection.entries.has("docProps/"), false);
  assert.equal(
    inspection.members.find((member) => member.name === "docProps/")?.isDirectory,
    true
  );
  const graph = buildPackageGraph(inspection.entries);
  assert.equal(Object.hasOwn(graph.partDigests, "docProps/"), false);
});

test("ZIP local and central metadata disagreement is rejected before inflation", () => {
  const bytes = Buffer.from(writePptxArchive(safeEntries()));
  const centralSignature = Buffer.from([0x50, 0x4b, 0x01, 0x02]);
  const central = bytes.indexOf(centralSignature);
  assert.ok(central >= 0);
  const localOffset = bytes.readUInt32LE(central + 42);
  bytes[localOffset + 30] ^= 0x01;

  assert.throws(
    () => inspectPptxPackageSecurity(bytes),
    (error) => error instanceof OoxmlSecurityError && error.code === "INVALID_ZIP"
  );
});
