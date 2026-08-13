#!/usr/bin/env node
import { createHash } from "node:crypto";
import { mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import process from "node:process";
import { fileURLToPath } from "node:url";

import {
  readPptxArchive,
  writePptxArchive,
} from "../../authored-hybrid/pptx-archive.ts";
import { createOoxmlMasterCompilerPort } from "../adapter.ts";
import {
  EXISTING_90_DECK_MASTER_CONTRACT,
  EXISTING_90_LOGO_SHA256,
  existing90LogoAsset,
} from "./existing-90-contract.ts";

const OFFICE_REL_NS =
  "http://schemas.openxmlformats.org/officeDocument/2006/relationships";

function insertBeforeClosing(xml, closing, fragment) {
  if (!xml.includes(closing)) throw new Error(`fixture source lacks ${closing}`);
  return xml.replace(closing, `${fragment}${closing}`);
}

export async function buildExisting90Fixture(destinationPath) {
  const destination = path.resolve(destinationPath);
  const temporaryDirectory = await mkdtemp(
    path.join(os.tmpdir(), "ooxml-vnext-existing-90-recipe-"),
  );
  try {
    const generatedPath = path.join(temporaryDirectory, "generated-90.pptx");
    const logoBytes = await readFile(new URL("../../../public/Logo.png", import.meta.url));
    const port = createOoxmlMasterCompilerPort();
    const created = port.createGenerated(
      EXISTING_90_DECK_MASTER_CONTRACT,
      [existing90LogoAsset(logoBytes)],
    );
    const slideRecipes = [
      {
        layoutId: "layout:cover",
        values: (number) => ({
          "cover-title": `기업 AI 전환 로드맵 ${number} 🚀`,
          "cover-body": "한국어·日本語·简体中文 / e\u0301 / 𠀀 fallback",
        }),
      },
      {
        layoutId: "layout:section",
        values: (number) => ({
          "section-title": `제${number}장 전략과 실행 統合`,
          "section-body": "CJK 혼합 섹션 — 품질·보안·운영 ✅",
        }),
      },
      {
        layoutId: "layout:standard",
        values: (number) => ({
          "standard-title": `표준 레이아웃 ${number}: 고객 가치와 성과`,
          "standard-body": "서울–東京–上海 협업, emoji 👩🏽‍💻, combining Å é, fallback 𠀀",
        }),
      },
      {
        layoutId: "layout:long-title",
        values: (number) => {
          const lineCount = (Math.floor((number - 1) / 4) % 3) + 1;
          const titleLines = [
            `긴 제목 ${number}: 생성형 AI 운영모델`,
            "한국어와 日本語·中文을 함께 검증",
            "세 번째 줄의 emoji 🧭 및 e\u0301 fallback 𠀀",
          ];
          return {
            "long-title": titleLines.slice(0, lineCount).join("\n"),
            "long-body": `1–3줄 제목 자동 적합성 사례 ${number}`,
          };
        },
      },
    ];
    for (let index = 0; index < 90; index += 1) {
      const recipe = slideRecipes[index % slideRecipes.length];
      const number = index + 1;
      port.materializeSlide(
        created.document,
        {
          slideId: `slide:existing-${String(number).padStart(3, "0")}`,
          layoutId: recipe.layoutId,
          values: recipe.values(number),
        },
        port.readBack(created.document).precondition,
      );
    }
    await port.saveBuffered(
      created.document,
      generatedPath,
      port.readBack(created.document).precondition,
      null,
    );
    const entries = readPptxArchive(await readFile(generatedPath));
    entries.set(
      "customXml/item1.xml",
      Buffer.from(
        '<?xml version="1.0" encoding="UTF-8"?><fixture xmlns="urn:presenton:fixture">opaque-custom-xml</fixture>',
      ),
    );
    entries.set(
      "ppt/embeddings/oleObject1.bin",
      Buffer.from("PRESENTON-OPAQUE-OLE-FIXTURE\0", "utf8"),
    );
    entries.set(
      "[Content_Types].xml",
      Buffer.from(
        insertBeforeClosing(
          entries.get("[Content_Types].xml").toString("utf8"),
          "</Types>",
          '<Default Extension="bin" ContentType="application/vnd.openxmlformats-officedocument.oleObject"/>',
        ),
      ),
    );
    const slideRelationships = "ppt/slides/_rels/slide1.xml.rels";
    entries.set(
      slideRelationships,
      Buffer.from(
        insertBeforeClosing(
          entries.get(slideRelationships).toString("utf8"),
          "</Relationships>",
          `<Relationship Id="rIdOpaqueOle" Type="${OFFICE_REL_NS}/oleObject" Target="../embeddings/oleObject1.bin"/>` +
            `<Relationship Id="rIdFixtureMail" Type="${OFFICE_REL_NS}/hyperlink" Target="mailto:fixture@example.invalid" TargetMode="External"/>`,
        ),
      ),
    );
    const bytes = writePptxArchive(entries);
    await writeFile(destination, bytes, { flag: "wx" });
    const imported = port.importPackage(bytes);
    return {
      archiveSha256: createHash("sha256").update(bytes).digest("hex"),
      archiveByteLength: bytes.length,
      packageDigest: imported.packageDigest,
      relationshipDigest: imported.relationshipDigest,
      closureDigest: imported.closureDigest,
      slideCount: imported.slideCount,
      layoutCount: imported.layoutCount,
      partCount: imported.partCount,
      logoSha256: EXISTING_90_LOGO_SHA256,
      layoutIds: slideRecipes.map(({ layoutId }) => layoutId),
      warnings: imported.externalRelationshipWarnings,
      quarantined: imported.classification === "QUARANTINED",
    };
  } finally {
    await rm(temporaryDirectory, { recursive: true, force: true });
  }
}

if (process.argv[1] === fileURLToPath(import.meta.url)) {
  const destination = process.argv[2];
  if (!destination) throw new Error("Usage: build-existing-90.mjs <new-output.pptx>");
  console.log(JSON.stringify(await buildExisting90Fixture(destination), null, 2));
}
