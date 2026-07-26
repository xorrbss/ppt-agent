import assert from "node:assert/strict";
import test from "node:test";

import {
  createTemplateV2StudioJournalEntry,
  parseTemplateV2StudioJournalEntry,
  readTemplateV2StudioJournal,
  removeTemplateV2StudioJournal,
  templateV2LayoutsEqual,
  templateV2StudioJournalKey,
  writeTemplateV2StudioJournal,
  type TemplateV2StudioJournalStorage,
} from "./template-v2-studio-journal.ts";

function memoryStorage(): TemplateV2StudioJournalStorage & {
  entries: Map<string, string>;
} {
  const entries = new Map<string, string>();
  return {
    entries,
    getItem: (key) => entries.get(key) ?? null,
    setItem: (key, value) => entries.set(key, value),
    removeItem: (key) => {
      entries.delete(key);
    },
  };
}

test("journal entries round-trip under a template-scoped portable key", () => {
  const storage = memoryStorage();
  const entry = createTemplateV2StudioJournalEntry({
    templateId: "brand/template",
    baseRevision: 7,
    capturedAt: new Date("2026-07-26T05:06:07Z"),
    baseLayouts: { layouts: [{ id: "title", elements: [] }] },
    layouts: { layouts: [{ id: "title" }] },
  });
  writeTemplateV2StudioJournal(storage, entry);

  assert.equal(
    templateV2StudioJournalKey("brand/template"),
    "presenton.template-v2-studio.draft.v1:brand%2Ftemplate"
  );
  assert.deepEqual(readTemplateV2StudioJournal(storage, "brand/template"), entry);
  removeTemplateV2StudioJournal(storage, "brand/template");
  assert.equal(readTemplateV2StudioJournal(storage, "brand/template"), null);
});

test("invalid, cross-template, and future-schema journals fail closed", () => {
  assert.equal(parseTemplateV2StudioJournalEntry("{broken"), null);
  assert.equal(
    parseTemplateV2StudioJournalEntry(
      JSON.stringify({
        kind: "presenton.template-v2-studio-draft",
        schemaVersion: 2,
        templateId: "alpha",
        baseRevision: 1,
        capturedAt: "2026-07-26T00:00:00Z",
        baseLayouts: {},
        layouts: {},
      })
    ),
    null
  );
  const valid = createTemplateV2StudioJournalEntry({
    templateId: "alpha",
    baseRevision: 1,
    baseLayouts: {},
    layouts: {},
  });
  assert.equal(
    parseTemplateV2StudioJournalEntry(JSON.stringify(valid), "beta"),
    null
  );
});

test("read removes a corrupt scoped journal so it cannot block future recovery", () => {
  const storage = memoryStorage();
  const key = templateV2StudioJournalKey("alpha");
  storage.entries.set(key, "null");
  assert.equal(readTemplateV2StudioJournal(storage, "alpha"), null);
  assert.equal(storage.entries.has(key), false);
});

test("layout equality is structural and order-sensitive", () => {
  assert.equal(templateV2LayoutsEqual({ layouts: [] }, { layouts: [] }), true);
  assert.equal(
    templateV2LayoutsEqual({ layouts: [{ id: "a" }] }, { layouts: [{ id: "b" }] }),
    false
  );
});
