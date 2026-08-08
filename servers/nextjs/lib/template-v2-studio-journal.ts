import { isJsonRecord, type JsonRecord } from "./template-v2-studio.ts";

export const TEMPLATE_V2_STUDIO_JOURNAL_SCHEMA_VERSION = 1;
const JOURNAL_KEY_PREFIX = "presenton.template-v2-studio.draft.v1:";

export interface TemplateV2StudioJournalEntry {
  kind: "presenton.template-v2-studio-draft";
  schemaVersion: 1;
  templateId: string;
  baseRevision: number;
  capturedAt: string;
  baseLayouts: JsonRecord;
  layouts: JsonRecord;
}

export interface TemplateV2StudioJournalStorage {
  getItem(key: string): string | null;
  setItem(key: string, value: string): void;
  removeItem(key: string): void;
}

export function templateV2StudioJournalKey(templateId: string): string {
  const normalized = templateId.trim();
  if (!normalized) throw new Error("templateId must not be empty");
  return `${JOURNAL_KEY_PREFIX}${encodeURIComponent(normalized)}`;
}

export function createTemplateV2StudioJournalEntry({
  templateId,
  baseRevision,
  layouts,
  baseLayouts,
  capturedAt = new Date(),
}: {
  templateId: string;
  baseRevision: number;
  layouts: JsonRecord;
  baseLayouts: JsonRecord;
  capturedAt?: Date;
}): TemplateV2StudioJournalEntry {
  const normalized = templateId.trim();
  if (!normalized) throw new Error("templateId must not be empty");
  if (!Number.isSafeInteger(baseRevision) || baseRevision < 1) {
    throw new Error("baseRevision must be a positive integer");
  }
  if (Number.isNaN(capturedAt.getTime())) {
    throw new Error("capturedAt must be valid");
  }
  return {
    kind: "presenton.template-v2-studio-draft",
    schemaVersion: TEMPLATE_V2_STUDIO_JOURNAL_SCHEMA_VERSION,
    templateId: normalized,
    baseRevision,
    capturedAt: capturedAt.toISOString(),
    baseLayouts: structuredClone(baseLayouts),
    layouts: structuredClone(layouts),
  };
}

export function parseTemplateV2StudioJournalEntry(
  serialized: string,
  expectedTemplateId?: string
): TemplateV2StudioJournalEntry | null {
  let parsed: unknown;
  try {
    parsed = JSON.parse(serialized);
  } catch {
    return null;
  }
  if (
    !isJsonRecord(parsed) ||
    parsed.kind !== "presenton.template-v2-studio-draft" ||
    parsed.schemaVersion !== TEMPLATE_V2_STUDIO_JOURNAL_SCHEMA_VERSION ||
    typeof parsed.templateId !== "string" ||
    !parsed.templateId.trim() ||
    !Number.isSafeInteger(parsed.baseRevision) ||
    (parsed.baseRevision as number) < 1 ||
    typeof parsed.capturedAt !== "string" ||
    Number.isNaN(Date.parse(parsed.capturedAt)) ||
    !isJsonRecord(parsed.baseLayouts) ||
    !isJsonRecord(parsed.layouts)
  ) {
    return null;
  }
  if (
    expectedTemplateId !== undefined &&
    parsed.templateId !== expectedTemplateId
  ) {
    return null;
  }
  return {
    kind: "presenton.template-v2-studio-draft",
    schemaVersion: TEMPLATE_V2_STUDIO_JOURNAL_SCHEMA_VERSION,
    templateId: parsed.templateId,
    baseRevision: parsed.baseRevision as number,
    capturedAt: new Date(parsed.capturedAt).toISOString(),
    baseLayouts: structuredClone(parsed.baseLayouts),
    layouts: structuredClone(parsed.layouts),
  };
}

export function readTemplateV2StudioJournal(
  storage: TemplateV2StudioJournalStorage,
  templateId: string
): TemplateV2StudioJournalEntry | null {
  const key = templateV2StudioJournalKey(templateId);
  const serialized = storage.getItem(key);
  if (serialized === null) return null;
  const entry = parseTemplateV2StudioJournalEntry(serialized, templateId);
  if (!entry) storage.removeItem(key);
  return entry;
}

export function writeTemplateV2StudioJournal(
  storage: TemplateV2StudioJournalStorage,
  entry: TemplateV2StudioJournalEntry
): void {
  storage.setItem(
    templateV2StudioJournalKey(entry.templateId),
    JSON.stringify(entry)
  );
}

export function removeTemplateV2StudioJournal(
  storage: TemplateV2StudioJournalStorage,
  templateId: string
): void {
  storage.removeItem(templateV2StudioJournalKey(templateId));
}

export function templateV2LayoutsEqual(
  left: JsonRecord,
  right: JsonRecord
): boolean {
  return JSON.stringify(left) === JSON.stringify(right);
}
