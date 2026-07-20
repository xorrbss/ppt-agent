import assert from "node:assert/strict";
import test from "node:test";

import { readPptxArchive, writePptxArchive } from "./pptx-archive.ts";

test("PPTX ZIP round-trip preserves binary and XML entries", () => {
  const entries = new Map([
    ["[Content_Types].xml", Buffer.from("<Types/>")],
    ["ppt/slides/slide1.xml", Buffer.from("<p:sld>한글</p:sld>")],
    ["ppt/media/image1.png", Buffer.from([0, 1, 2, 3, 4])],
  ]);
  const roundTrip = readPptxArchive(writePptxArchive(entries));
  assert.deepEqual([...roundTrip.keys()], [...entries.keys()]);
  for (const [name, bytes] of entries) assert.deepEqual(roundTrip.get(name), bytes);
});

test("archive writer rejects traversal and duplicate-number constraints remain bounded", () => {
  assert.throws(
    () => writePptxArchive(new Map([["../escape", Buffer.from("x")]])),
    /unsafe entry path/
  );
  assert.throws(() => readPptxArchive(Buffer.from("not-a-zip")), /truncated/);
});
