import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

const directory = path.dirname(fileURLToPath(import.meta.url));

test("raw-path document preview is an explicit disabled-by-default legacy facade", async () => {
  const source = await readFile(
    path.join(directory, "DocumentPreviewPage.tsx"),
    "utf8",
  );
  assert.match(
    source,
    /NEXT_PUBLIC_ENABLE_LEGACY_DOCUMENT_PREVIEW === "true"/,
  );
  assert.match(source, /if \(!LEGACY_DOCUMENT_PREVIEW_ENABLED\) return \[\]/);
  assert.match(
    source,
    /if \(!LEGACY_DOCUMENT_PREVIEW_ENABLED\) \{[\s\S]*?router\.replace\("\/"\);[\s\S]*?return;/,
  );
  assert.match(source, /file_paths: documentPaths/);
});
