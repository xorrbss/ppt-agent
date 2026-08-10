export type TemplateV2JsonRecord = Record<string, unknown>;

export function isTemplateV2JsonRecord(
  value: unknown
): value is TemplateV2JsonRecord {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

export function cloneTemplateV2Json<T>(value: T): T {
  return structuredClone(value);
}

export function templateV2SourceChildPath(
  parent: string,
  field: string,
  index?: number
): string {
  const fieldPath = parent ? `${parent}.${field}` : field;
  return index === undefined ? fieldPath : `${fieldPath}.${index}`;
}

export function templateV2JsonEqual(left: unknown, right: unknown): boolean {
  if (Object.is(left, right)) return true;
  if (Array.isArray(left) || Array.isArray(right)) {
    return (
      Array.isArray(left) &&
      Array.isArray(right) &&
      left.length === right.length &&
      left.every((value, index) => templateV2JsonEqual(value, right[index]))
    );
  }
  if (!isTemplateV2JsonRecord(left) || !isTemplateV2JsonRecord(right)) {
    return false;
  }
  const leftKeys = Object.keys(left).sort();
  const rightKeys = Object.keys(right).sort();
  return (
    leftKeys.length === rightKeys.length &&
    leftKeys.every(
      (key, index) =>
        key === rightKeys[index] && templateV2JsonEqual(left[key], right[key])
    )
  );
}
