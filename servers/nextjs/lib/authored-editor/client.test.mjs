import assert from "node:assert/strict";
import test from "node:test";

import { AuthoredCanonicalConflict, AuthoredEditorApi } from "./client.ts";

test("authored editor sends revision-pinned allowlist patch operations", async () => {
  const originalFetch = globalThis.fetch;
  let request;
  globalThis.fetch = async (url, init) => {
    request = { url: String(url), init };
    return new Response(JSON.stringify({ presentationId: "deck-1", revision: 2, sourceDigest: "sha256:next", history: { position: 1, length: 1, canUndo: true, canRedo: false }, slides: [] }), { status: 200 });
  };
  try {
    const result = await AuthoredEditorApi.applyPatches("deck-1", { revision: 1, sourceDigest: "sha256:before" }, [{ slideId: "slide-1", qid: "title", op: "set_text", value: "변경" }]);
    assert.equal(result.revision, 2);
    assert.match(request.url, /presentation\/deck-1\/canonical\/patches$/);
    assert.deepEqual(JSON.parse(request.init.body), {
      expectedRevision: 1,
      expectedDigest: "sha256:before",
      rebaseOnConflict: false,
      patches: [{ slideId: "slide-1", actions: [{ action: "setText", qid: "title", text: "변경" }] }],
    });
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test("authored editor exposes a canonical revision conflict for rebase", async () => {
  const originalFetch = globalThis.fetch;
  globalThis.fetch = async () => new Response(JSON.stringify({ code: "authored_canonical_revision_conflict", currentRevision: 3, currentDigest: "sha256:current" }), { status: 409 });
  try {
    await assert.rejects(
      () => AuthoredEditorApi.applyHistory("deck-1", { revision: 2, sourceDigest: "sha256:before" }, "undo"),
      (error) => error instanceof AuthoredCanonicalConflict && error.currentRevision === 3,
    );
  } finally {
    globalThis.fetch = originalFetch;
  }
});
