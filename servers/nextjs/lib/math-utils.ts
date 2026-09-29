/** `value` bounded to [min, max]; the upper bound wins when the two cross. */
export function clamp(value: number, min: number, max: number): number {
  return Math.min(Math.max(value, min), max);
}

/** The value when it is a finite number, otherwise undefined (no coercion). */
export function finiteNumber(value: unknown): number | undefined {
  return typeof value === "number" && Number.isFinite(value) ? value : undefined;
}
