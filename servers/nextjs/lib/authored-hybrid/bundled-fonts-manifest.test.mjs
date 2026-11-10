import assert from "node:assert/strict";
import fs from "node:fs/promises";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

import {
  BUNDLED_FONT_FAMILIES,
  HANGUL_CAPABLE_BUNDLED_FAMILY_KEYS,
  bundledFontFamily,
  bundledFontPublicUrl,
  isBundledFontFamily,
  normalizeBundledFamilyName,
  referencedBundledFamilies,
  resolveBundledFontFace,
} from "./bundled-fonts-manifest.ts";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const NEXTJS_ROOT = path.resolve(HERE, "..", "..");
const PUBLIC_FONTS = path.join(NEXTJS_ROOT, "public", "fonts");

test("manifest lookup hits on every spelling of a bundled family name", () => {
  for (const spelling of ["Pretendard", "pretendard", " PRETENDARD ", "'Pretendard'", '"pretendard"']) {
    assert.equal(bundledFontFamily(spelling)?.family, "Pretendard", spelling);
    assert.equal(isBundledFontFamily(spelling), true, spelling);
  }
  assert.equal(normalizeBundledFamilyName("  \"Noto   Sans KR\" "), "noto sans kr");
});

test("manifest lookup misses instead of substituting another family", () => {
  for (const name of ["Noto Sans KR", "Malgun Gothic", "PretendardX", "Pretendard Variable", "", "sans-serif"]) {
    assert.equal(bundledFontFamily(name), undefined, name);
    assert.equal(isBundledFontFamily(name), false, name);
    assert.equal(resolveBundledFontFace(name, { weight: 400 }), undefined, name);
  }
});

test("face resolution follows the CSS weight matching rule over the shipped faces", () => {
  const face = (weight, style) => resolveBundledFontFace("Pretendard", { weight, style })?.face.browserFileName;
  assert.equal(face(undefined), "Pretendard-Regular.woff2");
  assert.equal(face("normal"), "Pretendard-Regular.woff2");
  assert.equal(face(400), "Pretendard-Regular.woff2");
  assert.equal(face(450), "Pretendard-Regular.woff2");
  assert.equal(face(500), "Pretendard-Regular.woff2");
  assert.equal(face(550), "Pretendard-Bold.woff2");
  assert.equal(face(600), "Pretendard-Bold.woff2");
  assert.equal(face(650), "Pretendard-Bold.woff2");
  assert.equal(face(700), "Pretendard-Bold.woff2");
  assert.equal(face("bold"), "Pretendard-Bold.woff2");
  assert.equal(face(900), "Pretendard-Bold.woff2");
  // No italic is bundled; the upright face is returned and Chromium slants it.
  assert.equal(resolveBundledFontFace("Pretendard", { weight: 700, style: "italic" })?.face.style, "normal");
});

test("every manifest face exists on disk with its licence and the expected container magic", async () => {
  for (const family of BUNDLED_FONT_FAMILIES) {
    const directory = path.join(PUBLIC_FONTS, family.directory);
    await fs.access(path.join(directory, family.licenseFileName));
    assert.equal(family.license, "OFL-1.1");
    for (const face of family.faces) {
      const browser = await fs.readFile(path.join(directory, face.browserFileName));
      assert.equal(browser.subarray(0, 4).toString("latin1"), "wOF2", face.browserFileName);
      const ooxml = await fs.readFile(path.join(directory, face.ooxmlFileName));
      assert.equal(ooxml.readUInt32BE(0), 0x00010000, face.ooxmlFileName);
    }
  }
});

test("same-origin URLs for the browser faces match the editor stylesheet", async () => {
  const globalsCss = await fs.readFile(path.join(NEXTJS_ROOT, "app", "globals.css"), "utf8");
  for (const family of BUNDLED_FONT_FAMILIES) {
    for (const face of family.faces) {
      const url = bundledFontPublicUrl(family, face);
      assert.match(url, /^\/fonts\/[a-z0-9-]+\/[A-Za-z0-9-]+\.woff2$/);
      assert.ok(globalsCss.includes(`url("${url}")`), `globals.css must serve ${url}`);
    }
  }
});

test("Hangul-capable families are declared once", () => {
  assert.deepEqual([...HANGUL_CAPABLE_BUNDLED_FAMILY_KEYS], ["pretendard"]);
  assert.deepEqual(
    BUNDLED_FONT_FAMILIES.filter((entry) => entry.hangulCapable).map((entry) => entry.family),
    ["Pretendard"],
  );
});

test("document references are detected only inside font declarations", () => {
  const names = (html) => referencedBundledFamilies(html).map((family) => family.family);
  assert.deepEqual(names('<style>body{font-family:"Pretendard","Malgun Gothic",sans-serif}</style>'), ["Pretendard"]);
  assert.deepEqual(names('<p style="font: 700 20px Pretendard">x</p>'), ["Pretendard"]);
  assert.deepEqual(names("<p>Pretendard is a font</p>"), []);
  assert.deepEqual(names('<p style="font-family: PretendardX">x</p>'), []);
  assert.deepEqual(names('<p style="font-family: \'Noto Sans KR\'">x</p>'), []);
});
