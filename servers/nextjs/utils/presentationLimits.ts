export const MAX_NUMBER_OF_SLIDES = 200;

export function clampSlideCountValue(value: string): string {
  const digitsOnly = value.replace(/\D+/g, "");
  if (!digitsOnly) return "";
  const normalized = digitsOnly.replace(/^0+/, "");
  if (!normalized) return "";
  return String(Math.min(Number(normalized), MAX_NUMBER_OF_SLIDES));
}

