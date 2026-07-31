export interface TemplateV2ConflictSnapshot {
  templateId: string;
  expectedRevision: number;
  currentRevision: number;
  baseLayouts?: unknown;
  layouts: unknown;
}

export interface TemplateV2ConflictRecoveryBundle {
  kind: "presenton.template-v2-studio-conflict";
  schema_version: 1;
  template_id: string;
  expected_revision: number;
  current_revision: number;
  captured_at: string;
  base_layouts?: unknown;
  layouts: unknown;
}

function revision(value: number, name: string): number {
  if (!Number.isSafeInteger(value) || value < 1) {
    throw new Error(`${name} must be a positive integer`);
  }
  return value;
}

export function createTemplateV2ConflictRecoveryBundle(
  snapshot: TemplateV2ConflictSnapshot,
  capturedAt = new Date()
): TemplateV2ConflictRecoveryBundle {
  if (!snapshot.templateId.trim()) {
    throw new Error("templateId must not be empty");
  }
  if (Number.isNaN(capturedAt.getTime())) {
    throw new Error("capturedAt must be valid");
  }

  return {
    kind: "presenton.template-v2-studio-conflict",
    schema_version: 1,
    template_id: snapshot.templateId,
    expected_revision: revision(
      snapshot.expectedRevision,
      "expectedRevision"
    ),
    current_revision: revision(snapshot.currentRevision, "currentRevision"),
    captured_at: capturedAt.toISOString(),
    ...(snapshot.baseLayouts === undefined
      ? {}
      : { base_layouts: structuredClone(snapshot.baseLayouts) }),
    layouts: structuredClone(snapshot.layouts),
  };
}

export function templateV2ConflictRecoveryFilename(
  templateId: string,
  capturedAt = new Date()
): string {
  const safeTemplateId =
    templateId.trim().replace(/[^a-zA-Z0-9_-]+/g, "-") || "template";
  const timestamp = capturedAt
    .toISOString()
    .replace(/\.\d{3}Z$/, "Z")
    .replace(/[:]/g, "-");
  return `${safeTemplateId}-conflict-${timestamp}.json`;
}

export function serializeTemplateV2ConflictRecoveryBundle(
  snapshot: TemplateV2ConflictSnapshot,
  capturedAt = new Date()
): string {
  return `${JSON.stringify(
    createTemplateV2ConflictRecoveryBundle(snapshot, capturedAt),
    null,
    2
  )}\n`;
}
