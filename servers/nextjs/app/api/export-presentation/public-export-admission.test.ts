import assert from "node:assert/strict";
import test from "node:test";

import {
  parsePublicExportAdmission,
  parsePublicExportReceipt,
  PublicExportAdmissionError,
} from "../../../lib/public-export-admission.ts";

const PRESENTATION_ID = "10000000-0000-4000-8000-000000000001";

test("public export admission creates one durable operation identity", () => {
  const first = parsePublicExportAdmission({
    id: PRESENTATION_ID,
    format: "pptx",
    expectedPresentationRevision: 7,
    pptxMode: "hybrid",
    fontEmbedding: true,
  });
  const duplicate = parsePublicExportAdmission({
    id: PRESENTATION_ID,
    format: "pptx",
    expectedPresentationRevision: 7,
    pptxMode: "hybrid",
    fontEmbedding: true,
  });

  assert.equal(first.commandType, "export.pptx");
  assert.equal(first.idempotencyKey, duplicate.idempotencyKey);
  assert.deepEqual(first.context, {
    pptx_mode: "hybrid",
    font_embedding: true,
  });
});

test("public export admission rejects client-owned identity, paths, and sessions", () => {
  for (const forbidden of [
    { ownerId: "client-owner" },
    { tenant_id: "client-tenant" },
    { cookie: "session=secret" },
    { outputPath: "C:/exports/result.pptx" },
  ]) {
    assert.throws(
      () =>
        parsePublicExportAdmission({
          id: PRESENTATION_ID,
          format: "pdf",
          expectedPresentationRevision: 1,
          ...forbidden,
        }),
      PublicExportAdmissionError,
    );
  }
});

test("public export receipt exposes operation state instead of a local file path", () => {
  assert.deepEqual(
    parsePublicExportReceipt({
      operation_id: "20000000-0000-4000-8000-000000000001",
      receipt_id: "30000000-0000-4000-8000-000000000001",
      status: "queued",
      duplicate: false,
      timeline: { path: "must-not-leak" },
    }),
    {
      operationId: "20000000-0000-4000-8000-000000000001",
      receiptId: "30000000-0000-4000-8000-000000000001",
      status: "queued",
      duplicate: false,
    },
  );
});
