import assert from "node:assert/strict";
import test from "node:test";

import {
  backendErrorMessageId,
  backendMessageId,
  pickBackendMessage,
  resolveBackendError,
  resolveBackendMessage,
  type BackendTranslator,
} from "./backend-messages.ts";

function translator(catalog: Record<string, string>): BackendTranslator {
  return {
    has: (id) => id in catalog,
    format: (id, params = {}) =>
      catalog[id].replace(/\{(\w+)\}/gu, (_match, name: string) => {
        if (!(name in params)) throw new Error(`missing ${name}`);
        return String(params[name]);
      }),
  };
}

test("dotted backend keys map to underscore catalog ids", () => {
  assert.equal(backendMessageId("remake.page.ready"), "remake_page_ready");
  assert.equal(
    backendMessageId("workspace.operation.slide.regenerate.preview"),
    "workspace_operation_slide_regenerate_preview",
  );
  assert.equal(
    backendErrorMessageId("svg_source_export_format_unsupported"),
    "error_svg_source_export_format_unsupported",
  );
});

test("pickBackendMessage reads the field triple and rejects malformed keys", () => {
  assert.deepEqual(
    pickBackendMessage({
      message: "Queued a new version of page 3.",
      message_key: "remake.page.queued",
      message_params: { page: 3 },
    }),
    {
      text: "Queued a new version of page 3.",
      key: "remake.page.queued",
      params: { page: 3 },
    },
  );
  assert.deepEqual(
    pickBackendMessage({ label: "Autosave", label_key: "Not A Key" }, "label"),
    { text: "Autosave", key: undefined, params: undefined },
  );
  assert.deepEqual(pickBackendMessage(null), {});
});

test("a known key renders in the catalog, an unknown key keeps the backend text", () => {
  const t = translator({ remake_page_ready: "{page}페이지 준비 완료" });
  assert.equal(
    resolveBackendMessage(t, {
      key: "remake.page.ready",
      params: { page: 3 },
      text: "The new version preview of page 3 is ready.",
    }),
    "3페이지 준비 완료",
  );
  assert.equal(
    resolveBackendMessage(t, {
      key: "remake.page.failed",
      text: "The new page version could not be created.",
    }),
    "The new page version could not be created.",
  );
  assert.equal(resolveBackendMessage(t, undefined), undefined);
});

test("a catalog entry whose params drifted falls back to the backend text", () => {
  const t = translator({ remake_page_ready: "{page}페이지 {extra}" });
  assert.equal(
    resolveBackendMessage(t, {
      key: "remake.page.ready",
      params: { page: 3 },
      text: "fallback",
    }),
    "fallback",
  );
});

test("error codes resolve through the error_ prefix with the safe message as fallback", () => {
  const t = translator({ error_revision_conflict: "충돌" });
  assert.equal(resolveBackendError(t, "revision_conflict", "Conflict"), "충돌");
  assert.equal(resolveBackendError(t, "unknown_code", "Fallback"), "Fallback");
  assert.equal(resolveBackendError(t, undefined, undefined), undefined);
});
