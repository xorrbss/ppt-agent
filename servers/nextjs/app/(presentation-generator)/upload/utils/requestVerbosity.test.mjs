import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";
import { transform } from "esbuild";

const testDirectory = path.dirname(fileURLToPath(import.meta.url));

async function loadModule() {
  const source = await readFile(path.join(testDirectory, "requestVerbosity.ts"), "utf8");
  const { code } = await transform(source, { loader: "ts", format: "esm" });
  return import(`data:text/javascript;base64,${Buffer.from(code).toString("base64")}`);
}

const redesign = { svgTemplatePin: { id: "t" }, svgTemplateMode: "template-redesign" };

test("template redesign leaves verbosity unset until the user picks one", async () => {
  const { requestVerbosity } = await loadModule();
  assert.equal(requestVerbosity({ verbosity: "standard", ...redesign }), undefined);
  assert.equal(
    requestVerbosity({ verbosity: "concise", verbosityTouched: true, ...redesign }),
    "concise",
  );
});

test("other decks keep the configured verbosity", async () => {
  const { requestVerbosity } = await loadModule();
  assert.equal(requestVerbosity({ verbosity: "standard" }), "standard");
  assert.equal(
    requestVerbosity({ verbosity: "standard", svgTemplatePin: { id: "t" }, svgTemplateMode: "free-design" }),
    "standard",
  );
  assert.equal(requestVerbosity(undefined), undefined);
});
