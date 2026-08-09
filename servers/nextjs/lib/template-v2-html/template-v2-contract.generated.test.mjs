import assert from "node:assert/strict";
import { mkdtemp, readFile, rm } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import test from "node:test";
import { fileURLToPath, pathToFileURL } from "node:url";

import { build } from "esbuild";

const projectRoot = path.resolve(
  path.dirname(fileURLToPath(import.meta.url)),
  "../..",
);
const contractPath = path.resolve(
  projectRoot,
  "../../contracts/template_v2/v1/schema.json",
);
const runtimeContractPath = path.resolve(
  projectRoot,
  "lib/template-v2-html/template-v2-contract.generated.mjs",
);

function discriminatorValues(schema, union) {
  return union.oneOf.map(({ $ref }) => {
    const name = $ref.replace("#/$defs/", "");
    return schema.$defs[name].properties.type.const;
  });
}

async function importContractBoundary(t) {
  const temporaryDirectory = await mkdtemp(
    path.join(os.tmpdir(), "template-v2-contract-"),
  );
  t.after(() => rm(temporaryDirectory, { force: true, recursive: true }));
  const outfile = path.join(temporaryDirectory, "contract-boundary.mjs");
  await build({
    absWorkingDir: projectRoot,
    bundle: true,
    format: "esm",
    outfile,
    platform: "node",
    stdin: {
      contents: [
        'export * from "./lib/template-v2-html/template-v2-contract.generated";',
        'export { rawChartType } from "./components/slide-editor/charts/chart-data";',
        'export { chartKindFromValue } from "./lib/template-v2-html/chart-kinds";',
        'export { isComponent } from "./lib/template-v2-html/element-types";',
      ].join("\n"),
      loader: "ts",
      resolveDir: projectRoot,
      sourcefile: "template-v2-contract-boundary.ts",
    },
    tsconfig: path.join(projectRoot, "tsconfig.json"),
  });
  return import(pathToFileURL(outfile).href);
}

test("generated runtime discriminators match the Pydantic JSON Schema", async (t) => {
  const schema = JSON.parse(await readFile(contractPath, "utf8"));
  const contract = await importContractBoundary(t);
  const runtimeContract = await import(pathToFileURL(runtimeContractPath).href);
  const elementUnion = schema.$defs.Component.properties.elements.items;
  const infographicUnion = schema.$defs.Infographic.properties.data;

  assert.deepEqual(
    [...contract.TEMPLATE_V2_CHART_TYPES],
    schema.$defs.ChartType.enum,
  );
  assert.deepEqual(
    [...contract.TEMPLATE_V2_DATA_LABEL_POSITIONS],
    schema.$defs.DataLabelPosition.enum,
  );
  assert.deepEqual(
    [...contract.TEMPLATE_V2_ELEMENT_TYPES],
    discriminatorValues(schema, elementUnion),
  );
  assert.deepEqual(
    [...contract.TEMPLATE_V2_INFOGRAPHIC_TYPES],
    discriminatorValues(schema, infographicUnion),
  );
  for (const name of [
    "TEMPLATE_V2_CHART_TYPES",
    "TEMPLATE_V2_DATA_LABEL_POSITIONS",
    "TEMPLATE_V2_ELEMENT_TYPES",
    "TEMPLATE_V2_INFOGRAPHIC_TYPES",
  ]) {
    assert.deepEqual(runtimeContract[name], contract[name], name);
    assert.equal(Object.isFrozen(runtimeContract[name]), true, name);
    assert.equal(Object.isFrozen(contract[name]), true, name);
  }
});

test("renderer keeps legacy aliases outside the generated canonical list", async (t) => {
  const contract = await importContractBoundary(t);

  for (const type of [
    ...contract.TEMPLATE_V2_ELEMENT_TYPES,
    "svg",
    "list-view",
    "grid-view",
  ]) {
    assert.equal(contract.isComponent({ type, elements: [] }), false, type);
  }
  assert.equal(contract.isComponent({ elements: [] }), true);
  assert.equal(contract.chartKindFromValue("horizontal-bar"), "horizontal_bar");
  assert.equal(contract.chartKindFromValue("doughnut"), "donut");
  assert.equal(contract.chartKindFromValue("unknown"), "bar");
  for (const chartType of contract.TEMPLATE_V2_CHART_TYPES) {
    assert.equal(contract.rawChartType(chartType), chartType);
  }
});
