import assert from "node:assert/strict";
import test from "node:test";

import {
  readPptxArchive,
  writePptxArchive,
  writePptxArchiveAsync,
} from "./pptx-archive.ts";

test("async archive writer preserves deterministic ZIP contents", async () => {
  const entries = new Map<string, Buffer>([
    ["[Content_Types].xml", Buffer.from("<Types/>")],
    ["ppt/slides/slide1.xml", Buffer.from("<p:sld>" + "content".repeat(500) + "</p:sld>")],
    ["ppt/media/image1.png", Buffer.from(Array.from({ length: 1024 }, (_, index) => index % 251))],
  ]);

  const synchronous = writePptxArchive(entries);
  const asynchronous = await writePptxArchiveAsync(entries);
  assert.deepEqual(asynchronous, synchronous);

  const roundTrip = readPptxArchive(asynchronous);
  assert.equal(roundTrip.size, entries.size);
  for (const [name, value] of entries) {
    assert.deepEqual(roundTrip.get(name), value);
  }
});
