import crypto from "node:crypto";
import fs from "node:fs/promises";
import path from "node:path";

import { readPptxArchive } from "../lib/authored-hybrid/pptx-archive.ts";

function usage() {
  throw new Error(
    "Usage: inspect-pptx-embedded-fonts.mjs <presentation.pptx> [--output <report.json>]"
  );
}

function decodeEotFontData(eot) {
  let cursor = 82;
  for (let index = 0; index < 4; index += 1) {
    const length = eot.readUInt16LE(cursor);
    cursor += 2 + length + (index < 3 ? 2 : 0);
  }
  const sfnt = Buffer.alloc(eot.length - cursor);
  for (let index = 0; index < sfnt.length; index += 1) {
    sfnt[index] = eot[cursor + index] ^ 0x50;
  }
  return { cursor, sfnt };
}

const [inputPath, outputFlag, outputPath, ...extra] = process.argv.slice(2);
if (
  !inputPath ||
  extra.length ||
  (outputFlag !== undefined && outputFlag !== "--output") ||
  (outputFlag === "--output" && !outputPath)
) {
  usage();
}

const absolutePath = path.resolve(inputPath);
const entries = readPptxArchive(await fs.readFile(absolutePath));
const presentation = entries.get("ppt/presentation.xml")?.toString("utf8") ?? "";
const relationships =
  entries.get("ppt/_rels/presentation.xml.rels")?.toString("utf8") ?? "";
const contentTypes = entries.get("[Content_Types].xml")?.toString("utf8") ?? "";

const fontParts = [...entries.entries()]
  .filter(([name]) => name.endsWith(".fntdata"))
  .map(([name, eot]) => {
    const { cursor, sfnt } = decodeEotFontData(eot);
    return {
      name,
      eotBytes: eot.length,
      eotSizeField: eot.readUInt32LE(0),
      fontSizeField: eot.readUInt32LE(4),
      version: `0x${eot.readUInt32LE(8).toString(16).padStart(8, "0")}`,
      flags: `0x${eot.readUInt32LE(12).toString(16).padStart(8, "0")}`,
      fsType: eot.readUInt16LE(32),
      magic: `0x${eot.readUInt16LE(34).toString(16).padStart(4, "0")}`,
      fontDataOffset: cursor,
      sfntMagic: sfnt.subarray(0, 4).toString("hex"),
      sfntBytes: sfnt.length,
      sfntSha256: crypto.createHash("sha256").update(sfnt).digest("hex"),
    };
  });

const runTypefaceCounts = {};
for (const [name, data] of entries) {
  if (!/^ppt\/slides\/slide\d+\.xml$/.test(name)) continue;
  const xml = data.toString("utf8");
  for (const match of xml.matchAll(/\btypeface=["']([^"']+)["']/g)) {
    runTypefaceCounts[match[1]] = (runTypefaceCounts[match[1]] ?? 0) + 1;
  }
}

const report = JSON.stringify(
  {
    path: absolutePath,
    packageEntries: entries.size,
    fontParts,
    embeddedFontLists: presentation.match(/<p:embeddedFontLst\b/g)?.length ?? 0,
    embeddedFontDeclarations:
      presentation.match(/<p:embeddedFont\b/g)?.length ?? 0,
    fontRelationships:
      relationships.match(
        /Type=["']http:\/\/schemas\.openxmlformats\.org\/officeDocument\/2006\/relationships\/font["']/g
      )?.length ?? 0,
    fontContentType:
      /<Default\b[^>]*\bExtension=["']fntdata["'][^>]*\bContentType=["']application\/x-fontdata["'][^>]*\/?>/i.test(
        contentTypes
      ) ||
      /<Default\b[^>]*\bContentType=["']application\/x-fontdata["'][^>]*\bExtension=["']fntdata["'][^>]*\/?>/i.test(
        contentTypes
      ),
    runTypefaceCounts,
  },
  null,
  2
);
console.log(report);
if (outputPath) {
  const absoluteOutputPath = path.resolve(outputPath);
  await fs.mkdir(path.dirname(absoluteOutputPath), { recursive: true });
  await fs.writeFile(absoluteOutputPath, `${report}\n`, "utf8");
}
