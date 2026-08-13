export function slideNumbers(indices?: number[]) {
  if (!indices?.length) return "";
  return indices.map((index) => index + 1).join(", ");
}

export function formatQualityDuration(milliseconds?: number) {
  if (milliseconds === undefined) return null;
  return milliseconds >= 1_000
    ? `${(milliseconds / 1_000).toFixed(1)}초`
    : `${Math.round(milliseconds)}ms`;
}
