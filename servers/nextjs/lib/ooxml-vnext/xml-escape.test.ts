import assert from "node:assert/strict";
import test from "node:test";

import { xmlEscape, xmlUnescape } from "./package-ops.ts";
import { attributes } from "./master-readback-primitives.ts";

test("xmlEscape encodes the five XML specials and nothing else", () => {
  assert.equal(xmlEscape(`a&b<c>d"e'f\n`), "a&amp;b&lt;c&gt;d&quot;e&apos;f\n");
});

test("xmlUnescape decodes named and numeric references in one pass", () => {
  assert.equal(xmlUnescape("a&amp;b&lt;c&gt;d&quot;e&apos;f"), `a&b<c>d"e'f`);
  assert.equal(xmlUnescape("&AMP;&Lt;"), "&<");
  assert.equal(xmlUnescape("&#65;&#x42;&#x1F600;"), "AB\u{1F600}");
  // Double-escaped input decodes exactly one level.
  assert.equal(xmlUnescape("&amp;lt;&amp;amp;"), "&lt;&amp;");
  // Unknown and out-of-range references are left as written.
  assert.equal(xmlUnescape("&nbsp;&#x110000;&"), "&nbsp;&#x110000;&");
  assert.equal(xmlUnescape(xmlEscape("round & <trip> 'ok'")), "round & <trip> 'ok'");
});

test("readback attributes decode through the shared unescape", () => {
  assert.deepEqual(attributes(`<a:t name="x&amp;y" val="&#x41;"/>`), { name: "x&y", val: "A" });
});
