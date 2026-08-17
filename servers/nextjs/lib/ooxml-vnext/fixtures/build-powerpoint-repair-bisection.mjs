#!/usr/bin/env node
import { createHash } from "node:crypto";
import { mkdir, mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import process from "node:process";
import { fileURLToPath } from "node:url";

import {
  readPptxArchive,
  writePptxArchive,
} from "../../authored-hybrid/pptx-archive.ts";
import { createOoxmlMasterCompilerPort } from "../adapter.ts";
import { DEMO_DECK_MASTER_CONTRACT } from "./demo-contract.ts";

const OFFICE_REL_NS =
  "http://schemas.openxmlformats.org/officeDocument/2006/relationships";
const CUSTOM_XML_PART = "customXml/item1.xml";
const OLE_PART = "ppt/embeddings/oleObject1.bin";
const SLIDE_RELS = "ppt/slides/_rels/slide1.xml.rels";
const SYNTHETIC_OLE_BYTES = Buffer.from("PRESENTON-OPAQUE-OLE-FIXTURE\0", "utf8");

function sha256(bytes) {
  return createHash("sha256").update(bytes).digest("hex");
}

function insertBeforeClosing(xml, closing, fragment) {
  if (!xml.includes(closing)) throw new Error(`fixture source lacks ${closing}`);
  return xml.replace(closing, `${fragment}${closing}`);
}

function cloneEntries(entries) {
  return new Map([...entries].map(([name, bytes]) => [name, Buffer.from(bytes)]));
}

function addCustomXml(entries) {
  entries.set(
    CUSTOM_XML_PART,
    Buffer.from(
      '<?xml version="1.0" encoding="UTF-8"?><fixture xmlns="urn:presenton:fixture">opaque-custom-xml</fixture>',
    ),
  );
}

function addExternalHyperlinkRelationship(entries) {
  entries.set(
    SLIDE_RELS,
    Buffer.from(
      insertBeforeClosing(
        entries.get(SLIDE_RELS).toString("utf8"),
        "</Relationships>",
        `<Relationship Id="rIdFixtureMail" Type="${OFFICE_REL_NS}/hyperlink" Target="mailto:fixture@example.invalid" TargetMode="External"/>`,
      ),
    ),
  );
}

function addSyntheticOlePart(entries) {
  entries.set(OLE_PART, SYNTHETIC_OLE_BYTES);
  entries.set(
    "[Content_Types].xml",
    Buffer.from(
      insertBeforeClosing(
        entries.get("[Content_Types].xml").toString("utf8"),
        "</Types>",
        '<Default Extension="bin" ContentType="application/vnd.openxmlformats-officedocument.oleObject"/>',
      ),
    ),
  );
}

function addOrphanOleRelationship(entries) {
  addSyntheticOlePart(entries);
  entries.set(
    SLIDE_RELS,
    Buffer.from(
      insertBeforeClosing(
        entries.get(SLIDE_RELS).toString("utf8"),
        "</Relationships>",
        `<Relationship Id="rIdOpaqueOle" Type="${OFFICE_REL_NS}/oleObject" Target="../embeddings/oleObject1.bin"/>`,
      ),
    ),
  );
}

async function buildCleanThreeSlides(destination) {
  const port = createOoxmlMasterCompilerPort();
  const created = port.createGenerated(DEMO_DECK_MASTER_CONTRACT);
  const slides = [
    ["layout:cover", { "cover-title": "PowerPoint 호환성 검증", "cover-body": "편집 가능한 마스터와 자리표시자 — 日本語 / 简体中文" }],
    ["layout:section", { "section-title": "복구 원인 분리 🔍", "section-body": "합성 추가 요소를 한 번에 하나씩 격리합니다." }],
    ["layout:standard", { "standard-title": "편집 가능한 본문", "standard-body": "이 텍스트를 수정하세요: é / 👩🏽‍💻 / 𠀀" }],
  ];
  for (const [index, [layoutId, values]] of slides.entries()) {
    port.materializeSlide(
      created.document,
      { slideId: `slide:repair-bisection-${index + 1}`, layoutId, values },
      port.readBack(created.document).precondition,
    );
  }
  await port.saveBuffered(
    created.document,
    destination,
    port.readBack(created.document).precondition,
    null,
  );
}

export async function buildPowerPointRepairBisection(outputDirectory) {
  const output = path.resolve(outputDirectory);
  await mkdir(output, { recursive: true });
  const temporaryDirectory = await mkdtemp(
    path.join(os.tmpdir(), "ooxml-vnext-repair-bisection-"),
  );
  try {
    const cleanPath = path.join(temporaryDirectory, "clean-3.pptx");
    await buildCleanThreeSlides(cleanPath);
    const cleanBytes = await readFile(cleanPath);
    const baseEntries = readPptxArchive(cleanBytes);
    const variants = [
      {
        id: "clean",
        fileName: "01-clean-3.pptx",
        mutation: null,
        isolates: "generated master/layout/placeholder package only",
        syntheticValidity: "product-generated baseline",
      },
      {
        id: "custom-xml-only",
        fileName: "02-custom-xml-only-3.pptx",
        mutation: addCustomXml,
        isolates: "unreferenced synthetic customXml/item1.xml",
        syntheticValidity: "opaque preservation probe; not a real imported-document sample",
      },
      {
        id: "external-hyperlink-rel-only",
        fileName: "03-external-hyperlink-rel-only-3.pptx",
        mutation: addExternalHyperlinkRelationship,
        isolates: "external hyperlink relationship with no matching a:hlinkClick consumer",
        syntheticValidity: "intentionally orphaned relationship",
      },
      {
        id: "synthetic-ole-part-only",
        fileName: "04-synthetic-ole-part-only-3.pptx",
        mutation: addSyntheticOlePart,
        isolates: "29-byte synthetic OLE-labeled part without a relationship",
        syntheticValidity: "intentionally not a valid OLE1/OLE2 object",
      },
      {
        id: "orphan-ole-relationship",
        fileName: "05-orphan-ole-relationship-3.pptx",
        mutation: addOrphanOleRelationship,
        isolates: "synthetic OLE part plus relationship with no p:oleObj r:id consumer",
        syntheticValidity: "intentionally invalid PresentationML object topology",
      },
      {
        id: "combined-existing-90-extras",
        fileName: "06-combined-existing-90-extras-3.pptx",
        mutation(entries) {
          addCustomXml(entries);
          addOrphanOleRelationship(entries);
          addExternalHyperlinkRelationship(entries);
        },
        isolates: "the custom XML, OLE, and external additions used by the 90-slide recipe",
        syntheticValidity: "negative compatibility fixture; not eligible as a clean product witness",
      },
    ];
    const receipts = [];
    for (const variant of variants) {
      const entries = cloneEntries(baseEntries);
      variant.mutation?.(entries);
      const bytes = variant.mutation ? writePptxArchive(entries) : cleanBytes;
      const destination = path.join(output, variant.fileName);
      await writeFile(destination, bytes, { flag: "wx" });
      receipts.push({
        id: variant.id,
        path: destination,
        fileName: variant.fileName,
        sha256: sha256(bytes),
        byteLength: bytes.length,
        partCount: entries.size,
        slideCount: 3,
        isolates: variant.isolates,
        syntheticValidity: variant.syntheticValidity,
        openXmlSdkValidation: "not-run",
        visiblePowerPointWitness: "not-run",
      });
    }
    const manifest = {
      schemaVersion: "ooxml-vnext/powerpoint-repair-bisection/v1",
      generatedAtUtc: new Date().toISOString(),
      source: "adapter-generated clean three-slide package",
      policy: {
        cleanProductWitness: "01-clean-3.pptx",
        opaqueImportClaim:
          "Real imported opaque parts remain PRESERVE_EXACT; the synthetic OLE variants are negative topology probes and do not represent a valid imported Office object.",
        interpretation:
          "Open XML SDK success is structural evidence only. A visible PowerPoint open is required to attribute a repair dialog to one variant.",
      },
      variants: receipts,
    };
    const manifestPath = path.join(output, "bisection-manifest.json");
    await writeFile(manifestPath, `${JSON.stringify(manifest, null, 2)}\n`, { flag: "wx" });
    return Object.freeze({ manifestPath, variants: Object.freeze(receipts) });
  } finally {
    await rm(temporaryDirectory, { recursive: true, force: true });
  }
}

if (process.argv[1] === fileURLToPath(import.meta.url)) {
  const outputDirectory = process.argv[2];
  if (!outputDirectory) {
    throw new Error("Usage: build-powerpoint-repair-bisection.mjs <new-output-directory>");
  }
  console.log(JSON.stringify(await buildPowerPointRepairBisection(outputDirectory), null, 2));
}
