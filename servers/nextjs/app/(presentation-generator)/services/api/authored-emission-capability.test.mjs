import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

import { transform } from "esbuild";

const directory = path.dirname(fileURLToPath(import.meta.url));
const sourcePath = path.join(directory, "authored-emission-capability.ts");

function dataModule(source) {
  return `data:text/javascript;base64,${Buffer.from(source).toString("base64")}`;
}

async function loadCapabilityClient() {
  const source = await readFile(sourcePath, "utf8");
  const headers = dataModule("export const getHeader = () => ({});\n");
  const api = dataModule("export const getApiUrl = (value) => value;\n");
  const compiled = await transform(
    source
      .replace('from "./header";', `from "${headers}";`)
      .replace('from "@/utils/api";', `from "${api}";`),
    { format: "esm", loader: "ts", target: "esnext" },
  );
  return import(dataModule(compiled.code));
}

const digest = (character) => `sha256:${character.repeat(64)}`;

function projection(overrides = {}) {
  return {
    schemaVersion: "workbench-capability-projection/v1",
    presentationId: "deck-1",
    presentationRevision: 7,
    canonicalMode: "bundleGraph",
    canonicalDigest: digest("a"),
    manifestDigest: digest("c"),
    degradedFidelityOnly: false,
    fidelity: { state: "ready", receiptDigest: digest("b") },
    hybrid: {
      text: { state: "ready", receiptDigest: digest("b") },
      image: { state: "shadow" },
      shape: { state: "blocked", reason: "serializer_unavailable" },
    },
    readiness: {
      authoredCreationReady: true,
      fidelityExportReady: true,
      fullNativeParityReady: false,
      independentVnextGenerationReady: false,
      promotionReady: false,
    },
    killSwitches: [],
    elements: [],
    projectionDigest: digest("d"),
    ...overrides,
  };
}

test("capability projection retains the immutable manifest binding", async () => {
  const { normalizeAuthoredEmissionCapabilityProjection } = await loadCapabilityClient();
  assert.deepEqual(normalizeAuthoredEmissionCapabilityProjection({ data: projection() }), {
    ...projection(),
    canonicalDigest: digest("a"),
    fidelity: { state: "ready", receiptDigest: digest("b"), reason: undefined },
    hybrid: {
      text: { state: "ready", receiptDigest: digest("b"), reason: undefined },
      image: { state: "shadow", receiptDigest: undefined, reason: undefined },
      shape: { state: "blocked", receiptDigest: undefined, reason: "serializer_unavailable" },
    },
  });
});

test("capability projection rejects a client-side readiness promotion", async () => {
  const { normalizeAuthoredEmissionCapabilityProjection } = await loadCapabilityClient();
  assert.throws(
    () =>
      normalizeAuthoredEmissionCapabilityProjection(
        projection({
          readiness: {
            ...projection().readiness,
            promotionReady: true,
          },
        }),
      ),
    /unsupported readiness promotion/,
  );
});
