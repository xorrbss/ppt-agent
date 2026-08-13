import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import test from "node:test";

import * as publicationModule from "./publication.ts";
import { publishBufferedArtifact } from "./publication.ts";
import { patchTextSlice } from "./text-slice-patcher.ts";

const XML_PREFIX = `<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<p:sld xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main">
  <p:cSld><p:spTree>
    <p:sp>
      <p:nvSpPr><p:cNvPr id="2" name="placeholder:title" descr="vnext:role=title;contract=v1"/><p:cNvSpPr/><p:nvPr><p:ph type="title" idx="10"/><p:extLst><p:ext uri="{7D786E45-7874-4D65-7461-646174613A31}"><v:placeholder xmlns:v="urn:presenton:ooxml-vnext:semantic-metadata:v1" role="title" idx="10"/></p:ext></p:extLst></p:nvPr></p:nvSpPr>
      <p:spPr><a:xfrm><a:off x="1" y="2"/></a:xfrm></p:spPr>
      <p:txBody><a:bodyPr/><a:p><a:r><a:rPr lang="ko-KR" b="1"/><a:t>`;
const XML_SUFFIX = `</a:t></a:r><a:endParaRPr lang="ko-KR"/></a:p></p:txBody>
    </p:sp>
    <p:sp>
      <p:nvSpPr><p:cNvPr id="3" name="body"/><p:cNvSpPr/><p:nvPr/></p:nvSpPr>
      <p:txBody><a:bodyPr/><a:p><a:r><a:rPr i="1"/><a:t>Untouched</a:t></a:r></a:p></p:txBody>
    </p:sp>
  </p:spTree></p:cSld>
</p:sld>`;

function digest(value) {
  return createHash("sha256").update(value).digest("hex");
}

function slideXml(text = "분기 &amp; plan") {
  return Buffer.from(`${XML_PREFIX}${text}${XML_SUFFIX}`, "utf8");
}

test("targeted patch changes only one approved a:t byte slice", () => {
  const source = slideXml();
  const replacement = `새 <계획> & "검증"`;
  const result = patchTextSlice(source, {
    partName: "ppt/slides/slide1.xml",
    selector: { kind: "placeholder", idx: 10, role: "title" },
    expected: { text: "분기 & plan", sha256: digest("분기 & plan") },
    replacement,
  });

  const beforeStart = source.indexOf(Buffer.from("분기 &amp; plan"));
  const beforeEnd = beforeStart + Buffer.byteLength("분기 &amp; plan");
  const after = Buffer.from(result.bytes);
  const encodedReplacement = Buffer.from('새 &lt;계획&gt; &amp; "검증"');
  assert.deepEqual(after.subarray(0, beforeStart), source.subarray(0, beforeStart));
  assert.deepEqual(after.subarray(beforeStart, beforeStart + encodedReplacement.length), encodedReplacement);
  assert.deepEqual(after.subarray(beforeStart + encodedReplacement.length), source.subarray(beforeEnd));
  assert.match(after.toString("utf8"), /<a:rPr lang="ko-KR" b="1"\/><a:t>새/);
  assert.match(after.toString("utf8"), /<a:rPr i="1"\/><a:t>Untouched<\/a:t>/);
  assert.equal(result.receipt.outsideSliceDiffBytes, 0);
  assert.equal(result.receipt.outsideSliceUnchanged, true);
  assert.equal(result.receipt.sourceSha256, digest(source));
  assert.equal(result.receipt.resultSha256, digest(after));
});

test("plain run detection does not confuse a closing a:rPr with a closing a:r", () => {
  const source = Buffer.from(
    slideXml("Nested formatting").toString("utf8").replace(
      '<a:rPr lang="ko-KR" b="1"/>',
      '<a:rPr lang="ko-KR" b="1"><a:solidFill><a:schemeClr val="tx1"/></a:solidFill></a:rPr>',
    ),
    "utf8",
  );
  const result = patchTextSlice(source, {
    partName: "ppt/slides/slide1.xml",
    selector: { kind: "placeholder", idx: 10, role: "title" },
    expected: { text: "Nested formatting" },
    replacement: "Patched safely",
  });
  const patched = Buffer.from(result.bytes).toString("utf8");
  assert.match(
    patched,
    /<a:rPr lang="ko-KR" b="1"><a:solidFill><a:schemeClr val="tx1"\/><\/a:solidFill><\/a:rPr><a:t>Patched safely<\/a:t>/,
  );
  assert.equal(result.receipt.outsideSliceDiffBytes, 0);
});

test("element selector supports an explicit zero-based repeated-text occurrence", () => {
  const source = Buffer.from(
    `${XML_PREFIX}Same</a:t></a:r><a:r><a:rPr/><a:t>Same${XML_SUFFIX}`,
    "utf8",
  );
  assert.throws(
    () =>
      patchTextSlice(source, {
        partName: "ppt/slides/slide1.xml",
        selector: { kind: "element", id: 2 },
        expected: { text: "Same" },
        replacement: "Changed",
      }),
    /expected-text-node-count-2/,
  );
  const result = patchTextSlice(source, {
    partName: "ppt/slides/slide1.xml",
    selector: { kind: "element", id: "2", occurrence: 1 },
    expected: { text: "Same" },
    replacement: "Changed",
  });
  assert.match(Buffer.from(result.bytes).toString("utf8"), /<a:t>Same<\/a:t>.*<a:t>Changed<\/a:t>/s);
});

test("patcher rejects role mismatch, hash mismatch, and unsafe part names", () => {
  const source = slideXml();
  const base = {
    partName: "ppt/slides/slide1.xml",
    selector: { kind: "placeholder", idx: 10, role: "title" },
    expected: { text: "분기 & plan" },
    replacement: "Changed",
  };
  assert.throws(
    () => patchTextSlice(source, { ...base, selector: { ...base.selector, role: "body" } }),
    /selector-shape-count-0/,
  );
  const mismatchedMetadata = Buffer.from(
    source.toString("utf8").replace('role="title" idx="10"', 'role="title" idx="11"'),
  );
  assert.throws(
    () => patchTextSlice(mismatchedMetadata, base),
    /selector-shape-count-0/,
  );
  assert.throws(
    () => patchTextSlice(source, { ...base, expected: { ...base.expected, sha256: "0".repeat(64) } }),
    /expected-text-sha256-mismatch/,
  );
  assert.throws(
    () => patchTextSlice(source, { ...base, partName: "../ppt/slides/slide1.xml" }),
    /part-name-is-not-an-approved-text-part/,
  );
});

test("patcher rejects cross-run, field, whitespace-attribute, and non-UTF8 risks", () => {
  const crossRun = Buffer.from(
    `${XML_PREFIX}Hello </a:t></a:r><a:r><a:rPr/><a:t>world${XML_SUFFIX}`,
  );
  assert.throws(
    () =>
      patchTextSlice(crossRun, {
        partName: "ppt/slides/slide1.xml",
        selector: { kind: "element", id: 2 },
        expected: { text: "Hello world" },
        replacement: "Changed",
      }),
    /expected-text-node-count-0/,
  );

  const field = slideXml().toString("utf8")
    .replace("<a:r><a:rPr lang=\"ko-KR\" b=\"1\"/>", "<a:fld id=\"{A}\"><a:rPr/>")
    .replace("</a:t></a:r><a:endParaRPr", "</a:t></a:fld><a:endParaRPr");
  assert.throws(
    () =>
      patchTextSlice(Buffer.from(field), {
        partName: "ppt/slides/slide1.xml",
        selector: { kind: "element", id: 2 },
        expected: { text: "분기 & plan" },
        replacement: "Changed",
      }),
    /field-text-is-not-mutable/,
  );

  assert.throws(
    () =>
      patchTextSlice(slideXml(), {
        partName: "ppt/slides/slide1.xml",
        selector: { kind: "element", id: 2 },
        expected: { text: "분기 & plan" },
        replacement: " leading",
      }),
    /replacement-whitespace-would-require-xml-space-preserve/,
  );
  assert.throws(
    () =>
      patchTextSlice(Uint8Array.from([0xff, 0xfe, 0xfd]), {
        partName: "ppt/slides/slide1.xml",
        selector: { kind: "element", id: 2 },
        expected: { text: "x" },
        replacement: "y",
      }),
    /source-is-not-utf8/,
  );
});

test("xml:space preserve permits an exact whitespace-bearing content patch", () => {
  const source = Buffer.from(`${XML_PREFIX.replace("<a:t>", '<a:t xml:space="preserve">')} old ${XML_SUFFIX}`);
  const result = patchTextSlice(source, {
    partName: "ppt/slideLayouts/slideLayout1.xml",
    selector: { kind: "placeholder", idx: 10, role: "title" },
    expected: { text: " old " },
    replacement: " new ",
  });
  assert.match(Buffer.from(result.bytes).toString("utf8"), /<a:t xml:space="preserve"> new <\/a:t>/);
  assert.equal(result.receipt.outsideSliceDiffBytes, 0);
});

test("buffered publication validates, digests, fsyncs, renames, and reads back", async () => {
  const directory = await fs.mkdtemp(path.join(os.tmpdir(), "ooxml-publication-"));
  try {
    const destinationPath = path.join(directory, "deck.pptx");
    const bytes = Buffer.from("buffered-pptx-candidate");
    let validatedPath;
    const receipt = await publishBufferedArtifact({
      destinationPath,
      bytes,
      expectedDestinationSha256: null,
      validateAndReopen: async (candidate) => {
        validatedPath = candidate.path;
        assert.equal(path.dirname(candidate.path), directory);
        assert.equal(path.extname(candidate.path), ".pptx");
        assert.equal(candidate.byteLength, bytes.length);
        assert.equal(candidate.expectedSha256, digest(bytes));
        assert.deepEqual(await fs.readFile(candidate.path), bytes);
      },
    });

    assert.deepEqual(await fs.readFile(destinationPath), bytes);
    assert.equal(receipt.sha256, digest(bytes));
    assert.equal(receipt.publishedReadBackSha256, digest(bytes));
    assert.equal(receipt.atomicRename, true);
    assert.equal(receipt.replacedExisting, false);
    assert.equal(receipt.validation, "reopened");
    assert.equal(receipt.destinationPath, destinationPath);
    assert.equal(await fs.stat(validatedPath).then(() => true, () => false), false);
    assert.deepEqual(
      (await fs.readdir(directory)).filter((name) => name.includes(".candidate")),
      [],
    );
  } finally {
    await fs.rm(directory, { recursive: true, force: true });
  }
});

test("atomic replacement honors the expected destination digest", async () => {
  const directory = await fs.mkdtemp(path.join(os.tmpdir(), "ooxml-publication-"));
  try {
    const destinationPath = path.join(directory, "deck.pptx");
    const oldBytes = Buffer.from("old");
    const newBytes = Buffer.from("new");
    await fs.writeFile(destinationPath, oldBytes);
    const receipt = await publishBufferedArtifact({
      destinationPath,
      bytes: newBytes,
      expectedDestinationSha256: digest(oldBytes),
      validateAndReopen: async ({ path: candidatePath }) => {
        assert.deepEqual(await fs.readFile(candidatePath), newBytes);
      },
    });
    assert.equal(receipt.replacedExisting, true);
    assert.deepEqual(await fs.readFile(destinationPath), newBytes);
  } finally {
    await fs.rm(directory, { recursive: true, force: true });
  }
});

test("failed validation and candidate tampering never publish or damage the destination", async () => {
  const directory = await fs.mkdtemp(path.join(os.tmpdir(), "ooxml-publication-"));
  try {
    const destinationPath = path.join(directory, "deck.pptx");
    const oldBytes = Buffer.from("old");
    await fs.writeFile(destinationPath, oldBytes);

    await assert.rejects(
      publishBufferedArtifact({
        destinationPath,
        bytes: Buffer.from("candidate"),
        expectedDestinationSha256: digest(oldBytes),
        validateAndReopen: async () => {
          throw new Error("reopen failed");
        },
      }),
      /reopen failed/,
    );
    assert.deepEqual(await fs.readFile(destinationPath), oldBytes);

    await assert.rejects(
      publishBufferedArtifact({
        destinationPath,
        bytes: Buffer.from("candidate"),
        expectedDestinationSha256: digest(oldBytes),
        validateAndReopen: async ({ path: candidatePath }) => {
          await fs.writeFile(candidatePath, "tampered");
        },
      }),
      /candidate-changed-during-validation/,
    );
    assert.deepEqual(await fs.readFile(destinationPath), oldBytes);
    assert.deepEqual(
      (await fs.readdir(directory)).filter((name) => name.includes(".candidate")),
      [],
    );
  } finally {
    await fs.rm(directory, { recursive: true, force: true });
  }
});

test("publication has no streaming API and rejects non-byte input", async () => {
  assert.deepEqual(Object.keys(publicationModule).sort(), ["publishBufferedArtifact"]);
  const directory = await fs.mkdtemp(path.join(os.tmpdir(), "ooxml-publication-"));
  try {
    await assert.rejects(
      publishBufferedArtifact({
        destinationPath: path.join(directory, "deck.pptx"),
        bytes: "not buffered bytes",
        validateAndReopen: async () => {},
      }),
      /artifact-must-be-buffered-bytes/,
    );
  } finally {
    await fs.rm(directory, { recursive: true, force: true });
  }
});
