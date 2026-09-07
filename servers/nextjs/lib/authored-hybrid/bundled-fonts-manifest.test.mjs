import assert from "node:assert/strict";
import { createHash } from "node:crypto";
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
import { loadBundledBrowserFace } from "./bundled-fonts.ts";
import { resolveFontEmbeddingPlan } from "./font-embedding-policy.ts";
import { resolveScriptAwareTypeface } from "./font-policy.ts";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const NEXTJS_ROOT = path.resolve(HERE, "..", "..");
const PUBLIC_FONTS = path.join(NEXTJS_ROOT, "public", "fonts");

function sha256(data) {
  return createHash("sha256").update(data).digest("hex");
}

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
      const loaded = await loadBundledBrowserFace(family, face);
      assert.ok(loaded);
      assert.equal(sha256(loaded.data), sha256(browser));
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

test("Hangul-capable families are declared once and honoured by the script-aware typeface policy", () => {
  assert.deepEqual([...HANGUL_CAPABLE_BUNDLED_FAMILY_KEYS], ["pretendard"]);
  for (const family of BUNDLED_FONT_FAMILIES.filter((entry) => entry.hangulCapable)) {
    const evidence = [{ typeface: family.family, face: "bold" }];
    const resolved = resolveScriptAwareTypeface("cjk", [family.family, "sans-serif"], {
      embeddedTypefaceFaces: evidence,
      face: "bold",
    });
    assert.equal(resolved.typeface, family.family);
  }
  // A family that is not in the manifest never takes over a Hangul run.
  assert.equal(
    resolveScriptAwareTypeface("cjk", ["Inter", "sans-serif"], {
      embeddedTypefaceFaces: [{ typeface: "Inter", face: "bold" }],
      face: "bold",
    }).typeface,
    "Noto Sans KR",
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

test("the OOXML embedding plan packages the manifest bytes, not a Google or system copy", async () => {
  for (const family of BUNDLED_FONT_FAMILIES) {
    const plan = await resolveFontEmbeddingPlan({
      requested: true,
      families: [family.family],
      strategy: "full",
      allowVariableFonts: false,
    });
    assert.equal(plan.status.eligible, true, JSON.stringify(plan.status.failures));
    assert.deepEqual(
      plan.files.map((file) => file.sourcePath).sort(),
      family.faces.map((face) => face.ooxmlFileName).sort(),
    );
    for (const file of plan.files) {
      assert.equal(file.source, "bundled-open-font");
      const onDisk = await fs.readFile(path.join(PUBLIC_FONTS, family.directory, file.fileName));
      assert.equal(file.sha256, sha256(onDisk), file.fileName);
      assert.ok(
        file.inspection.familyNames.some((name) => normalizeBundledFamilyName(name) === normalizeBundledFamilyName(family.family)),
        `${file.fileName} must carry the manifest family name`,
      );
    }
  }
});
