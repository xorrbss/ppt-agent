import fs from "node:fs/promises";
import path from "node:path";
import process from "node:process";
import { createHash } from "node:crypto";
import { chromium } from "playwright";
import { Presentation, PresentationFile } from "@oai/artifact-tool";

import {
  readPptxArchive,
  writePptxArchive,
} from "../../../../servers/nextjs/lib/authored-hybrid/pptx-archive.ts";

const ROOT = path.dirname(new URL(import.meta.url).pathname.replace(/^\/(?:[A-Za-z]:)/, (m) => m.slice(1)));
const INPUT_PATH = path.join(ROOT, "canonical-input.json");

function parseArgs(argv) {
  const result = { out: path.join(ROOT, "output") };
  for (let i = 0; i < argv.length; i += 1) {
    if (argv[i] === "--out" && argv[i + 1]) {
      result.out = path.resolve(argv[i + 1]);
      i += 1;
      continue;
    }
    throw new Error(`Unknown or incomplete argument: ${argv[i]}`);
  }
  return result;
}

function stable(value) {
  if (Array.isArray(value)) return value.map(stable);
  if (value && typeof value === "object") {
    return Object.fromEntries(
      Object.keys(value)
        .sort()
        .map((key) => [key, stable(value[key])])
    );
  }
  return value;
}

function stableJson(value) {
  return JSON.stringify(stable(value));
}

function digest(value) {
  return `sha256:${createHash("sha256").update(stableJson(value), "utf8").digest("hex")}`;
}

function without(object, key) {
  const clone = structuredClone(object);
  delete clone[key];
  return clone;
}

function assert(condition, message) {
  if (!condition) throw new Error(message);
}

function validateCanonical(contract) {
  assert(contract.contractVersion === "deckmaster-contract/v1", "unexpected contractVersion");
  assert(contract.deckMaster?.slideSizePx?.width === 1280, "slide width must be 1280px");
  assert(contract.deckMaster?.slideSizePx?.height === 720, "slide height must be 720px");
  assert(contract.slides?.length === 1, "vertical slice must contain exactly one slide");

  const master = contract.deckMaster;
  const slide = contract.slides[0];
  const layouts = new Map(master.layouts.map((layout) => [layout.id, layout]));
  assert(layouts.has("layout:standard-title"), "standard layout is required");
  assert(layouts.has("layout:long-title"), "long-title layout is required");
  assert(slide.layoutId === "layout:long-title", "PoC slide must use long-title layout");
  assert(slide.title.lines.length >= 1 && slide.title.lines.length <= 3, "title must use 1-3 canonical lines");
  assert(slide.title.lines.join(" ") === slide.title.text, "title.text must equal canonical lines joined with spaces");
  assert(slide.ownership.locked === false && slide.ownership.modelWritable === true, "slide must remain model writable");

  for (const item of master.furniture) {
    assert(item.ownership.locked === true, `${item.id} must be locked`);
    assert(item.ownership.modelWritable === false, `${item.id} must not be model writable`);
  }
  for (const layout of master.layouts) {
    for (const placeholder of layout.placeholders) {
      assert(placeholder.ownership.locked === false, `${placeholder.id} must be unlocked`);
      assert(placeholder.ownership.modelWritable === true, `${placeholder.id} must be model writable`);
    }
  }

  const leadingForbidden = /^[\)\]\}\u3001\u3002\uff0c\uff0e\uff01\uff1f\uff09\u3009\u300b\u300d\u300f\u3011\u3015\u3017\u3019\u301b]/u;
  const trailingForbidden = /[\(\[\{\uff08\u3008\u300a\u300c\u300e\u3010\u3014\u3016\u3018\u301a]$/u;
  for (const entry of contract.stressCorpus) {
    assert(entry.expectedLines.length >= 1 && entry.expectedLines.length <= 3, `${entry.id} must have 1-3 lines`);
    for (const line of entry.expectedLines) {
      assert(!leadingForbidden.test(line), `${entry.id} violates leading-closing kinsoku`);
      assert(!trailingForbidden.test(line), `${entry.id} violates trailing-opening kinsoku`);
    }
  }

  const fontManifestExpected = digest(without(master.fontManifest, "digest"));
  assert(master.fontManifest.digest === fontManifestExpected, `font manifest digest mismatch; expected ${fontManifestExpected}`);
  for (const script of ["latin", "Hang", "Jpan", "Hans", "Hant"]) {
    assert(master.fontManifest.themeScripts?.[script] === master.fontManifest.windowsFallback, `theme script ${script} must use the Windows fallback`);
  }
  assert(master.fontManifest.windowsResolved?.state === "installed-system", "Windows font state must be installed-system");
  assert(master.fontManifest.windowsResolved?.cloud === false, "PoC may not depend on a cloud font");
  assert(master.fontManifest.windowsResolved?.embedded === false, "PoC records a system font and must not claim embedding");
  const masterExpected = digest(without(master, "digest"));
  assert(master.digest === masterExpected, `master digest mismatch; expected ${masterExpected}`);
  const contentExpected = digest({ title: slide.title, body: slide.body });
  assert(slide.digests.content === contentExpected, `content digest mismatch; expected ${contentExpected}`);
  const specExpected = digest(without(slide, "digests"));
  assert(slide.digests.spec === specExpected, `spec digest mismatch; expected ${specExpected}`);

  return {
    masterDigest: masterExpected,
    fontManifestDigest: fontManifestExpected,
    contentDigest: contentExpected,
    specDigest: specExpected,
    canonicalDigest: digest(contract),
  };
}

function ptToPx(points) {
  return (points * 96) / 72;
}

function shapePosition(bounds) {
  return {
    left: bounds.x,
    top: bounds.y,
    width: bounds.width,
    height: bounds.height,
  };
}

function applyTextStyle(shape, style, fontFamily, color, options = {}) {
  shape.text.style = {
    typeface: fontFamily,
    fontSize: ptToPx(style.fontPt),
    lineSpacing: style.lineHeight,
    color,
    bold: options.bold ?? false,
    alignment: options.alignment ?? "left",
    verticalAlignment: options.verticalAlignment ?? "top",
    wrap: "square",
    autoFit: "none",
    insets: style.marginsPx,
  };
}

function addMasterFurniture(master, contract) {
  const fontFamily = contract.fontManifest.primary;
  for (const item of contract.furniture) {
    if (item.kind === "rect") {
      master.shapes.add({
        geometry: "rect",
        name: item.id,
        position: shapePosition(item.bounds),
        fill: item.fill,
        line: { style: "solid", fill: "none", width: 0 },
      });
      continue;
    }
    if (item.kind === "line") {
      master.shapes.add({
        geometry: "line",
        name: item.id,
        position: shapePosition(item.bounds),
        fill: "none",
        line: { style: "solid", fill: item.stroke, width: 1 },
      });
      continue;
    }
    const text = master.shapes.add({
      geometry: "textbox",
      name: item.id,
      position: shapePosition(item.bounds),
      fill: "none",
      line: { style: "solid", fill: "none", width: 0 },
    });
    text.text = item.text;
    text.text.style = {
      typeface: fontFamily,
      fontSize: item.id.endsWith(":brand") ? ptToPx(11) : ptToPx(9),
      bold: item.id.endsWith(":brand"),
      color: item.fill,
      autoFit: "none",
      wrap: "none",
      insets: { top: 0, right: 0, bottom: 0, left: 0 },
      verticalAlignment: "middle",
    };
  }
}

function addLayout(presentation, master, layoutContract, fontFamily) {
  const layout = presentation.layouts.add(layoutContract.name);
  layout.setParentLayoutId(master.id);
  for (const item of layoutContract.placeholders) {
    const placeholder = layout.placeholders.add({
      type: item.type,
      index: item.index,
      text: item.type === "title" ? "Title" : "Body",
      geometry: "textbox",
      position: shapePosition(item.bounds),
      fill: "none",
      line: { style: "solid", fill: "none", width: 0 },
    });
    applyTextStyle(
      placeholder,
      item.textStyle,
      fontFamily,
      item.type === "title" ? "tx1" : "tx2",
      { bold: item.type === "title", verticalAlignment: item.type === "title" ? "middle" : "top" }
    );
  }
  return layout;
}

function htmlEscape(value) {
  return value
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

function renderHtml(contract, digests) {
  const master = contract.deckMaster;
  const slide = contract.slides[0];
  const layout = master.layouts.find((candidate) => candidate.id === slide.layoutId);
  const titlePlaceholder = layout.placeholders.find((placeholder) => placeholder.type === "title");
  const bodyPlaceholder = layout.placeholders.find((placeholder) => placeholder.type === "body");
  const cssFonts = [master.fontManifest.primary, ...master.fontManifest.fallbacks]
    .map((font) => (font.includes(" ") ? `"${font}"` : font))
    .join(", ");
  const furniture = Object.fromEntries(master.furniture.map((item) => [item.id, item]));
  const titleLines = slide.title.lines.map((line) => `<span>${htmlEscape(line)}</span>`).join("\n");
  const embeddedContract = htmlEscape(stableJson(contract));

  return `<!doctype html>
<html lang="ko">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=1280, initial-scale=1">
<title>DeckMasterContract vertical slice</title>
<style>
  * { box-sizing: border-box; }
  html, body { margin: 0; width: 1280px; height: 720px; overflow: hidden; background: ${master.theme.colors.bg1}; }
  body { font-family: ${cssFonts}; color: ${master.theme.colors.tx1}; }
  .slide { position: relative; width: 1280px; height: 720px; background: ${master.theme.colors.bg1}; overflow: hidden; }
  .master { position: absolute; pointer-events: none; }
  .top-rail { left: 0; top: 0; width: 1280px; height: 10px; background: ${furniture["furniture:top-rail"].fill}; }
  .brand { left: 76px; top: 35px; width: 420px; height: 28px; color: ${furniture["furniture:brand"].fill}; font-size: 14.6667px; line-height: 28px; font-weight: 700; letter-spacing: .04em; }
  .footer-rule { left: 76px; top: 672px; width: 1128px; height: 1px; background: ${furniture["furniture:footer-rule"].stroke}; }
  .footer { left: 76px; top: 684px; width: 650px; height: 22px; color: ${furniture["furniture:footer"].fill}; font-size: 12px; line-height: 22px; }
  .placeholder { position: absolute; overflow: hidden; }
  .title { left: ${titlePlaceholder.bounds.x}px; top: ${titlePlaceholder.bounds.y}px; width: ${titlePlaceholder.bounds.width}px; height: ${titlePlaceholder.bounds.height}px; padding: ${titlePlaceholder.textStyle.marginsPx.top}px ${titlePlaceholder.textStyle.marginsPx.right}px ${titlePlaceholder.textStyle.marginsPx.bottom}px ${titlePlaceholder.textStyle.marginsPx.left}px; display: flex; flex-direction: column; justify-content: center; font-size: ${ptToPx(titlePlaceholder.textStyle.fontPt)}px; line-height: ${titlePlaceholder.textStyle.lineHeight}; font-weight: 700; letter-spacing: -.025em; }
  .title span { display: block; white-space: nowrap; }
  .body { left: ${bodyPlaceholder.bounds.x}px; top: ${bodyPlaceholder.bounds.y}px; width: ${bodyPlaceholder.bounds.width}px; height: ${bodyPlaceholder.bounds.height}px; padding: ${bodyPlaceholder.textStyle.marginsPx.top}px ${bodyPlaceholder.textStyle.marginsPx.right}px ${bodyPlaceholder.textStyle.marginsPx.bottom}px ${bodyPlaceholder.textStyle.marginsPx.left}px; font-size: ${ptToPx(bodyPlaceholder.textStyle.fontPt)}px; line-height: ${bodyPlaceholder.textStyle.lineHeight}; color: ${master.theme.colors.tx2}; }
  .accent { position: absolute; left: ${furniture["furniture:title-accent"].bounds.x}px; top: ${furniture["furniture:title-accent"].bounds.y}px; width: ${furniture["furniture:title-accent"].bounds.width}px; height: ${furniture["furniture:title-accent"].bounds.height}px; border-radius: 3px; background: ${furniture["furniture:title-accent"].fill}; }
</style>
</head>
<body>
<main class="slide" data-contract-digest="${digests.canonicalDigest}" data-layout="${slide.layoutId}">
  <div class="master top-rail" data-owner="master" data-id="furniture:top-rail"></div>
  <div class="master brand" data-owner="master" data-id="furniture:brand">${htmlEscape(furniture["furniture:brand"].text)}</div>
  <div class="master footer-rule" data-owner="master" data-id="furniture:footer-rule"></div>
  <div class="master footer" data-owner="master" data-id="furniture:footer">${htmlEscape(furniture["furniture:footer"].text)}</div>
  <section class="placeholder title" data-owner="slide-placeholder" data-id="placeholder:long-title">${titleLines}</section>
  <div class="master accent" data-owner="master" data-id="furniture:title-accent" aria-hidden="true"></div>
  <section class="placeholder body" data-owner="slide-placeholder" data-id="placeholder:long-body">${htmlEscape(slide.body)}</section>
</main>
<script id="canonical-contract" type="application/json">${embeddedContract}</script>
</body>
</html>`;
}

async function writeBlob(filePath, blob) {
  await fs.writeFile(filePath, new Uint8Array(await blob.arrayBuffer()));
}

async function sha256File(filePath) {
  const bytes = await fs.readFile(filePath);
  return createHash("sha256").update(bytes).digest("hex");
}

async function validateWindowsFontFiles(fontManifest) {
  for (const file of fontManifest.windowsResolved.files) {
    const stats = await fs.stat(file.path);
    assert(stats.isFile(), `font manifest path is not a file: ${file.path}`);
    assert(stats.size === file.sizeBytes, `font size mismatch: ${file.path}`);
    assert(await sha256File(file.path) === file.sha256, `font SHA-256 mismatch: ${file.path}`);
  }
}

async function renderHtmlPng(htmlPath, pngPath, metricsPath) {
  const executablePath = process.env.CHROME_PATH || "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe";
  const browser = await chromium.launch({ executablePath, headless: true });
  try {
    const page = await browser.newPage({
      viewport: { width: 1280, height: 720 },
      deviceScaleFactor: 1,
    });
    await page.goto(new URL(`file:///${htmlPath.replaceAll("\\\\", "/")}`).href, { waitUntil: "load" });
    await page.evaluate(() => document.fonts.ready);
    const metrics = await page.evaluate(() => {
      const rect = (element) => {
        const bounds = element.getBoundingClientRect();
        return {
          x: bounds.x,
          y: bounds.y,
          width: bounds.width,
          height: bounds.height,
          right: bounds.right,
          bottom: bounds.bottom,
          clientWidth: element.clientWidth,
          clientHeight: element.clientHeight,
          scrollWidth: element.scrollWidth,
          scrollHeight: element.scrollHeight,
        };
      };
      const title = document.querySelector(".title");
      const body = document.querySelector(".body");
      const slide = document.querySelector(".slide");
      return {
        viewport: { width: innerWidth, height: innerHeight, devicePixelRatio },
        fontsReady: document.fonts.status === "loaded",
        resolvedBodyFont: getComputedStyle(document.body).fontFamily,
        slide: rect(slide),
        title: {
          ...rect(title),
          lines: [...title.querySelectorAll("span")].map((span) => ({ text: span.textContent, ...rect(span) })),
        },
        body: rect(body),
      };
    });
    await fs.writeFile(metricsPath, `${JSON.stringify(metrics, null, 2)}\n`, "utf8");
    await page.screenshot({ path: pngPath, type: "png" });
  } finally {
    await browser.close();
  }
}

const XML_HEADER = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>';
const NS = {
  a: "http://schemas.openxmlformats.org/drawingml/2006/main",
  p: "http://schemas.openxmlformats.org/presentationml/2006/main",
  r: "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
};

function xmlEscape(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&apos;");
}

function pxToEmu(value) {
  return Math.round(value * 9525);
}

function rgb(value) {
  return value.replace(/^#/, "").toUpperCase();
}

function colorXml(value) {
  const name = String(value);
  if (/^#[0-9A-Fa-f]{6}$/.test(name)) return `<a:srgbClr val="${rgb(name)}"/>`;
  return `<a:schemeClr val="${xmlEscape(name)}"/>`;
}

function xfrmXml(bounds) {
  return `<a:xfrm><a:off x="${pxToEmu(bounds.x)}" y="${pxToEmu(bounds.y)}"/><a:ext cx="${pxToEmu(bounds.width)}" cy="${pxToEmu(bounds.height)}"/></a:xfrm>`;
}

function spPrXml(item) {
  const isLine = item.kind === "line";
  const fill = item.fill
    ? `<a:solidFill>${colorXml(item.fill)}</a:solidFill>`
    : "<a:noFill/>";
  const line = isLine || item.stroke
    ? `<a:ln w="12700"><a:solidFill>${colorXml(item.stroke ?? "#000000")}</a:solidFill><a:prstDash val="solid"/></a:ln>`
    : "<a:ln><a:noFill/></a:ln>";
  return `<p:spPr>${xfrmXml(item.bounds)}${isLine ? '<a:prstGeom prst="line"><a:avLst/></a:prstGeom>' : '<a:prstGeom prst="rect"><a:avLst/></a:prstGeom>'}${fill}${line}</p:spPr>`;
}

function bodyPrXml(style = null, anchor = "t") {
  const margins = style?.marginsPx ?? { top: 0, right: 0, bottom: 0, left: 0 };
  return `<a:bodyPr wrap="square" lIns="${pxToEmu(margins.left)}" tIns="${pxToEmu(margins.top)}" rIns="${pxToEmu(margins.right)}" bIns="${pxToEmu(margins.bottom)}" anchor="${anchor}" rtlCol="0"><a:noAutofit/></a:bodyPr><a:lstStyle/>`;
}

function paragraphXml(text, options) {
  const { fontPt, bold, color, fontFamily, align = "l", lineHeight = 1, lang = "ko-KR" } = options;
  const size = Math.round(fontPt * 100);
  const linePercent = Math.round(lineHeight * 100000);
  return `<a:p><a:pPr algn="${align}" eaLnBrk="1" latinLnBrk="0" hangingPunct="0"><a:lnSpc><a:spcPct val="${linePercent}"/></a:lnSpc></a:pPr><a:r><a:rPr lang="${lang}" sz="${size}" b="${bold ? 1 : 0}" dirty="0"><a:solidFill>${colorXml(color)}</a:solidFill><a:latin typeface="${xmlEscape(fontFamily)}"/><a:ea typeface="${xmlEscape(fontFamily)}"/><a:cs typeface="${xmlEscape(fontFamily)}"/></a:rPr><a:t>${xmlEscape(text)}</a:t></a:r><a:endParaRPr lang="${lang}" sz="${size}"/></a:p>`;
}

function textBoxXml(item, id, fontFamily) {
  const fontPt = item.id.endsWith(":brand") ? 11 : 9;
  const paragraph = paragraphXml(item.text, {
    fontPt,
    bold: item.id.endsWith(":brand"),
    color: item.fill,
    fontFamily,
    lineHeight: 1,
  });
  return `<p:sp xmlns:a="${NS.a}"><p:nvSpPr><p:cNvPr id="${id}" name="${xmlEscape(item.id)}" descr="owner=master;locked=1;modelWritable=0"/><p:cNvSpPr txBox="1"><a:spLocks noGrp="1" noMove="1" noResize="1" noEditPoints="1"/></p:cNvSpPr><p:nvPr/></p:nvSpPr><p:spPr>${xfrmXml(item.bounds)}<a:prstGeom prst="rect"><a:avLst/></a:prstGeom><a:noFill/><a:ln><a:noFill/></a:ln></p:spPr><p:txBody>${bodyPrXml(null, "ctr")}${paragraph}</p:txBody></p:sp>`;
}

function furnitureXml(item, id, fontFamily) {
  if (item.kind === "text") return textBoxXml(item, id, fontFamily);
  return `<p:sp xmlns:a="${NS.a}"><p:nvSpPr><p:cNvPr id="${id}" name="${xmlEscape(item.id)}" descr="owner=master;locked=1;modelWritable=0"/><p:cNvSpPr><a:spLocks noGrp="1" noMove="1" noResize="1" noEditPoints="1"/></p:cNvSpPr><p:nvPr/></p:nvSpPr>${spPrXml(item)}</p:sp>`;
}

function placeholderXml(item, id, fontFamily, text = null, owner = "layout") {
  const style = item.textStyle;
  const paragraphs = (text === null ? [item.type === "title" ? "Title" : "Body"] : text.split("\n"))
    .map((line) => paragraphXml(line, {
      fontPt: style.fontPt,
      bold: item.type === "title",
      color: item.type === "title" ? "tx1" : "tx2",
      fontFamily,
      lineHeight: style.lineHeight,
    }))
    .join("");
  const phType = item.type === "title" ? "title" : item.type === "body" ? "body" : "obj";
  return `<p:sp xmlns:a="${NS.a}"><p:nvSpPr><p:cNvPr id="${id}" name="${xmlEscape(item.id)}" descr="owner=${owner};locked=0;modelWritable=1"/><p:cNvSpPr txBox="1"/><p:nvPr><p:ph type="${phType}" idx="${item.index}"/></p:nvPr></p:nvSpPr><p:spPr>${xfrmXml(item.bounds)}<a:prstGeom prst="rect"><a:avLst/></a:prstGeom><a:noFill/><a:ln><a:noFill/></a:ln></p:spPr><p:txBody>${bodyPrXml(style, item.type === "title" ? "ctr" : "t")}${paragraphs}</p:txBody></p:sp>`;
}

function packageRelationships(items) {
  return `${XML_HEADER}<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">${items.map((item) => `<Relationship Id="${item.id}" Type="${item.type}" Target="${item.target}"${item.targetMode ? ` TargetMode="${item.targetMode}"` : ""}/>`).join("")}</Relationships>`;
}

function themeXml(master) {
  const colors = master.theme.colors;
  const schemeOrder = ["dk1", "lt1", "dk2", "lt2", "accent1", "accent2", "accent3", "accent4", "accent5", "accent6", "hlink", "folHlink"];
  const scheme = schemeOrder.map((name) => `<a:${name}>${colorXml(colors[name])}</a:${name}>`).join("");
  const font = xmlEscape(master.fontManifest.primary);
  return `${XML_HEADER}<a:theme xmlns:a="${NS.a}" name="${xmlEscape(master.theme.name)}"><a:themeElements><a:clrScheme name="${xmlEscape(master.theme.name)}">${scheme}</a:clrScheme><a:fontScheme name="Authored vNext Fonts"><a:majorFont><a:latin typeface="${font}"/><a:ea typeface="${font}"/><a:cs typeface="${font}"/><a:font script="Hang" typeface="${font}"/><a:font script="Jpan" typeface="${font}"/><a:font script="Hans" typeface="${font}"/><a:font script="Hant" typeface="${font}"/></a:majorFont><a:minorFont><a:latin typeface="${font}"/><a:ea typeface="${font}"/><a:cs typeface="${font}"/><a:font script="Hang" typeface="${font}"/><a:font script="Jpan" typeface="${font}"/><a:font script="Hans" typeface="${font}"/><a:font script="Hant" typeface="${font}"/></a:minorFont></a:fontScheme><a:fmtScheme name="Authored vNext Format"><a:fillStyleLst><a:solidFill><a:schemeClr val="phClr"/></a:solidFill><a:solidFill><a:schemeClr val="accent1"/></a:solidFill><a:solidFill><a:schemeClr val="accent2"/></a:solidFill></a:fillStyleLst><a:lnStyleLst><a:ln w="12700"><a:solidFill><a:schemeClr val="phClr"/></a:solidFill><a:prstDash val="solid"/></a:ln><a:ln w="25400"><a:solidFill><a:schemeClr val="phClr"/></a:solidFill><a:prstDash val="solid"/></a:ln><a:ln w="38100"><a:solidFill><a:schemeClr val="phClr"/></a:solidFill><a:prstDash val="solid"/></a:ln></a:lnStyleLst><a:effectStyleLst><a:effectStyle><a:effectLst/></a:effectStyle><a:effectStyle><a:effectLst/></a:effectStyle><a:effectStyle><a:effectLst/></a:effectStyle></a:effectStyleLst><a:bgFillStyleLst><a:solidFill><a:schemeClr val="phClr"/></a:solidFill><a:solidFill><a:schemeClr val="bg1"/></a:solidFill><a:solidFill><a:schemeClr val="bg2"/></a:solidFill></a:bgFillStyleLst></a:fmtScheme></a:themeElements><a:objectDefaults/><a:extraClrSchemeLst/></a:theme>`;
}

function buildPptxPackage(contract) {
  const master = contract.deckMaster;
  const slide = contract.slides[0];
  const font = master.fontManifest.primary;
  const layoutContracts = master.layouts;
  const longIndex = layoutContracts.findIndex((item) => item.id === slide.layoutId);
  assert(longIndex >= 0, "slide layout was not found");
  const entries = new Map();
  const commonStart = `<p:cSld><p:spTree><p:nvGrpSpPr><p:cNvPr id="1" name=""/><p:cNvGrpSpPr/><p:nvPr/></p:nvGrpSpPr><p:grpSpPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="0" cy="0"/><a:chOff x="0" y="0"/><a:chExt cx="0" cy="0"/></a:xfrm></p:grpSpPr>`;
  const commonEnd = "</p:spTree></p:cSld>";

  entries.set("[Content_Types].xml", Buffer.from(`${XML_HEADER}<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/ppt/presentation.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.presentation.main+xml"/><Override PartName="/ppt/slideMasters/slideMaster1.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.slideMaster+xml"/>${layoutContracts.map((_, index) => `<Override PartName="/ppt/slideLayouts/slideLayout${index + 1}.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.slideLayout+xml"/>`).join("")}<Override PartName="/ppt/slides/slide1.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.slide+xml"/><Override PartName="/ppt/theme/theme1.xml" ContentType="application/vnd.openxmlformats-officedocument.theme+xml"/><Override PartName="/docProps/core.xml" ContentType="application/vnd.openxmlformats-package.core-properties+xml"/><Override PartName="/docProps/app.xml" ContentType="application/vnd.openxmlformats-officedocument.extended-properties+xml"/></Types>`, "utf8"));
  entries.set("_rels/.rels", Buffer.from(packageRelationships([
    { id: "rId1", type: `${NS.r}/officeDocument`, target: "ppt/presentation.xml" },
    { id: "rId2", type: "http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties", target: "docProps/core.xml" },
    { id: "rId3", type: `${NS.r}/extended-properties`, target: "docProps/app.xml" },
  ]), "utf8"));
  entries.set("docProps/core.xml", Buffer.from(`${XML_HEADER}<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties" xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:dcterms="http://purl.org/dc/terms/" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"><dc:title>DeckMasterContract vertical slice</dc:title><dc:creator>Presenton authored-vnext experiment</dc:creator><cp:lastModifiedBy>Presenton authored-vnext experiment</cp:lastModifiedBy><dcterms:created xsi:type="dcterms:W3CDTF">2026-08-13T00:00:00Z</dcterms:created><dcterms:modified xsi:type="dcterms:W3CDTF">2026-08-13T00:00:00Z</dcterms:modified></cp:coreProperties>`, "utf8"));
  entries.set("docProps/app.xml", Buffer.from(`${XML_HEADER}<Properties xmlns="http://schemas.openxmlformats.org/officeDocument/2006/extended-properties" xmlns:vt="http://schemas.openxmlformats.org/officeDocument/2006/docPropsVTypes"><Application>Presenton</Application><PresentationFormat>On-screen Show (16:9)</PresentationFormat><Slides>1</Slides><Notes>0</Notes><HiddenSlides>0</HiddenSlides><MMClips>0</MMClips><ScaleCrop>false</ScaleCrop><Company>Presenton</Company><AppVersion>1.0</AppVersion></Properties>`, "utf8"));

  const presentationXml = `${XML_HEADER}<p:presentation xmlns:a="${NS.a}" xmlns:r="${NS.r}" xmlns:p="${NS.p}"><p:sldMasterIdLst><p:sldMasterId id="2147483648" r:id="rId1"/></p:sldMasterIdLst><p:sldIdLst><p:sldId id="256" r:id="rId2"/></p:sldIdLst><p:sldSz cx="12192000" cy="6858000" type="screen16x9"/><p:notesSz cx="6858000" cy="9144000"/><p:kinsoku lang="ko-KR" invalStChars=")]},、。，．？！）》」』】〕〉》】〙〗〟’”｠»" invalEndChars="([{（〈《「『【〔〖〘〚‘“｟«"/><p:defaultTextStyle><a:defPPr><a:defRPr lang="ko-KR"/></a:defPPr></p:defaultTextStyle></p:presentation>`;
  entries.set("ppt/presentation.xml", Buffer.from(presentationXml, "utf8"));
  entries.set("ppt/_rels/presentation.xml.rels", Buffer.from(packageRelationships([
    { id: "rId1", type: `${NS.r}/slideMaster`, target: "slideMasters/slideMaster1.xml" },
    { id: "rId2", type: `${NS.r}/slide`, target: "slides/slide1.xml" },
    { id: "rId3", type: `${NS.r}/theme`, target: "theme/theme1.xml" },
  ]), "utf8"));

  const furniture = master.furniture.map((item, index) => furnitureXml(item, index + 2, font)).join("");
  const masterXml = `${XML_HEADER}<p:sldMaster xmlns:a="${NS.a}" xmlns:r="${NS.r}" xmlns:p="${NS.p}">${commonStart}${furniture}${commonEnd}<p:clrMap accent1="accent1" accent2="accent2" accent3="accent3" accent4="accent4" accent5="accent5" accent6="accent6" bg1="lt1" bg2="lt2" folHlink="folHlink" hlink="hlink" tx1="dk1" tx2="dk2"/><p:sldLayoutIdLst>${layoutContracts.map((_, index) => `<p:sldLayoutId id="${2147483649 + index}" r:id="rId${index + 1}"/>`).join("")}</p:sldLayoutIdLst><p:txStyles><p:titleStyle><a:lvl1pPr algn="l" eaLnBrk="1" latinLnBrk="0" hangingPunct="0"><a:defRPr sz="3600" b="1"><a:solidFill><a:schemeClr val="tx1"/></a:solidFill><a:latin typeface="+mj-lt"/><a:ea typeface="+mj-ea"/></a:defRPr></a:lvl1pPr></p:titleStyle><p:bodyStyle><a:lvl1pPr algn="l" eaLnBrk="1" latinLnBrk="0" hangingPunct="0"><a:defRPr sz="1800"><a:solidFill><a:schemeClr val="tx2"/></a:solidFill><a:latin typeface="+mn-lt"/><a:ea typeface="+mn-ea"/></a:defRPr></a:lvl1pPr></p:bodyStyle><p:otherStyle><a:defPPr><a:defRPr lang="ko-KR"/></a:defPPr></p:otherStyle></p:txStyles></p:sldMaster>`;
  entries.set("ppt/slideMasters/slideMaster1.xml", Buffer.from(masterXml, "utf8"));
  entries.set("ppt/slideMasters/_rels/slideMaster1.xml.rels", Buffer.from(packageRelationships([
    ...layoutContracts.map((_, index) => ({ id: `rId${index + 1}`, type: `${NS.r}/slideLayout`, target: `../slideLayouts/slideLayout${index + 1}.xml` })),
    { id: `rId${layoutContracts.length + 1}`, type: `${NS.r}/theme`, target: "../theme/theme1.xml" },
  ]), "utf8"));

  layoutContracts.forEach((layoutContract, index) => {
    const placeholders = layoutContract.placeholders.map((item, itemIndex) => placeholderXml(item, itemIndex + 2, font)).join("");
    const layoutXml = `${XML_HEADER}<p:sldLayout xmlns:a="${NS.a}" xmlns:r="${NS.r}" xmlns:p="${NS.p}" type="${layoutContract.kind === "standard" ? "titleAndObj" : "cust"}" preserve="1" showMasterSp="1"><p:cSld name="${xmlEscape(layoutContract.name)}"><p:spTree><p:nvGrpSpPr><p:cNvPr id="1" name=""/><p:cNvGrpSpPr/><p:nvPr/></p:nvGrpSpPr><p:grpSpPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="0" cy="0"/><a:chOff x="0" y="0"/><a:chExt cx="0" cy="0"/></a:xfrm></p:grpSpPr>${placeholders}</p:spTree></p:cSld><p:clrMapOvr><a:masterClrMapping/></p:clrMapOvr></p:sldLayout>`;
    entries.set(`ppt/slideLayouts/slideLayout${index + 1}.xml`, Buffer.from(layoutXml, "utf8"));
    entries.set(`ppt/slideLayouts/_rels/slideLayout${index + 1}.xml.rels`, Buffer.from(packageRelationships([{ id: "rId1", type: `${NS.r}/slideMaster`, target: "../slideMasters/slideMaster1.xml" }]), "utf8"));
  });

  const slideLayout = layoutContracts[longIndex];
  const slideTitle = slideLayout.placeholders.find((item) => item.type === "title");
  const slideBody = slideLayout.placeholders.find((item) => item.type === "body");
  const slideShapes = `${placeholderXml(slideTitle, 2, font, slide.title.lines.join("\n"), "slide-placeholder")}${placeholderXml(slideBody, 3, font, slide.body, "slide-placeholder")}`;
  const slideXml = `${XML_HEADER}<p:sld xmlns:a="${NS.a}" xmlns:r="${NS.r}" xmlns:p="${NS.p}" showMasterSp="1"><p:cSld><p:spTree><p:nvGrpSpPr><p:cNvPr id="1" name=""/><p:cNvGrpSpPr/><p:nvPr/></p:nvGrpSpPr><p:grpSpPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="0" cy="0"/><a:chOff x="0" y="0"/><a:chExt cx="0" cy="0"/></a:xfrm></p:grpSpPr>${slideShapes}</p:spTree></p:cSld><p:clrMapOvr><a:masterClrMapping/></p:clrMapOvr></p:sld>`;
  entries.set("ppt/slides/slide1.xml", Buffer.from(slideXml, "utf8"));
  entries.set("ppt/slides/_rels/slide1.xml.rels", Buffer.from(packageRelationships([{ id: "rId1", type: `${NS.r}/slideLayout`, target: `../slideLayouts/slideLayout${longIndex + 1}.xml` }]), "utf8"));
  entries.set("ppt/theme/theme1.xml", Buffer.from(themeXml(master), "utf8"));
  return writePptxArchive(entries);
}

function patchArtifactPptx(bytes, contract) {
  const entries = readPptxArchive(bytes);
  const presentationPath = "ppt/presentation.xml";
  const corePath = "docProps/core.xml";
  const presentationXml = entries.get(presentationPath)?.toString("utf8");
  assert(presentationXml, "artifact PPTX presentation.xml is missing");
  const kinsoku = '<p:kinsoku lang="ko-KR" invalStChars=")]},、。，．？！）》」』】〕〉》】〙〗〟’”｠»" invalEndChars="([{（〈《「『【〔〖〘〚‘“｟«"/>';
  const patched = /<p:kinsoku\b/i.test(presentationXml)
    ? presentationXml.replace(/<p:kinsoku\b[^>]*\/>/i, kinsoku)
    : /<p:defaultTextStyle\b/i.test(presentationXml)
      ? presentationXml.replace(/<p:defaultTextStyle\b/i, `${kinsoku}<p:defaultTextStyle`)
      : presentationXml.replace(/<\/p:presentation>/i, `${kinsoku}</p:presentation>`);
  entries.set(presentationPath, Buffer.from(patched, "utf8"));

  const masterContract = contract.deckMaster;
  const customMasterPath = [...entries.keys()].find((part) =>
    /^ppt\/slideMasters\/slideMaster\d+\.xml$/.test(part) &&
    entries.get(part).toString("utf8").includes(xmlEscape(masterContract.name))
  );
  assert(customMasterPath, "custom artifact-tool master part was not found");
  let masterXml = entries.get(customMasterPath).toString("utf8");
  const furnitureNames = new Set(masterContract.furniture.map((item) => item.id));
  masterXml = masterXml.replace(
    /<p:sp\b[\s\S]*?<\/p:sp>/g,
    (shapeXml) => {
      const name = shapeXml.match(/<p:cNvPr\b[^>]*\bname="([^"]*)"/)?.[1];
      return name && furnitureNames.has(name) ? "" : shapeXml;
    }
  );
  let nextId = Math.max(1, ...[...masterXml.matchAll(/<p:cNvPr\b[^>]*\bid="(\d+)"/g)].map((match) => Number(match[1]))) + 1;
  const canonicalFurniture = masterContract.furniture
    .map((item) => furnitureXml(item, nextId++, masterContract.fontManifest.primary))
    .join("");
  masterXml = masterXml.replace(/<\/p:spTree>/i, `${canonicalFurniture}</p:spTree>`);
  entries.set(customMasterPath, Buffer.from(masterXml, "utf8"));
  const customMasterRelsPath = `${path.posix.dirname(customMasterPath)}/_rels/${path.posix.basename(customMasterPath)}.rels`;
  const customMasterRels = entries.get(customMasterRelsPath)?.toString("utf8") ?? "";
  const themeTarget = [...customMasterRels.matchAll(/<Relationship\b[^>]*\bType="[^"]*\/theme"[^>]*\bTarget="([^"]+)"[^>]*\/>/g)][0]?.[1]
    ?? [...customMasterRels.matchAll(/<Relationship\b[^>]*\bTarget="([^"]+)"[^>]*\bType="[^"]*\/theme"[^>]*\/>/g)][0]?.[1];
  assert(themeTarget, "custom master theme relationship was not found");
  const themePart = themeTarget.startsWith("/") ? themeTarget.slice(1) : path.posix.normalize(path.posix.join(path.posix.dirname(customMasterPath), themeTarget));
  const fallback = xmlEscape(masterContract.fontManifest.windowsFallback);
  const scripts = ["Hang", "Jpan", "Hans", "Hant"].map((script) => `<a:font script="${script}" typeface="${fallback}"/>`).join("");
  let themeXmlValue = entries.get(themePart)?.toString("utf8");
  assert(themeXmlValue, "custom master theme part was not found");
  themeXmlValue = themeXmlValue
    .replace(/<a:majorFont>[\s\S]*?<\/a:majorFont>/i, `<a:majorFont><a:latin typeface="${fallback}"/><a:ea typeface="${fallback}"/><a:cs typeface="${fallback}"/>${scripts}</a:majorFont>`)
    .replace(/<a:minorFont>[\s\S]*?<\/a:minorFont>/i, `<a:minorFont><a:latin typeface="${fallback}"/><a:ea typeface="${fallback}"/><a:cs typeface="${fallback}"/>${scripts}</a:minorFont>`);
  entries.set(themePart, Buffer.from(themeXmlValue, "utf8"));

  for (const layoutContract of masterContract.layouts) {
    const layoutPart = [...entries.keys()].find((part) =>
      /^ppt\/slideLayouts\/slideLayout\d+\.xml$/.test(part) &&
      entries.get(part).toString("utf8").includes(`name="${xmlEscape(layoutContract.name)}"`)
    );
    assert(layoutPart, `artifact-tool layout part not found: ${layoutContract.name}`);
    let layoutXml = entries.get(layoutPart).toString("utf8");
    const present = new Set(
      [...layoutXml.matchAll(/<p:ph\b([^>]*)\/>/g)].map((match) => {
        const type = match[1].match(/\btype="([^"]+)"/)?.[1] ?? "obj";
        const index = match[1].match(/\bidx="([^"]+)"/)?.[1] ?? "0";
        return `${type}:${index}`;
      })
    );
    let layoutNextId = Math.max(1, ...[...layoutXml.matchAll(/<p:cNvPr\b[^>]*\bid="(\d+)"/g)].map((match) => Number(match[1]))) + 1;
    const missing = layoutContract.placeholders
      .filter((item) => !present.has(`${item.type === "content" ? "obj" : item.type}:${item.index}`))
      .map((item) => placeholderXml(item, layoutNextId++, masterContract.fontManifest.primary))
      .join("");
    if (missing) {
      layoutXml = layoutXml.replace(/<\/p:spTree>/i, `${missing}</p:spTree>`);
      entries.set(layoutPart, Buffer.from(layoutXml, "utf8"));
    }
  }

  for (const part of [...entries.keys()].filter((item) => /^ppt\/(?:slides|slideLayouts)\/[^/]+\.xml$/.test(item))) {
    const xml = entries.get(part).toString("utf8").replace(
      /<a:pPr(?![^>]*\beaLnBrk=)/g,
      '<a:pPr eaLnBrk="1" latinLnBrk="0" hangingPunct="0"'
    );
    entries.set(part, Buffer.from(xml, "utf8"));
  }
  if (entries.has(corePath)) {
    const core = entries.get(corePath).toString("utf8")
      .replace(/<dcterms:created\b[^>]*>[\s\S]*?<\/dcterms:created>/i, '<dcterms:created xsi:type="dcterms:W3CDTF">2026-08-13T00:00:00Z</dcterms:created>')
      .replace(/<dcterms:modified\b[^>]*>[\s\S]*?<\/dcterms:modified>/i, '<dcterms:modified xsi:type="dcterms:W3CDTF">2026-08-13T00:00:00Z</dcterms:modified>');
    entries.set(corePath, Buffer.from(core, "utf8"));
  }
  return writePptxArchive(entries);
}

async function buildArtifactPptx(contract, pptxPath, facadeFactsPath) {
  const masterContract = contract.deckMaster;
  const slideSpec = contract.slides[0];
  const fontFamily = masterContract.fontManifest.primary;
  const presentation = Presentation.create({ slideSize: { width: 1280, height: 720 } });
  presentation.theme.colorScheme = {
    name: masterContract.theme.name,
    themeColors: masterContract.theme.colors,
  };
  const master = presentation.masters.add(masterContract.name);
  master.setColorMap({
    bg1: "lt1",
    tx1: "dk1",
    bg2: "lt2",
    tx2: "dk2",
    accent1: "accent1",
    accent2: "accent2",
    accent3: "accent3",
    accent4: "accent4",
    accent5: "accent5",
    accent6: "accent6",
    hlink: "hlink",
    folHlink: "folHlink",
  });
  master.background.fill = "bg1";
  addMasterFurniture(master, masterContract);

  const layouts = new Map();
  for (const layoutContract of masterContract.layouts) {
    layouts.set(layoutContract.id, addLayout(presentation, master, layoutContract, fontFamily));
  }
  const layoutContract = masterContract.layouts.find((item) => item.id === slideSpec.layoutId);
  const slide = presentation.slides.add();
  slide.setLayout(layouts.get(slideSpec.layoutId));
  const title = slide.placeholders.getItem("title");
  title.text = slideSpec.title.lines.join("\n");
  applyTextStyle(title, layoutContract.placeholders.find((item) => item.type === "title").textStyle, fontFamily, "tx1", { bold: true, verticalAlignment: "middle" });
  const body = slide.placeholders.getItem("body");
  body.text = slideSpec.body;
  applyTextStyle(body, layoutContract.placeholders.find((item) => item.type === "body").textStyle, fontFamily, "tx2", { verticalAlignment: "top" });
  slide.speakerNotes.textFrame.setText([
    "[Sources]",
    "Unicode Standard Annex #14, Unicode Line Breaking Algorithm: https://www.unicode.org/reports/tr14/",
    "Microsoft Learn, Structure of a PresentationML document: https://learn.microsoft.com/office/open-xml/presentation/structure-of-a-presentationml-document",
  ]);
  slide.speakerNotes.setVisible(false);

  const pptx = await PresentationFile.exportPptx(presentation);
  const tempPath = `${pptxPath}.artifact-tool.tmp.pptx`;
  await pptx.save(tempPath);
  const tempInspectPath = `${tempPath}.inspect.ndjson`;
  const finalBytes = patchArtifactPptx(await fs.readFile(tempPath), contract);
  await fs.writeFile(pptxPath, finalBytes);
  try {
    const finalInspectPath = `${pptxPath}.artifact-tool.inspect.ndjson`;
    await fs.rm(finalInspectPath, { force: true });
    await fs.rename(tempInspectPath, finalInspectPath);
  } catch (error) {
    if (error?.code !== "ENOENT") throw error;
  }
  await fs.rm(tempPath, { force: true });
  await fs.writeFile(facadeFactsPath, `${JSON.stringify({
    artifactToolVersion: "2.8.43",
    master: { id: master.id, name: master.name, elementNames: master.elements.map((item) => item.name) },
    layouts: [...layouts.entries()].map(([semanticId, item]) => ({ semanticId, artifactId: item.id, name: item.name, parentLayoutId: item.parentLayoutId })),
    slide: { id: slide.id, layoutId: slide.layoutId, titlePlaceholder: title.placeholder, bodyPlaceholder: body.placeholder },
  }, null, 2)}\n`, "utf8");
}

async function main() {
  const args = parseArgs(process.argv.slice(2));
  await fs.mkdir(args.out, { recursive: true });
  const contract = JSON.parse(await fs.readFile(INPUT_PATH, "utf8"));
  const digests = validateCanonical(contract);
  await validateWindowsFontFiles(contract.deckMaster.fontManifest);
  const masterContract = contract.deckMaster;
  const slideSpec = contract.slides[0];
  const fontFamily = masterContract.fontManifest.primary;

  const htmlPath = path.join(args.out, "vertical-slice.html");
  const pngPath = path.join(args.out, "vertical-slice.preview.png");
  const pptxPath = path.join(args.out, "vertical-slice.pptx");
  const browserMetricsPath = path.join(args.out, "browser-metrics.json");
  const facadeFactsPath = path.join(args.out, "artifact-facade-facts.json");
  const inspectPath = path.join(args.out, "artifact-inspect.ndjson");
  const layoutPath = path.join(args.out, "slide.layout.json");

  await fs.writeFile(htmlPath, renderHtml(contract, digests), "utf8");
  await renderHtmlPng(htmlPath, pngPath, browserMetricsPath);
  const canonicalLayoutEvidence = {
    slideSizePx: masterContract.slideSizePx,
    layout: masterContract.layouts.find((item) => item.id === slideSpec.layoutId),
    titleLines: slideSpec.title.lines,
    titleLineCount: slideSpec.title.lines.length,
  };
  await fs.writeFile(layoutPath, `${JSON.stringify(canonicalLayoutEvidence, null, 2)}\n`, "utf8");
  await fs.writeFile(inspectPath, `${stableJson(canonicalLayoutEvidence)}\n`, "utf8");
  await buildArtifactPptx(contract, pptxPath, facadeFactsPath);
  const pptxBytes = await fs.readFile(pptxPath);
  const packageReadback = readPptxArchive(pptxBytes);
  const customMasterPart = [...packageReadback.keys()].find((part) =>
    /^ppt\/slideMasters\/slideMaster\d+\.xml$/.test(part) &&
    packageReadback.get(part).toString("utf8").includes(xmlEscape(masterContract.name))
  );
  assert(customMasterPart, "manifest could not resolve the custom master part");
  const actualLayoutParts = masterContract.layouts.map((layoutContract) => {
    const layoutPart = [...packageReadback.keys()].find((part) =>
      /^ppt\/slideLayouts\/slideLayout\d+\.xml$/.test(part) &&
      packageReadback.get(part).toString("utf8").includes(`name="${xmlEscape(layoutContract.name)}"`)
    );
    assert(layoutPart, `manifest could not resolve layout part: ${layoutContract.name}`);
    return layoutPart;
  });
  const masterRelsPath = `${path.posix.dirname(customMasterPart)}/_rels/${path.posix.basename(customMasterPart)}.rels`;
  const masterRels = packageReadback.get(masterRelsPath)?.toString("utf8") ?? "";
  const actualThemeTarget = [...masterRels.matchAll(/<Relationship\b[^>]*\bType="[^"]*\/theme"[^>]*\bTarget="([^"]+)"[^>]*\/>/g)][0]?.[1]
    ?? [...masterRels.matchAll(/<Relationship\b[^>]*\bTarget="([^"]+)"[^>]*\bType="[^"]*\/theme"[^>]*\/>/g)][0]?.[1];
  assert(actualThemeTarget, "manifest could not resolve the custom master theme relationship");
  const actualThemePart = actualThemeTarget.startsWith("/")
    ? actualThemeTarget.slice(1)
    : path.posix.normalize(path.posix.join(path.posix.dirname(customMasterPart), actualThemeTarget));
  await fs.writeFile(
    path.join(args.out, "package-readback.json"),
    `${JSON.stringify({ entries: [...packageReadback.keys()].sort(), entryCount: packageReadback.size }, null, 2)}\n`,
    "utf8"
  );

  const manifest = {
    contractVersion: contract.contractVersion,
    canonicalInput: path.basename(INPUT_PATH),
    digests,
    semanticInventory: {
      masters: [{ id: masterContract.id, name: masterContract.name }],
      layouts: masterContract.layouts.map((item) => ({ id: item.id, name: item.name, kind: item.kind })),
      slides: [{ id: slideSpec.id, layoutId: slideSpec.layoutId }],
      furnitureIds: masterContract.furniture.map((item) => item.id),
      placeholderIds: masterContract.layouts.flatMap((layoutContract) => layoutContract.placeholders.map((item) => item.id)),
    },
    packageInventory: {
      masterPart: customMasterPart,
      layoutParts: actualLayoutParts,
      slidePart: "ppt/slides/slide1.xml",
      themePart: actualThemePart,
      relationshipDerived: true,
    },
    files: {
      html: { name: path.basename(htmlPath), sha256: await sha256File(htmlPath) },
      png: { name: path.basename(pngPath), sha256: await sha256File(pngPath) },
      pptx: { name: path.basename(pptxPath), sha256: await sha256File(pptxPath) },
    },
  };
  await fs.writeFile(path.join(args.out, "manifest.json"), `${JSON.stringify(manifest, null, 2)}\n`, "utf8");
}

main().then(
  () => process.exit(0),
  (error) => {
    console.error(error instanceof Error ? error.stack : String(error));
    process.exit(1);
  }
);
