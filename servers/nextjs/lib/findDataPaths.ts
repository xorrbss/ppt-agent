// Path resolution for the legacy string-match text binding (TiptapTextReplacer),
// used only for custom DB templates now (built-ins bind explicitly via
// <EditableText>). Pure + testable, kept out of the component.
//
// The old findDataPath returned only the FIRST content path whose string equalled
// the edited text, so two fields with the same text both bound to the first — the
// second duplicate edited the wrong field. collectMatchingPaths returns EVERY
// matching path in a stable traversal order, and the caller picks by DOM
// occurrence, so the Nth duplicate on screen binds to the Nth matching field.

/** All content paths whose string value equals `targetText`, in traversal order. */
export function collectMatchingPaths(
  data: any,
  targetText: string,
  path = ""
): string[] {
  if (!data || typeof data !== "object") return [];
  const target = targetText.trim();
  const out: string[] = [];
  for (const [key, value] of Object.entries(data)) {
    const currentPath = path ? `${path}.${key}` : key;
    if (typeof value === "string") {
      if (value.trim() === target) out.push(currentPath);
    } else if (Array.isArray(value)) {
      for (let i = 0; i < value.length; i++) {
        out.push(
          ...collectMatchingPaths(value[i], targetText, `${currentPath}[${i}]`)
        );
      }
    } else if (value !== null && typeof value === "object") {
      out.push(...collectMatchingPaths(value, targetText, currentPath));
    }
  }
  return out;
}
