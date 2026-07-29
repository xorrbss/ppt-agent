import assert from "node:assert/strict";
import test from "node:test";

import {
  extractAuthoredSlideDom,
  resolveAuthoredHybridChromeExecutable,
} from "./index.ts";

function textElement(slide) {
  return slide.elements.find(
    (element) =>
      "text" in element &&
      element.text?.plainText.includes("PowerPoint")
  );
}

test("optional PowerPoint layout extraction maps the measured CSS face first", async (t) => {
  const chromeExecutable = await resolveAuthoredHybridChromeExecutable();
  if (!chromeExecutable) {
    if (process.env.CI === "true") {
      assert.fail("CI must provide Chrome/Chromium for font layout extraction");
    }
    t.skip("Chrome/Chromium is unavailable");
    return;
  }
  const html = `<!doctype html><html><head><style>
    html,body{width:1280px;height:720px;margin:0;overflow:hidden}
    p{position:absolute;left:80px;top:80px;width:420px;margin:0;
      font:400 34px/46px "Noto Sans KR",sans-serif}
  </style></head><body><p>PowerPoint 한국어 줄바꿈 측정</p></body></html>`;
  const options = { chromeExecutable, timeoutMs: 20_000 };
  const source = await extractAuthoredSlideDom(html, options);
  const mapped = await extractAuthoredSlideDom(html, {
    ...options,
    fontLayoutMode: "powerpoint",
  });
  const sourceText = textElement(source);
  const mappedText = textElement(mapped);

  assert.ok(sourceText);
  assert.ok(mappedText);
  assert.equal(sourceText.id, mappedText.id);
  assert.equal(sourceText.text.plainText, mappedText.text.plainText);
  assert.equal(sourceText.text.style.fontFamilies[0], "Noto Sans KR");
  assert.equal(mappedText.text.style.fontFamilies[0], "Malgun Gothic");
  assert.ok(
    mappedText.text.style.fontFamilies.indexOf("Noto Sans KR") >
      mappedText.text.style.fontFamilies.indexOf("Malgun Gothic")
  );
});
