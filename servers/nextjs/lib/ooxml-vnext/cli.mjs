#!/usr/bin/env node
import { readFile } from "node:fs/promises";
import path from "node:path";
import process from "node:process";

import { createOoxmlMasterCompilerPort } from "./adapter.ts";
import { DEMO_DECK_MASTER_CONTRACT } from "./fixtures/demo-contract.ts";

function usage() {
  console.error(
    "Usage:\n  cli.mjs inspect <input.pptx>\n  cli.mjs noop <input.pptx> <output.pptx>\n  cli.mjs generate-demo <output.pptx> [slide-count]",
  );
}

function parseSlideCount(value) {
  const count = value === undefined ? 1 : Number(value);
  if (!Number.isInteger(count) || count < 1 || count > 90) {
    throw new Error("slide-count must be an integer from 1 to 90");
  }
  return count;
}

async function main(argv) {
  const [command, first, second] = argv;
  if (!command || !first) {
    usage();
    process.exitCode = 2;
    return;
  }
  const port = createOoxmlMasterCompilerPort();
  if (command === "inspect") {
    console.log(JSON.stringify(port.inspect(await readFile(path.resolve(first))), null, 2));
    return;
  }
  if (command === "noop") {
    if (!second) throw new Error("noop requires an output path");
    const imported = port.importPackage(await readFile(path.resolve(first)));
    const receipt = await port.saveBuffered(
      imported.document,
      path.resolve(second),
      port.readBack(imported.document).precondition,
      null,
    );
    console.log(JSON.stringify(receipt, null, 2));
    return;
  }
  if (command === "generate-demo") {
    const count = parseSlideCount(second);
    const created = port.createGenerated(DEMO_DECK_MASTER_CONTRACT);
    for (let index = 0; index < count; index += 1) {
      const readBack = port.readBack(created.document);
      port.materializeSlide(
        created.document,
        {
          slideId: `slide:demo-${index + 1}`,
          layoutId: index % 2 === 0 ? "layout:standard" : "layout:long-title",
          values:
            index % 2 === 0
              ? {
                  "standard-title": `OOXML vNext demo ${index + 1}`,
                  "standard-body": "Generated master, stable placeholders, buffered save",
                }
              : {
                  "long-title": `Long-title layout validates deterministic semantic metadata (${index + 1})`,
                  "long-body": "No GenOffice mutation or streaming save path is used.",
                },
        },
        readBack.precondition,
      );
    }
    const receipt = await port.saveBuffered(
      created.document,
      path.resolve(first),
      port.readBack(created.document).precondition,
      null,
    );
    console.log(JSON.stringify(receipt, null, 2));
    return;
  }
  usage();
  process.exitCode = 2;
}

main(process.argv.slice(2)).catch((error) => {
  console.error(error instanceof Error ? error.message : String(error));
  process.exitCode = 1;
});
