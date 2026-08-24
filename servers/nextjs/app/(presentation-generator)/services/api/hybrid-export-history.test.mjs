import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

import { transform } from "esbuild";

const directory = path.dirname(fileURLToPath(import.meta.url));
const sourcePath = path.join(directory, "hybrid-export-history.ts");
const digest = (character) => character.repeat(64);

function dataModule(source) {
  return `data:text/javascript;base64,${Buffer.from(source).toString("base64")}`;
}

async function loadHistoryClient() {
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

function history(overrides = {}) {
  return {
    schemaVersion: "presenton.authored-hybrid-export-history/v1",
    retention: {},
    attempts: [{
      exportAttemptId: "attempt-1",
      state: "succeeded",
      resolution: "active",
      presentationRevision: 4,
      format: "pptx",
      evidence: {
        status: "complete",
        attemptId: "attempt-1",
        presentationRevision: 4,
        pptxSha256: digest("a"),
        slidePngSha256: [digest("b")],
        visualParity: "not-verified",
        expiresAt: "2026-08-24T00:00:00Z",
      },
    }],
    ...overrides,
  };
}

test("Hybrid history refuses cross-attempt and stale complete evidence", async () => {
  const { normalizeHybridExportHistory } = await loadHistoryClient();
  assert.throws(
    () => normalizeHybridExportHistory(history({
      attempts: [{ ...history().attempts[0], evidence: { ...history().attempts[0].evidence, attemptId: "attempt-other" } }],
    })),
    /mismatched evidence\.attemptId/,
  );
  assert.throws(
    () => normalizeHybridExportHistory(history({
      attempts: [{ ...history().attempts[0], evidence: { ...history().attempts[0].evidence, presentationRevision: 5 } }],
    })),
    /mismatched evidence\.presentationRevision/,
  );
  assert.throws(
    () => normalizeHybridExportHistory(history({
      attempts: [{ ...history().attempts[0], evidence: { ...history().attempts[0].evidence, expiresAt: "2020-01-01T00:00:00Z" } }],
    })),
    /stale complete evidence/,
  );
  assert.throws(
    () => normalizeHybridExportHistory(history({
      attempts: [{ ...history().attempts[0], evidence: { ...history().attempts[0].evidence, expiresAt: "not-a-date" } }],
    })),
    /invalid evidence\.expiresAt/,
  );
});
