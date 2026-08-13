#!/usr/bin/env node
import { readFile } from "node:fs/promises";
import path from "node:path";
import process from "node:process";

import { createOoxmlMasterCompilerPort } from "./adapter.ts";
import { normalizeDeckMasterContract } from "./contracts.ts";

const OPERATIONS = new Set(["INSPECT", "PRESERVE_SAVE", "GENERATE_SAVE"]);

function fail(code, message) {
  const error = new Error(message);
  error.code = code;
  throw error;
}

function object(value, name) {
  if (typeof value !== "object" || value === null || Array.isArray(value)) {
    fail("INVALID_RUNNER_REQUEST", `${name} must be an object`);
  }
  return value;
}

function exactKeys(value, allowed, name) {
  const extra = Object.keys(value).filter((key) => !allowed.includes(key));
  if (extra.length) fail("INVALID_RUNNER_REQUEST", `${name} contains unsupported fields: ${extra.join(",")}`);
}

function requiredString(value, name) {
  if (typeof value !== "string" || !value || value.length > 32_768) {
    fail("INVALID_RUNNER_REQUEST", `${name} is invalid`);
  }
  return value;
}

function requiredBinaryString(value, name) {
  if (typeof value !== "string" || !value || value.length > 6_666_672 || !/^[A-Za-z0-9+/]+={0,2}$/.test(value)) {
    fail("INVALID_RUNNER_REQUEST", `${name} is invalid`);
  }
  return value;
}

function absolutePath(value, name) {
  const resolved = path.resolve(requiredString(value, name));
  if (!path.isAbsolute(resolved)) fail("INVALID_RUNNER_REQUEST", `${name} must resolve absolutely`);
  return resolved;
}

function detachReadBack(readBack) {
  const detached = { ...readBack };
  delete detached.document;
  return detached;
}

function detachSave(save, changedParts) {
  return {
    revision: save.revision,
    publication: {
      schemaVersion: save.publication.schemaVersion,
      operation: save.publication.operation,
      sha256: save.publication.sha256,
      byteLength: save.publication.byteLength,
      validation: save.publication.validation,
      candidateDigestVerified: save.publication.candidateDigestVerified,
      atomicRename: save.publication.atomicRename,
      publishedReadBackSha256: save.publication.publishedReadBackSha256,
    },
    changedParts,
    readBack: detachReadBack(save.readBack),
  };
}

function parsePatch(value) {
  const patch = object(value, "patch");
  exactKeys(patch, ["partName", "selector", "expectedText", "replacementText"], "patch");
  const selector = object(patch.selector, "patch.selector");
  exactKeys(selector, ["kind", "idx", "role", "id", "occurrence"], "patch.selector");
  if (selector.kind === "placeholder") {
    if (!Number.isSafeInteger(selector.idx) || selector.idx < 0) {
      fail("INVALID_RUNNER_REQUEST", "placeholder selector idx is invalid");
    }
    if (selector.role !== undefined) requiredString(selector.role, "patch.selector.role");
  } else if (selector.kind === "element") {
    if (!(typeof selector.id === "string" || Number.isSafeInteger(selector.id))) {
      fail("INVALID_RUNNER_REQUEST", "element selector id is invalid");
    }
  } else {
    fail("INVALID_RUNNER_REQUEST", "patch.selector.kind is unsupported");
  }
  if (selector.occurrence !== undefined && (!Number.isSafeInteger(selector.occurrence) || selector.occurrence < 0)) {
    fail("INVALID_RUNNER_REQUEST", "patch.selector.occurrence is invalid");
  }
  return {
    partName: requiredString(patch.partName, "patch.partName"),
    selector,
    expected: { text: typeof patch.expectedText === "string" ? patch.expectedText : fail("INVALID_RUNNER_REQUEST", "patch.expectedText is invalid") },
    replacement: typeof patch.replacementText === "string" ? patch.replacementText : fail("INVALID_RUNNER_REQUEST", "patch.replacementText is invalid"),
  };
}

function parseSlides(value) {
  if (!Array.isArray(value) || value.length > 500) fail("INVALID_RUNNER_REQUEST", "slides is invalid");
  return value.map((slide, index) => {
    const item = object(slide, `slides[${index}]`);
    exactKeys(item, ["slideId", "layoutId", "values"], `slides[${index}]`);
    return {
      slideId: requiredString(item.slideId, `slides[${index}].slideId`),
      layoutId: requiredString(item.layoutId, `slides[${index}].layoutId`),
      values: object(item.values, `slides[${index}].values`),
    };
  });
}

function parseAssets(value) {
  if (!Array.isArray(value) || value.length > 16) fail("INVALID_RUNNER_REQUEST", "assets is invalid");
  return value.map((asset, index) => {
    const item = object(asset, `assets[${index}]`);
    exactKeys(item, ["assetId", "mediaType", "sha256", "bytesBase64"], `assets[${index}]`);
    if (item.mediaType !== "image/png" || !/^[0-9a-f]{64}$/.test(item.sha256)) {
      fail("INVALID_RUNNER_REQUEST", `assets[${index}] metadata is invalid`);
    }
    return {
      assetId: requiredString(item.assetId, `assets[${index}].assetId`),
      mediaType: "image/png",
      sha256: item.sha256,
      bytesBase64: requiredBinaryString(item.bytesBase64, `assets[${index}].bytesBase64`),
    };
  });
}

async function execute(request) {
  const input = object(request, "request");
  exactKeys(input, ["schemaVersion", "operation", "inputPath", "outputPath", "patch", "masterContract", "slides", "deleteMaster", "assets"], "request");
  if (input.schemaVersion !== "authored-vnext-ooxml-product-runner/v1") {
    fail("UNSUPPORTED_SCHEMA_VERSION", "unsupported runner schemaVersion");
  }
  if (!OPERATIONS.has(input.operation)) fail("UNSUPPORTED_OPERATION", "unsupported runner operation");
  const port = createOoxmlMasterCompilerPort();

  if (input.operation === "INSPECT") {
    const imported = port.importPackage(await readFile(absolutePath(input.inputPath, "inputPath")));
    const readBack = port.readBack(imported.document);
    let masterInventory = null;
    try {
      masterInventory = port.inventoryMasters(imported.document);
    } catch (error) {
      masterInventory = {
        version: "ooxml-master-layout-inventory/v1",
        packageDigest: readBack.package.packageDigest,
        relationshipDigest: readBack.package.relationshipDigest,
        masters: [],
        layouts: [],
        structuralRelationships: [],
        issueCodes: [error instanceof Error ? error.message : "MASTER_INVENTORY_FAILED"],
      };
    }
    return {
      inspection: readBack.package,
      originalPackage: imported.originalPackage,
      preservation: readBack.preservation,
      masterInventory,
    };
  }

  const outputPath = absolutePath(input.outputPath, "outputPath");
  if (input.operation === "PRESERVE_SAVE") {
    const imported = port.importPackage(await readFile(absolutePath(input.inputPath, "inputPath")));
    let changedParts = [];
    if (input.patch !== undefined) {
      const mutation = port.targetedTextOperation(
        imported.document,
        parsePatch(input.patch),
        port.readBack(imported.document).precondition,
      );
      changedParts = [...mutation.changedParts];
    }
    const save = await port.saveBuffered(
      imported.document,
      outputPath,
      port.readBack(imported.document).precondition,
      null,
    );
    return detachSave(save, changedParts);
  }

  const contract = normalizeDeckMasterContract(object(input.masterContract, "masterContract"));
  const slides = parseSlides(input.slides ?? []);
  const assets = parseAssets(input.assets ?? []);
  const created = port.createGenerated(contract, assets);
  for (const slide of slides) {
    port.materializeSlide(created.document, slide, port.readBack(created.document).precondition);
  }
  if (input.deleteMaster === true) {
    if (slides.length) fail("MASTER_IN_USE", "a master with materialized slides cannot be deleted");
    port.deleteMaster(created.document, port.readBack(created.document).precondition);
  } else if (input.deleteMaster !== undefined && input.deleteMaster !== false) {
    fail("INVALID_RUNNER_REQUEST", "deleteMaster is invalid");
  }
  const save = await port.saveBuffered(
    created.document,
    outputPath,
    port.readBack(created.document).precondition,
    null,
  );
  return detachSave(save, save.readBack.preservation.parts
    .filter(({ status }) => status === "GENERATED")
    .map(({ name }) => name));
}

async function main(argv) {
  if (argv.length !== 1) fail("USAGE", "product-runner.mjs requires one JSON request path");
  const requestPath = absolutePath(argv[0], "requestPath");
  const request = JSON.parse(await readFile(requestPath, "utf8"));
  return execute(request);
}

try {
  const receipt = await main(process.argv.slice(2));
  process.stdout.write(`${JSON.stringify({ ok: true, receipt })}\n`);
} catch (error) {
  process.stdout.write(`${JSON.stringify({
    ok: false,
    error: {
      code: typeof error?.code === "string" ? error.code : "OOXML_RUNNER_FAILED",
      message: error instanceof Error ? error.message : "unknown OOXML runner failure",
    },
  })}\n`);
  process.exitCode = 1;
}
