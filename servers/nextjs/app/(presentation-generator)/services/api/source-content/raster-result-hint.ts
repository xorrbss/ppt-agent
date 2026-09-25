const KEY = "presenton:raster-original-preserve-result:v1";

export function rememberRasterOriginalPreserveResult(presentationId: string): void {
  try { window.sessionStorage.setItem(`${KEY}:${presentationId}`, String(Date.now())); } catch { /* The generation flow does not depend on this hint. */ }
}

export function clearRasterOriginalPreserveResult(presentationId: string): void {
  try { window.sessionStorage.removeItem(`${KEY}:${presentationId}`); } catch { /* Informational hint only. */ }
}

export function consumeRasterOriginalPreserveResult(presentationId: string): boolean {
  try {
    const key = `${KEY}:${presentationId}`;
    const writtenAt = Number(window.sessionStorage.getItem(key));
    window.sessionStorage.removeItem(key);
    return Number.isFinite(writtenAt) && writtenAt > 0 && Date.now() - writtenAt < 2 * 60 * 60 * 1000;
  } catch { return false; }
}
