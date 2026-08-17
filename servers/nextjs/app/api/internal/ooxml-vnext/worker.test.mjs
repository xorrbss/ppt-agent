import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { mkdtemp, readFile, readdir, rm } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import test from "node:test";
import { fileURLToPath, pathToFileURL } from "node:url";

import { transform } from "esbuild";
import { NextRequest } from "next/server.js";
import { DEMO_DECK_MASTER_CONTRACT } from "../../../../lib/ooxml-vnext/fixtures/demo-contract.ts";
import { readPptxArchive } from "../../../../lib/authored-hybrid/pptx-archive.ts";
import {
  executeWorkerExport,
  OoxmlWorkerError,
  sha256CanonicalJson,
  WORKER_OPERATION,
  WORKER_SCHEMA_VERSION,
} from "./worker.ts";

const testDirectory = path.dirname(fileURLToPath(import.meta.url));

async function loadRoute() {
  const source = await readFile(path.join(testDirectory, "route.ts"), "utf8");
  const nextServerUrl = pathToFileURL(
    path.join(testDirectory, "../../../../node_modules/next/server.js"),
  ).href;
  const workerUrl = pathToFileURL(path.join(testDirectory, "worker.ts")).href;
  const executableSource = source
    .replace('"next/server"', JSON.stringify(nextServerUrl))
    .replace('"./worker.ts"', JSON.stringify(workerUrl));
  const compiled = await transform(executableSource, { format: "esm", loader: "ts", target: "esnext" });
  return import(`data:text/javascript;base64,${Buffer.from(compiled.code).toString("base64")}`);
}

const SECRET = "worker-test-secret-that-is-longer-than-thirty-two-bytes";
const slides = [
  {
    slideId: "slide:test-1",
    layoutId: "layout:standard",
    values: {
      "standard-title": "Durable export",
      "standard-body": "Buffered and reopened",
    },
  },
];

function envelope(overrides = {}) {
  const master = structuredClone(DEMO_DECK_MASTER_CONTRACT);
  const assets = [];
  return {
    schemaVersion: WORKER_SCHEMA_VERSION,
    operation: WORKER_OPERATION,
    operationId: "operation-test-1",
    fencingToken: 7,
    master,
    slides: structuredClone(slides),
    assets,
    digests: {
      masterSha256: sha256CanonicalJson(master),
      specSha256: sha256CanonicalJson(slides),
      assetManifestSha256: sha256CanonicalJson([]),
      fontManifestSha256: sha256CanonicalJson(master.fontManifest),
      packageSha256: null,
    },
    ...overrides,
  };
}

function retainedReferenceSlide(pageNumber, slideCount) {
  const title = `Retained page ${pageNumber}`;
  const body = `resolved semantic body ${pageNumber}`;
  const referenceLayoutDigest = "sha256:" + "b".repeat(64);
  const grammar = {
    schemaVersion: "authored-body-grammar/v3",
    styleId: "osb-smart-banking",
    kind: "process-grid",
    archetype: "process",
    slideRole: "content",
    bodyRole: "standard-body",
    sourceTextDigest: `sha256:${createHash("sha256").update(body, "utf8").digest("hex")}`,
    bodySlot: { x: 90, y: 160, width: 1100, height: 470 },
    contentRect: { x: 90, y: 160, width: 1100, height: 470 },
    referenceLayoutId: "osb-process",
    referenceLayoutDigest,
    referenceSourceDigest: "sha256:" + "c".repeat(64),
    title: {
      bounds: { x: 90, y: 70, width: 700, height: 60 },
      textStyle: {
        fontFamily: "Arial",
        fontPt: 28,
        fontWeight: 700,
        color: "#17335C",
        alignment: "left",
        lineHeight: 1.15,
        verticalAlignment: "middle",
      },
      text: title,
      lines: [title],
    },
    blocks: [{
      id: "process-block-1",
      kind: "process-step",
      semanticRole: "process-step",
      bounds: { x: 120, y: 210, width: 980, height: 260 },
      shape: "roundRect",
      fill: "#DCECF6",
      stroke: "#0879B7",
      strokeWidth: 1,
      textInsets: { top: 12, right: 14, bottom: 12, left: 14 },
      textStyle: {
        fontFamily: "Arial",
        fontPt: 16,
        fontWeight: 500,
        color: "#17335C",
        alignment: "left",
        lineHeight: 1.2,
        verticalAlignment: "middle",
      },
      fontPt: 16,
      zIndex: 10,
      text: body,
    }],
    decorations: [],
    semanticRolesUsed: ["process-step"],
    documentMetadata: null,
  };
  grammar.digest = `sha256:${sha256CanonicalJson(grammar)}`;
  const layoutRolesUsed = [grammar.referenceLayoutId, grammar.archetype, grammar.slideRole];
  return {
    slideId: `slide:retained-${pageNumber}`,
    layoutId: "layout:standard",
    values: {
      "standard-title": title,
      "standard-body": body,
      __bodyGrammar: JSON.stringify(grammar),
      __resolvedSnapshotEvidence: JSON.stringify({
        schemaVersion: "resolved-snapshot-evidence/v1",
        resolvedTemplateId: "artifact-template-ai/osb-proposal-1-29",
        resolvedReferenceLayoutDigest: referenceLayoutDigest,
        resolvedCompositionDigest: "sha256:" + "d".repeat(64),
        resolvedRuleDigest: "sha256:" + "e".repeat(64),
        pageNumber,
        slideCount,
        semanticRolesUsed: grammar.semanticRolesUsed,
        layoutRolesUsed,
        bodyGrammarDigest: grammar.digest,
      }),
    },
  };
}

test("canonical digest normalizes integral numbers and Unicode NFC", () => {
  assert.equal(
    sha256CanonicalJson({ text: "e\u0301", value: 76.0 }),
    createHash("sha256").update('{"text":"é","value":76}', "utf8").digest("hex"),
  );
});

test("authenticated POST returns fully buffered PPTX and read-back receipt", async (t) => {
  const { POST } = await loadRoute();
  const previous = process.env.AUTHORED_VNEXT_OOXML_WORKER_TOKEN;
  process.env.AUTHORED_VNEXT_OOXML_WORKER_TOKEN = SECRET;
  t.after(() => {
    if (previous === undefined) delete process.env.AUTHORED_VNEXT_OOXML_WORKER_TOKEN;
    else process.env.AUTHORED_VNEXT_OOXML_WORKER_TOKEN = previous;
  });
  const request = new NextRequest("http://localhost/api/internal/ooxml-vnext", {
    method: "POST",
    headers: { authorization: `Bearer ${SECRET}`, "content-type": "application/json" },
    body: JSON.stringify(envelope()),
  });
  const response = await POST(request);
  const body = await response.json();
  assert.equal(response.status, 200);
  assert.equal(response.headers.get("cache-control"), "no-store");
  assert.equal(body.status, "SUCCEEDED");
  assert.equal(body.operationId, "operation-test-1");
  assert.equal(body.fencingToken, 7);
  assert.equal(body.receipt.validation, "candidate-reopened");
  assert.equal(body.receipt.candidateDigestVerified, true);
  assert.equal(body.receipt.package.slideCount, 1);
  assert.equal(body.receipt.preservation.silentLossCount, 0);
  assert.equal(body.receipt.master.logoCount, 1);
  assert.equal(body.receipt.master.pageNumberCount, 1);
  assert.equal(body.receipt.master.fontFileCount, 1);
  assert.equal(body.receipt.master.duplicateFurnitureCount, 0);
  assert.equal(body.receipt.master.fontFilesMode, "REFERENCE_METADATA_VERIFIED");
  const bytes = Buffer.from(body.artifact.contentBase64, "base64");
  assert.equal(bytes.byteLength, body.artifact.byteLength);
  assert.equal(bytes.subarray(0, 2).toString("ascii"), "PK");
});

test("worker read-back preserves resolved snapshot evidence for every page", async () => {
  const retainedSlides = [
    retainedReferenceSlide(1, 2),
    retainedReferenceSlide(2, 2),
  ];
  const input = envelope({ slides: retainedSlides });
  input.digests.specSha256 = sha256CanonicalJson(retainedSlides);

  const result = await executeWorkerExport(input, {
    signal: new AbortController().signal,
  });

  assert.deepEqual(
    result.receipt.slides.map((slide) => ({
      pageNumber: slide.pageNumber,
      slideCount: slide.slideCount,
      referenceLayoutDigest: slide.referenceLayoutDigest,
      resolvedCompositionDigest: slide.resolvedCompositionDigest,
      resolvedRuleDigest: slide.resolvedRuleDigest,
      semanticRolesUsed: slide.semanticRolesUsed,
      layoutRolesUsed: slide.layoutRolesUsed,
      bodyGrammarDigest: slide.bodyGrammarDigest,
    })),
    retainedSlides.map((slide, index) => {
      const evidence = JSON.parse(slide.values.__resolvedSnapshotEvidence);
      return {
        pageNumber: index + 1,
        slideCount: retainedSlides.length,
        referenceLayoutDigest: evidence.resolvedReferenceLayoutDigest,
        resolvedCompositionDigest: evidence.resolvedCompositionDigest,
        resolvedRuleDigest: evidence.resolvedRuleDigest,
        semanticRolesUsed: evidence.semanticRolesUsed,
        layoutRolesUsed: evidence.layoutRolesUsed,
        bodyGrammarDigest: evidence.bodyGrammarDigest,
      };
    }),
  );
});

test("POST fails closed for missing or wrong bearer without leaking configuration", async (t) => {
  const { POST } = await loadRoute();
  const previous = process.env.AUTHORED_VNEXT_OOXML_WORKER_TOKEN;
  process.env.AUTHORED_VNEXT_OOXML_WORKER_TOKEN = SECRET;
  t.after(() => {
    if (previous === undefined) delete process.env.AUTHORED_VNEXT_OOXML_WORKER_TOKEN;
    else process.env.AUTHORED_VNEXT_OOXML_WORKER_TOKEN = previous;
  });
  for (const authorization of [undefined, "Bearer wrong-secret"]) {
    const headers = { "content-type": "application/json" };
    if (authorization) headers.authorization = authorization;
    const response = await POST(
      new NextRequest("http://localhost/api/internal/ooxml-vnext", {
        method: "POST",
        headers,
        body: JSON.stringify(envelope()),
      }),
    );
    const text = await response.text();
    assert.equal(response.status, 401);
    assert.doesNotMatch(text, /worker-test-secret/);
    assert.doesNotMatch(text, /OOXML_VNEXT_INTERNAL_SECRET/);
  }
});

test("master, spec, asset manifest, font manifest, and package mismatches fail closed", async () => {
  const mismatchCases = [
    ["masterSha256", "MASTER_DIGEST_MISMATCH"],
    ["specSha256", "SPEC_DIGEST_MISMATCH"],
    ["assetManifestSha256", "ASSET_MANIFEST_DIGEST_MISMATCH"],
    ["fontManifestSha256", "FONT_MANIFEST_DIGEST_MISMATCH"],
  ];
  for (const [field, code] of mismatchCases) {
    const input = envelope();
    input.digests[field] = "0".repeat(64);
    await assert.rejects(
      executeWorkerExport(input, { signal: new AbortController().signal }),
      (error) => error instanceof OoxmlWorkerError && error.code === code,
    );
  }
  const packageMismatch = envelope();
  packageMismatch.digests.packageSha256 = "0".repeat(64);
  await assert.rejects(
    executeWorkerExport(packageMismatch, { signal: new AbortController().signal }),
    (error) => error instanceof OoxmlWorkerError && error.code === "PACKAGE_DIGEST_MISMATCH",
  );
});

test("verified PNG assets are embedded and survive worker read-back", async () => {
  const bytes = await readFile(path.join(testDirectory, "../../../../public/Logo.png"));
  const asset = {
    assetId: "asset:test",
    mediaType: "image/png",
    sha256: createHash("sha256").update(bytes).digest("hex"),
    bytesBase64: bytes.toString("base64"),
  };
  const input = envelope({ assets: [asset] });
  input.master.furniture.find(({ kind }) => kind === "logo").assetId = asset.assetId;
  input.master.furniture.find(({ kind }) => kind === "logo").assetSha256 = asset.sha256;
  input.digests.masterSha256 = sha256CanonicalJson(input.master);
  input.digests.assetManifestSha256 = sha256CanonicalJson([
    { assetId: asset.assetId, mediaType: asset.mediaType, sha256: asset.sha256 },
  ]);
  const result = await executeWorkerExport(input, { signal: new AbortController().signal });
  assert.equal(result.status, "SUCCEEDED");
  assert.equal(result.receipt.master.logoCount, 1);
});

test("version drift, duplicate IDs, and caller-controlled paths are rejected", async () => {
  const cases = [
    [envelope({ schemaVersion: "authored-vnext-ooxml-worker/v2" }), "UNSUPPORTED_SCHEMA_VERSION"],
    [envelope({ destinationPath: "../../outside.pptx" }), "INVALID_ENVELOPE"],
    [envelope({ slides: [slides[0], slides[0]] }), "INVALID_ENVELOPE"],
  ];
  for (const [input, code] of cases) {
    await assert.rejects(
      executeWorkerExport(input, { signal: new AbortController().signal }),
      (error) => error instanceof OoxmlWorkerError && error.code === code,
    );
  }
});

test("worker wire constants and fields stay aligned with the shared JSON schema", async () => {
  const schemaPath = path.join(
    testDirectory,
    "../../../../../../contracts/authored_vnext/export/v1/export-contract.schema.json",
  );
  const schema = JSON.parse(await readFile(schemaPath, "utf8"));
  const request = schema.$defs.WorkerRequestEnvelope;
  const response = schema.$defs.WorkerResponseEnvelope;
  assert.equal(request.properties.schemaVersion.const, WORKER_SCHEMA_VERSION);
  assert.equal(request.properties.operation.const, WORKER_OPERATION);
  assert.deepEqual(
    new Set(request.required),
    new Set(["schemaVersion", "operation", "operationId", "fencingToken", "master", "slides", "assets", "digests"]),
  );
  assert.equal(response.properties.status.const, "SUCCEEDED");
  assert.deepEqual(
    new Set(response.required),
    new Set(["schemaVersion", "operation", "operationId", "fencingToken", "status", "artifact", "receipt"]),
  );
  assert.equal(schema.$defs.WorkerAsset.properties.bytesBase64.contentEncoding, "base64");
  assert.equal(schema.$defs.BufferedArtifact.properties.contentBase64.contentEncoding, "base64");
  assert.equal(schema.$defs.WorkerReadBackReceipt.properties.candidateDigestVerified.const, true);
  assert.equal(schema.$defs.WorkerMasterReceipt.properties.duplicateFurnitureCount.const, 0);
  assert.equal(schema.$defs.WorkerError.properties.failedClosed.const, true);
});

test("expired deadlines and unsupported resume receipts fail closed", async () => {
  const cases = [
    [envelope({ deadline: "2000-01-01T00:00:00.000Z" }), "WORKER_DEADLINE_EXCEEDED"],
    [envelope({ resumeFromReceipt: { artifactSha256: "0".repeat(64) } }), "RESUME_UNSUPPORTED"],
  ];
  for (const [input, code] of cases) {
    await assert.rejects(
      executeWorkerExport(input, { signal: new AbortController().signal }),
      (error) => error instanceof OoxmlWorkerError && error.code === code,
    );
  }
});

test("rich CJK runs preserve bold, fallback fonts, emoji, and valid combining marks", async () => {
  const richSlides = structuredClone(slides);
  const richTitle = "\uD55C\uAD6D\uC5B4 \uC804\uB7B5 \u{1F9D1}\u200D\u{1F4BB}\u0301";
  richSlides[0].values["standard-title"] = {
    runs: [{ text: richTitle, bold: true, fontFamily: "Malgun Gothic" }],
  };
  richSlides[0].values["standard-body"] = {
    runs: [
      { text: "Latin fallback", italic: true, fontFamily: "Arial" },
    ],
  };
  const input = envelope({ slides: richSlides });
  input.digests.specSha256 = sha256CanonicalJson(richSlides);
  const result = await executeWorkerExport(input, { signal: new AbortController().signal });
  const entries = readPptxArchive(Buffer.from(result.artifact.contentBase64, "base64"));
  const slideXml = entries.get("ppt/slides/slide1.xml").toString("utf8");
  assert.ok(slideXml.includes(richTitle));
  assert.match(slideXml, /b="1"/u);
  assert.match(slideXml, /i="1"/u);
  assert.match(slideXml, /<a:ea typeface="Malgun Gothic"\/>/u);
  assert.match(slideXml, /<a:latin typeface="Arial"\/>/u);
});

test("non-NFC text, unpinned fonts, and standard titles needing promotion fail closed", async () => {
  const cases = [
    ["TEXT_NOT_CANONICAL", { "standard-title": "Cafe\u0301", "standard-body": "Body" }],
    ["FONT_FALLBACK_NOT_PINNED", {
      "standard-title": { runs: [{ text: "Title", fontFamily: "Unknown Corporate Font" }] },
      "standard-body": "Body",
    }],
    ["TITLE_LAYOUT_PROMOTION_REQUIRED", {
      "standard-title": "First\nSecond\nThird",
      "standard-body": "Body",
    }],
  ];
  for (const [code, values] of cases) {
    const candidateSlides = [{ ...slides[0], values }];
    const input = envelope({ slides: candidateSlides });
    input.digests.specSha256 = sha256CanonicalJson(candidateSlides);
    await assert.rejects(
      executeWorkerExport(input, { signal: new AbortController().signal }),
      (error) => error instanceof OoxmlWorkerError && error.code === code,
    );
  }
});

test("pre-aborted work is cancelled and worker-owned temporary directories are removed", async (t) => {
  const root = await mkdtemp(path.join(os.tmpdir(), "ooxml-worker-test-root-"));
  t.after(() => rm(root, { recursive: true, force: true }));
  const controller = new AbortController();
  controller.abort();
  await assert.rejects(
    executeWorkerExport(envelope(), { signal: controller.signal, temporaryRoot: root }),
    (error) => error instanceof OoxmlWorkerError && error.code === "WORKER_CANCELLED",
  );
  assert.deepEqual(await readdir(root), []);

  const result = await executeWorkerExport(envelope(), {
    signal: new AbortController().signal,
    temporaryRoot: root,
  });
  assert.equal(result.status, "SUCCEEDED");
  assert.deepEqual(await readdir(root), []);
  assert.doesNotMatch(JSON.stringify(result), /presenton-ooxml-worker|candidate\.pptx/);
});
