export interface NativeImageCrop { readonly left: number; readonly top: number; readonly right: number; readonly bottom: number }
function axis(token: string | undefined, horizontal: boolean): number | null {
  const t = (token ?? "center").toLowerCase();
  if (t === "center") return .5;
  if (horizontal && t === "left" || !horizontal && t === "top") return 0;
  if (horizontal && t === "right" || !horizontal && t === "bottom") return 1;
  const match = /^(\d+(?:\.\d+)?)%$/.exec(t); const n = match ? Number(match[1]) / 100 : NaN;
  return Number.isFinite(n) && n >= 0 && n <= 1 ? n : null;
}
export function parseNativeObjectPosition(value: string): { x: number; y: number } | null {
  const tokens = value.trim().split(/\s+/).filter(Boolean); if (tokens.length < 1 || tokens.length > 2) return null;
  const x = axis(tokens[0], true); const y = axis(tokens[1], false); return x === null || y === null ? null : { x, y };
}
export function nativeCoverCrop(sourceWidth: number, sourceHeight: number, boxWidth: number, boxHeight: number, position: string): NativeImageCrop | null {
  const anchor = parseNativeObjectPosition(position); if (!anchor || ![sourceWidth, sourceHeight, boxWidth, boxHeight].every((v) => Number.isFinite(v) && v > 0)) return null;
  const sourceRatio = sourceWidth / sourceHeight; const boxRatio = boxWidth / boxHeight;
  if (Math.abs(sourceRatio - boxRatio) < 1e-9) return { left: 0, top: 0, right: 0, bottom: 0 };
  if (sourceRatio > boxRatio) { const visible = boxRatio / sourceRatio; const hidden = 1 - visible; return { left: hidden * anchor.x, right: hidden * (1 - anchor.x), top: 0, bottom: 0 }; }
  const visible = sourceRatio / boxRatio; const hidden = 1 - visible; return { left: 0, right: 0, top: hidden * anchor.y, bottom: hidden * (1 - anchor.y) };
}
export function nativeCropSourceRectXml(crop: NativeImageCrop): string { const p = (v: number) => Math.max(0, Math.min(100000, Math.round(v * 100000))); return `<a:srcRect l="${p(crop.left)}" t="${p(crop.top)}" r="${p(crop.right)}" b="${p(crop.bottom)}"/>`; }
